"""Real-data edge arithmetic validation and auditable summary output."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256

_VALIDATION_VERSION = "edge-arithmetic-validation-2026-08-05-v1"


def validate_edge_arithmetic(
    work_edges_path: str | Path,
    edges_year_path: str | Path,
    diagnostics_path: str | Path,
    *,
    warning_institution_count: int,
    exclusion_institution_count: int,
) -> dict[str, Any]:
    """Validate stored real-data contributions against pair and aggregation invariants."""
    work_edges = Path(work_edges_path)
    edges_year = Path(edges_year_path)
    diagnostics = Path(diagnostics_path)
    for path in (work_edges, edges_year, diagnostics):
        if not path.is_file():
            raise ValueError(f"edge validation input does not exist: {path}")
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = '2GB'")
        connection.execute("SET threads = 1")
        connection.execute("SET preserve_insertion_order = false")
        work_values = connection.execute(
            """
            SELECT
                count(*),
                count(*) FILTER (WHERE source_id >= target_id),
                count(*) FILTER (WHERE full_weight <> 1),
                count(*) FILTER (WHERE fractional_weight <= 0 OR fractional_weight > 1),
                count(DISTINCT corpus_view),
                count(DISTINCT hierarchy_view)
            FROM read_parquet(?)
            """,
            [str(work_edges)],
        ).fetchone()
        diagnostic_values = connection.execute(
            """
            SELECT
                count(*),
                count(*) FILTER (WHERE generated_pair_count <> expected_pair_count),
                count(*) FILTER (WHERE abs(fractional_weight_sum - 1.0) > 1e-10),
                max(fractional_sum_absolute_error),
                count(*) FILTER (
                    WHERE is_large_consortium <> (institution_count >= ?)
                ),
                count(*) FILTER (
                    WHERE exceeds_consortium_exclusion_threshold <> (institution_count >= ?)
                )
            FROM read_parquet(?)
            """,
            [warning_institution_count, exclusion_institution_count, str(diagnostics)],
        ).fetchone()
        reconciliation = connection.execute(
            """
            SELECT
                (SELECT sum(full_weight) FROM read_parquet(?)),
                (SELECT sum(full_count) FROM read_parquet(?)),
                (SELECT sum(fractional_weight) FROM read_parquet(?)),
                (SELECT sum(fractional_count) FROM read_parquet(?)),
                (SELECT count(*) FROM read_parquet(?))
            """,
            [
                str(work_edges),
                str(edges_year),
                str(work_edges),
                str(edges_year),
                str(edges_year),
            ],
        ).fetchone()
    finally:
        connection.close()
    if work_values is None or diagnostic_values is None or reconciliation is None:
        raise ValueError("edge validation query failed")
    checks = {
        "stable_source_target_order": int(work_values[1]) == 0,
        "unit_full_work_weights": int(work_values[2]) == 0,
        "fractional_weights_in_range": int(work_values[3]) == 0,
        "strict_and_broad_present": int(work_values[4]) == 2,
        "organization_and_umbrella_present": int(work_values[5]) == 2,
        "pair_counts_match_combinations": int(diagnostic_values[1]) == 0,
        "fractional_weights_sum_to_one_per_work": int(diagnostic_values[2]) == 0,
        "warning_threshold_flags_match": int(diagnostic_values[4]) == 0,
        "exclusion_threshold_flags_match": int(diagnostic_values[5]) == 0,
        "annual_full_counts_reconcile": int(reconciliation[0]) == int(reconciliation[1]),
        "annual_fractional_counts_reconcile": abs(
            float(reconciliation[2]) - float(reconciliation[3])
        )
        < 1e-4,
    }
    failed = sorted(name for name, passed in checks.items() if not passed)
    if failed:
        raise ValueError(f"edge arithmetic validation failed: {', '.join(failed)}")
    return {
        "schema_version": 1,
        "validation_version": _VALIDATION_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "validation_version": _VALIDATION_VERSION,
                "work_edges_sha256": file_sha256(work_edges),
                "edges_year_sha256": file_sha256(edges_year),
                "diagnostics_sha256": file_sha256(diagnostics),
                "warning_institution_count": warning_institution_count,
                "exclusion_institution_count": exclusion_institution_count,
            }
        ),
        "status": "passed",
        "checks": checks,
        "work_edge_count": int(work_values[0]),
        "collaborative_work_view_count": int(diagnostic_values[0]),
        "annual_edge_count": int(reconciliation[4]),
        "maximum_fractional_sum_absolute_error": float(diagnostic_values[3] or 0.0),
        "annual_fractional_reconciliation_difference": float(reconciliation[2])
        - float(reconciliation[3]),
        "generated_at_utc": _timestamp(),
    }


def write_edge_validation_artifact(
    payload: dict[str, Any],
    *,
    path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    write_json_artifact(
        path=path,
        dataset_name="edge_arithmetic_validation",
        payload=payload,
        records=[payload],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes={"project": config_file_hash(project_config_path)},
        source_versions={"validation_policy": _VALIDATION_VERSION},
        source_manifests=[
            ".agent/manifests/work_edges.json",
            ".agent/manifests/edges_year.json",
            ".agent/manifests/edge_work_diagnostics.json",
        ],
        command=command,
    )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
