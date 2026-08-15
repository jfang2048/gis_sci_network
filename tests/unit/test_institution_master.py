from pathlib import Path
from typing import Any

import duckdb
import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gisnet.config import OpenAlexConfig
from gisnet.institutions import master as master_module
from gisnet.institutions.master import build_institution_master
from gisnet.institutions.types import InstitutionTypePolicy
from gisnet.openalex.cache import RawResponseCache
from gisnet.openalex.client import OpenAlexClient


def policy() -> InstitutionTypePolicy:
    return InstitutionTypePolicy.model_validate(
        {
            "policy_version": "test",
            "review_status": "provisional",
            "unknown_policy": {
                "analytical_scope": "unknown",
                "normalized_category": "unknown",
                "is_primary_research_scope": False,
                "reason": "unknown",
            },
            "types": {
                "education": {
                    "analytical_scope": "primary",
                    "normalized_category": "education",
                    "is_primary_research_scope": True,
                    "reason": "test",
                }
            },
        }
    )


def _openalex_record(institution_id: str, latitude: float) -> dict[str, object]:
    return {
        "id": f"https://openalex.org/{institution_id}",
        "ror": f"https://ror.org/0{institution_id.lower():0<8}",
        "display_name": f"Institution {institution_id}",
        "display_name_acronyms": [],
        "display_name_alternatives": [],
        "country_code": "DE",
        "type": "education",
        "lineage": [f"https://openalex.org/{institution_id}"],
        "geo": {"latitude": latitude, "longitude": 13.4, "country_code": "DE"},
        "associated_institutions": [],
        "updated_date": "2026-08-01T00:00:00",
    }


def test_builds_unique_master_and_audits_conflicts(tmp_path: Path) -> None:
    extracted = tmp_path / "extracted.parquet"
    rows = [
        {
            "institution_id": "I1",
            "ror_id": "https://ror.org/012345678",
            "display_name": "Alpha University",
            "country_code": "DE",
            "institution_type": "education",
            "lineage": ["I1"],
        },
        {
            "institution_id": "I1",
            "ror_id": "https://ror.org/012345678",
            "display_name": "Alpha Universität",
            "country_code": "DE",
            "institution_type": "education",
            "lineage": ["I1", "I9"],
        },
        {
            "institution_id": "I2",
            "ror_id": None,
            "display_name": "Unknown Country Lab",
            "country_code": None,
            "institution_type": None,
            "lineage": ["I2"],
        },
    ]
    pq.write_table(pa.Table.from_pylist(rows), extracted)
    summary = build_institution_master(
        extracted,
        policy(),
        master_path=tmp_path / "institutions.parquet",
        qa_path=tmp_path / "qa.parquet",
    )
    assert summary["institution_count"] == 2
    assert summary["metadata_qa_count"] == 2
    assert summary["lookup_requested_count"] == 2
    assert summary["coordinate_count"] == 0
    assert summary["missing_coordinate_count"] == 2
    connection = duckdb.connect()
    try:
        master = connection.execute(
            """
            SELECT institution_id, display_name, alternative_names, country_code,
                   normalized_category, is_primary_research_scope, lineage
            FROM read_parquet(?) ORDER BY institution_id
            """,
            [str(tmp_path / "institutions.parquet")],
        ).fetchall()
        qa = connection.execute(
            """
            SELECT institution_id, issue_fields, lookup_status
            FROM read_parquet(?) ORDER BY institution_id
            """,
            [str(tmp_path / "qa.parquet")],
        ).fetchall()
    finally:
        connection.close()
    assert master == [
        (
            "I1",
            "Alpha University",
            ["Alpha Universität"],
            "DE",
            "education",
            True,
            ["I1", "I9"],
        ),
        ("I2", "Unknown Country Lab", [], None, "unknown", False, ["I2"]),
    ]
    assert qa == [
        ("I1", ["conflicting_display_name"], "offline"),
        ("I2", ["missing_country_code", "missing_institution_type", "missing_ror"], "offline"),
    ]


def test_fetches_every_stable_id_and_reuses_records_from_prior_batches(tmp_path: Path) -> None:
    extracted = tmp_path / "extracted.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "institution_id": institution_id,
                    "ror_id": f"https://ror.org/0{institution_id.lower():0<8}",
                    "display_name": f"Institution {institution_id}",
                    "country_code": "DE",
                    "institution_type": "education",
                    "lineage": [institution_id],
                }
                for institution_id in ("I1", "I2")
            ]
        ),
        extracted,
    )
    cache = RawResponseCache(tmp_path / "cache")
    cached_parameters = {
        "filter": "openalex:I1|I999",
        "select": (
            "id,ror,display_name,display_name_acronyms,display_name_alternatives,country_code,"
            "type,lineage,geo,associated_institutions,updated_date"
        ),
        "per-page": 200,
    }
    cache.put(
        endpoint="/institutions",
        parameters=cached_parameters,
        data={"results": [_openalex_record("I1", 52.5)]},
        status_code=200,
        retrieved_at_utc="2026-08-01T00:00:00Z",
    )
    requested_filters: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_filters.append(request.url.params["filter"])
        return httpx.Response(
            200,
            json={"results": [_openalex_record("I2", 48.1)]},
            request=request,
        )

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.openalex.org"
    )
    client = OpenAlexClient(OpenAlexConfig(max_retries=0), api_key="", client=http_client)
    summary = build_institution_master(
        extracted,
        policy(),
        master_path=tmp_path / "institutions.parquet",
        qa_path=tmp_path / "qa.parquet",
        client=client,
        cache=cache,
        lookup_batch_size=50,
    )
    client.close()

    assert requested_filters == ["openalex:I2"]
    assert summary["lookup_requested_count"] == 2
    assert summary["lookup_cache_found_count"] == 1
    assert summary["lookup_network_target_count"] == 1
    assert summary["lookup_found_count"] == 2
    assert summary["coordinate_count"] == 2
    assert summary["missing_coordinate_count"] == 0
    assert summary["coordinate_source_counts"] == {"openalex": 2}
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """
            SELECT institution_id, latitude, longitude, coordinate_source, metadata_source
            FROM read_parquet(?) ORDER BY institution_id
            """,
            [str(tmp_path / "institutions.parquet")],
        ).fetchall()
    finally:
        connection.close()
    assert rows == [
        ("I1", 52.5, 13.4, "openalex", "openalex_lookup"),
        ("I2", 48.1, 13.4, "openalex", "openalex_lookup"),
    ]


def test_institution_artifacts_record_master_policy_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, str]] = []

    def capture(**kwargs: Any) -> None:
        captured.append(dict(kwargs["source_versions"]))

    monkeypatch.setattr(master_module, "write_json_artifact", capture)
    monkeypatch.setattr(master_module, "write_parquet_manifest", capture)
    project = tmp_path / "project.yml"
    project.write_text("project_version: test\n", encoding="utf-8")
    institution_types = tmp_path / "institution-types.yml"
    institution_types.write_text("policy_version: test\n", encoding="utf-8")
    master_module.write_institution_master_artifacts(
        {
            "logical_input_hash": "test",
            "ror_schema_versions": [],
            "outputs": {
                "institutions": str(tmp_path / "institutions.parquet"),
                "institution_metadata_qa": str(tmp_path / "qa.parquet"),
            },
        },
        summary_path=tmp_path / "summary.json",
        run_id="test-run",
        project_config_path=project,
        institution_type_path=institution_types,
        command="test",
    )
    assert len(captured) == 3
    assert all(
        versions["institution_master_policy"] == master_module._STAGE_VERSION
        for versions in captured
    )
