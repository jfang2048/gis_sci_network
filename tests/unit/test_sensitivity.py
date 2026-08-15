from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.validation.sensitivity import (
    _comparison_row,
    _institution_scope_comparison,
    _write_institution_scope_sensitivity,
)


def test_sensitivity_change_flag_and_unavailable_status() -> None:
    changed = _comparison_row("S01", "A", "count", "base", "alt", 100, 125)
    assert changed["absolute_relative_change"] == 0.25
    assert changed["major_change"] is True
    assert changed["primary_result_overwritten"] is False
    unavailable = _comparison_row(
        "S08", "Registry", "count", "provisional", "reviewed", None, None, status="not_available"
    )
    assert unavailable["major_change"] is False
    assert unavailable["status"] == "not_available"


def test_s06_requires_and_uses_separately_materialized_expanded_scope(tmp_path: Path) -> None:
    missing = _institution_scope_comparison(tmp_path / "missing.parquet")
    assert missing["status"] == "not_available_expanded_scope_not_materialized"
    assert missing["alternative_value"] is None

    memberships = tmp_path / "work_institutions.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "publication_year": 2025,
                    "hierarchy_view": "organization",
                    "work_id": "W1",
                    "institution_id": "I1",
                    "strict_primary": True,
                    "broad_primary": True,
                    "is_primary_network_scope": True,
                    "is_target_macro_region": True,
                    "analytical_scope": "primary",
                },
                {
                    "publication_year": 2025,
                    "hierarchy_view": "organization",
                    "work_id": "W1",
                    "institution_id": "I2",
                    "strict_primary": True,
                    "broad_primary": True,
                    "is_primary_network_scope": False,
                    "is_target_macro_region": True,
                    "analytical_scope": "secondary",
                },
            ]
        ),
        memberships,
    )
    scope = tmp_path / "institution_scope_sensitivity_year.parquet"
    connection = duckdb.connect()
    try:
        _write_institution_scope_sensitivity(connection, memberships, scope)
    finally:
        connection.close()

    comparison = _institution_scope_comparison(scope)
    assert comparison["status"] == "complete"
    assert comparison["baseline_value"] == 1.0
    assert comparison["alternative_value"] == 2.0
    assert comparison["absolute_difference"] == 1.0
