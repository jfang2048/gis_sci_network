from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.institutions.hierarchy import build_institution_hierarchy
from gisnet.institutions.overrides import InstitutionOverrideRegistry


def test_builds_comparable_views_and_uses_only_explicit_collapse(tmp_path: Path) -> None:
    institutions = tmp_path / "institutions.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "institution_id": "I1",
                    "ror_id": "https://ror.org/03ysstz10",
                    "lineage": ["I1", "I2"],
                    "parent_ids": ["I2"],
                    "ror_parent_ids": ["058rymf81"],
                },
                {
                    "institution_id": "I2",
                    "ror_id": "https://ror.org/058rymf81",
                    "lineage": ["I2"],
                    "parent_ids": [],
                    "ror_parent_ids": [],
                },
                {
                    "institution_id": "I3",
                    "ror_id": None,
                    "lineage": ["I3"],
                    "parent_ids": [],
                    "ror_parent_ids": [],
                },
            ]
        ),
        institutions,
    )
    overrides_path = tmp_path / "overrides.csv"
    overrides_path.write_text(
        "rule_id,action,source_institution_id,target_institution_id,country_code,reason,provenance\n"
        "collapse-i3,collapse,I3,I2,,Documented unit test,Fixture\n",
        encoding="utf-8",
    )
    hierarchy = tmp_path / "hierarchy.parquet"
    audit = tmp_path / "audit.parquet"
    candidates = tmp_path / "candidates.parquet"
    summary = build_institution_hierarchy(
        institutions,
        InstitutionOverrideRegistry.load(overrides_path),
        hierarchy_path=hierarchy,
        audit_path=audit,
        candidates_path=candidates,
    )
    assert summary["hierarchy_row_count"] == 6
    assert summary["explicit_collapse_count"] == 1
    assert summary["automatic_collapse_count"] == 0
    connection = duckdb.connect()
    try:
        mappings = connection.execute(
            """
            SELECT hierarchy_view, institution_id, canonical_institution_id, is_collapsed
            FROM read_parquet(?) ORDER BY hierarchy_view, institution_id
            """,
            [str(hierarchy)],
        ).fetchall()
        i1_candidate = connection.execute(
            """
            SELECT candidate_umbrella_institution_ids, resolution
            FROM read_parquet(?) WHERE institution_id = 'I1'
            """,
            [str(candidates)],
        ).fetchone()
        audit_row = connection.execute(
            "SELECT rule_id, reason FROM read_parquet(?)", [str(audit)]
        ).fetchone()
    finally:
        connection.close()
    assert mappings == [
        ("organization", "I1", "I1", False),
        ("organization", "I2", "I2", False),
        ("organization", "I3", "I3", False),
        ("umbrella", "I1", "I1", False),
        ("umbrella", "I2", "I2", False),
        ("umbrella", "I3", "I2", True),
    ]
    assert i1_candidate == (["I2"], "retained_separate_without_explicit_rule")
    assert audit_row == ("collapse-i3", "Documented unit test")
