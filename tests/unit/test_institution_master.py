from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.institutions.master import build_institution_master
from gisnet.institutions.types import InstitutionTypePolicy


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
