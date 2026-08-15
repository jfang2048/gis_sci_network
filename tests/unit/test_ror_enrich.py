import json
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gisnet.ror import enrich as enrich_module
from gisnet.ror.enrich import enrich_institutions_with_ror, normalize_ror_record


def _record(identifier: str = "03ysstz10") -> dict[str, object]:
    return {
        "id": f"https://ror.org/{identifier}",
        "admin": {"last_modified": {"date": "2025-09-22", "schema_version": "2.1"}},
        "names": [
            {"value": "Example Institute", "lang": "en", "types": ["ror_display", "label"]},
            {"value": "EI", "lang": "en", "types": ["acronym"]},
        ],
        "locations": [
            {
                "geonames_details": {
                    "country_code": "DE",
                    "country_name": "Germany",
                    "name": "Berlin",
                    "lat": 52.5,
                    "lng": 13.4,
                }
            }
        ],
        "relationships": [{"type": "parent", "id": "https://ror.org/058rymf81", "label": "Parent"}],
        "status": "active",
        "types": ["education", "funder"],
    }


def test_normalizer_extracts_v21_fields() -> None:
    value = normalize_ror_record(_record())
    assert value["ror_record_id"] == "03ysstz10"
    assert value["ror_display_name"] == "Example Institute"
    assert value["ror_acronyms"] == ["EI"]
    assert value["ror_country_code"] == "DE"
    assert value["ror_latitude"] == 52.5
    assert value["ror_longitude"] == 13.4
    assert value["ror_parent_ids"] == ["058rymf81"]
    assert value["ror_schema_version"] == "2.1"


def test_dump_and_cache_modes_share_normalized_schema(tmp_path: Path) -> None:
    institutions = tmp_path / "institutions.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "institution_id": "I1",
                    "ror_id": "https://ror.org/03ysstz10",
                    "display_name": "Example Institute",
                    "country_code": "DE",
                    "institution_type": "education",
                    "latitude": None,
                    "longitude": None,
                    "coordinate_source": None,
                },
                {
                    "institution_id": "I2",
                    "ror_id": None,
                    "display_name": "No ROR",
                    "country_code": "US",
                    "institution_type": "education",
                    "latitude": 40.0,
                    "longitude": -75.0,
                    "coordinate_source": "openalex",
                },
            ]
        ),
        institutions,
    )
    dump = tmp_path / "ror.json"
    dump.write_text(json.dumps([_record()]), encoding="utf-8")
    dump_output = tmp_path / "dump.parquet"
    dump_qa = tmp_path / "dump-qa.parquet"
    summary = enrich_institutions_with_ror(
        institutions,
        output_path=dump_output,
        qa_path=dump_qa,
        cache_directory=tmp_path / "cache",
        mode="dump",
        dump_path=dump,
        dump_version="test-v1",
    )
    assert summary["status_counts"] == {"enriched": 1, "missing_ror_id": 1}
    assert summary["coordinate_fallback_count"] == 1
    assert summary["resolved_coordinate_count"] == 2
    assert summary["missing_resolved_coordinate_count"] == 0
    assert summary["coordinate_source_counts"] == {"openalex": 1, "ror": 1}
    cache = tmp_path / "cache" / "03ysstz10.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps(_record()), encoding="utf-8")
    cache_output = tmp_path / "cache.parquet"
    cache_qa = tmp_path / "cache-qa.parquet"
    enrich_institutions_with_ror(
        institutions,
        output_path=cache_output,
        qa_path=cache_qa,
        cache_directory=cache.parent,
        mode="cache",
    )
    connection = duckdb.connect()
    try:
        dump_rows = connection.execute(
            "SELECT * FROM read_parquet(?) ORDER BY institution_id", [str(dump_output)]
        ).fetchall()
        cache_rows = connection.execute(
            "SELECT * FROM read_parquet(?) ORDER BY institution_id", [str(cache_output)]
        ).fetchall()
    finally:
        connection.close()
    assert dump_rows == cache_rows


def test_ror_coordinate_fallback_preserves_complete_higher_priority_pair(tmp_path: Path) -> None:
    institutions = tmp_path / "institutions.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "institution_id": "I1",
                    "ror_id": "https://ror.org/03ysstz10",
                    "display_name": "Example Institute",
                    "country_code": "DE",
                    "institution_type": "education",
                    "latitude": None,
                    "longitude": None,
                    "coordinate_source": None,
                },
                {
                    "institution_id": "I2",
                    "ror_id": "https://ror.org/03ysstz10",
                    "display_name": "Example Institute",
                    "country_code": "DE",
                    "institution_type": "education",
                    "latitude": 1.0,
                    "longitude": 2.0,
                    "coordinate_source": "openalex",
                },
                {
                    "institution_id": "I3",
                    "ror_id": "https://ror.org/03ysstz10",
                    "display_name": "Example Institute",
                    "country_code": "DE",
                    "institution_type": "education",
                    "latitude": 1.0,
                    "longitude": None,
                    "coordinate_source": "openalex",
                },
            ]
        ),
        institutions,
    )
    dump = tmp_path / "ror.json"
    dump.write_text(json.dumps([_record()]), encoding="utf-8")
    output = tmp_path / "output.parquet"
    summary = enrich_institutions_with_ror(
        institutions,
        output_path=output,
        qa_path=tmp_path / "qa.parquet",
        cache_directory=tmp_path / "cache",
        mode="dump",
        dump_path=dump,
        dump_version="test-v1",
    )
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """
            SELECT institution_id, latitude, longitude, coordinate_source,
                   ror_latitude, ror_longitude
            FROM read_parquet(?) ORDER BY institution_id
            """,
            [str(output)],
        ).fetchall()
    finally:
        connection.close()
    assert rows == [
        ("I1", 52.5, 13.4, "ror", 52.5, 13.4),
        ("I2", 1.0, 2.0, "openalex", 52.5, 13.4),
        ("I3", 52.5, 13.4, "ror", 52.5, 13.4),
    ]
    assert summary["ror_coordinate_count"] == 3
    assert summary["coordinate_fallback_count"] == 2
    assert summary["resolved_coordinate_count"] == 3
    assert summary["missing_resolved_coordinate_count"] == 0
    assert summary["partial_resolved_coordinate_count"] == 0
    assert summary["coordinate_source_counts"] == {"openalex": 1, "ror": 2}


def test_incomplete_ror_pair_is_not_promoted(tmp_path: Path) -> None:
    institutions = tmp_path / "institutions.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "institution_id": "I1",
                    "ror_id": "https://ror.org/03ysstz10",
                    "display_name": "Example Institute",
                    "country_code": "DE",
                    "institution_type": "education",
                    "latitude": None,
                    "longitude": None,
                    "coordinate_source": None,
                }
            ]
        ),
        institutions,
    )
    record = _record()
    record["locations"] = [
        {
            "geonames_details": {
                "country_code": "DE",
                "country_name": "Germany",
                "name": "Berlin",
                "lat": 52.5,
                "lng": None,
            }
        }
    ]
    dump = tmp_path / "ror.json"
    dump.write_text(json.dumps([record]), encoding="utf-8")
    output = tmp_path / "output.parquet"
    summary = enrich_institutions_with_ror(
        institutions,
        output_path=output,
        qa_path=tmp_path / "qa.parquet",
        cache_directory=tmp_path / "cache",
        mode="dump",
        dump_path=dump,
        dump_version="test-v1",
    )
    row = pq.read_table(output).to_pylist()[0]
    assert (row["latitude"], row["longitude"], row["coordinate_source"]) == (None, None, None)
    assert summary["ror_coordinate_count"] == 0
    assert summary["coordinate_fallback_count"] == 0
    assert summary["resolved_coordinate_count"] == 0
    assert summary["missing_resolved_coordinate_count"] == 1


def test_ror_artifacts_record_enrichment_policy_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, str]] = []

    def capture(**kwargs: Any) -> None:
        captured.append(dict(kwargs["source_versions"]))

    monkeypatch.setattr(enrich_module, "write_json_artifact", capture)
    monkeypatch.setattr(enrich_module, "write_parquet_manifest", capture)
    project = tmp_path / "project.yml"
    project.write_text("project_version: test\n", encoding="utf-8")
    enrich_module.write_ror_artifacts(
        {
            "logical_input_hash": "test",
            "ror_schema_versions": ["2.1"],
            "ror_dump_version": None,
            "outputs": {
                "institutions_ror": str(tmp_path / "institutions-ror.parquet"),
                "institution_ror_qa": str(tmp_path / "qa.parquet"),
            },
        },
        summary_path=tmp_path / "summary.json",
        run_id="test-run",
        project_config_path=project,
        command="test",
    )
    assert len(captured) == 3
    assert all(
        versions["institution_ror_policy"] == enrich_module._STAGE_VERSION for versions in captured
    )
