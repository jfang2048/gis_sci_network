"""Required sensitivity matrix over stored primary and alternative policy views."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import yaml

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "required-sensitivity-matrix-2026-08-06-v2"
_MAJOR_CHANGE_THRESHOLD = 0.20


def build_sensitivity_matrix(
    graph_metrics_path: str | Path,
    edges_path: str | Path,
    work_edges_path: str | Path,
    nodes_path: str | Path,
    work_institutions_path: str | Path,
    work_corpus_path: str | Path,
    topic_registry_path: str | Path,
    *,
    output_path: str | Path,
    scope_output_path: str | Path,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Compute the eight required sensitivity comparisons without replacing primary outputs."""
    sources = [
        Path(graph_metrics_path),
        Path(edges_path),
        Path(work_edges_path),
        Path(nodes_path),
        Path(work_institutions_path),
        Path(work_corpus_path),
        Path(topic_registry_path),
    ]
    for path in sources:
        if not path.is_file():
            raise ValueError(f"sensitivity input does not exist: {path}")
    output = Path(output_path)
    scope_output = Path(scope_output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    scope_output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".parquet.tmp")
    scope_temporary = scope_output.with_suffix(".parquet.tmp")
    for path in (temporary, scope_temporary):
        path.unlink(missing_ok=True)
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        strict_broad = connection.execute(
            """
            SELECT
                sum(edge_count) FILTER (WHERE corpus_view = 'strict'),
                sum(edge_count) FILTER (WHERE corpus_view = 'broad')
            FROM read_parquet(?) WHERE hierarchy_view = 'organization'
            """,
            [str(sources[0])],
        ).fetchone()
        counting = connection.execute(
            """
            SELECT sum(full_count), sum(fractional_count)
            FROM read_parquet(?)
            WHERE corpus_view = 'broad' AND hierarchy_view = 'organization'
            """,
            [str(sources[1])],
        ).fetchone()
        hierarchy = connection.execute(
            """
            SELECT
                sum(edge_count) FILTER (WHERE hierarchy_view = 'organization'),
                sum(edge_count) FILTER (WHERE hierarchy_view = 'umbrella')
            FROM read_parquet(?) WHERE corpus_view = 'broad'
            """,
            [str(sources[0])],
        ).fetchone()
        rolling = connection.execute(
            """
            WITH years AS (
                SELECT DISTINCT year FROM read_parquet(?)
                WHERE corpus_view = 'broad' AND hierarchy_view = 'organization'
            ), annual AS (
                SELECT year, count(*)::DOUBLE AS edge_count
                FROM read_parquet(?)
                WHERE corpus_view = 'broad' AND hierarchy_view = 'organization'
                GROUP BY year
            ), rolling AS (
                SELECT y.year, count(DISTINCT (e.source_id, e.target_id))::DOUBLE AS edge_count
                FROM years y
                INNER JOIN read_parquet(?) e
                    ON e.year BETWEEN y.year - 2 AND y.year
                   AND e.corpus_view = 'broad'
                   AND e.hierarchy_view = 'organization'
                GROUP BY y.year
            )
            SELECT avg(annual.edge_count), avg(rolling.edge_count)
            FROM annual INNER JOIN rolling USING (year)
            """,
            [str(sources[1]), str(sources[1]), str(sources[1])],
        ).fetchone()
        consortium = connection.execute(
            """
            SELECT
                sum(fractional_weight),
                sum(fractional_weight) FILTER (WHERE NOT is_large_consortium)
            FROM read_parquet(?)
            WHERE corpus_view = 'broad' AND hierarchy_view = 'organization'
            """,
            [str(sources[2])],
        ).fetchone()
        _write_institution_scope_sensitivity(connection, sources[4], scope_temporary)
        preprints = connection.execute(
            """
            SELECT
                count(*) FILTER (WHERE broad_primary),
                count(*) FILTER (WHERE broad_preprint_sensitivity)
            FROM read_parquet(?)
            """,
            [str(sources[5])],
        ).fetchone()
    except BaseException:
        for path in (temporary, scope_temporary):
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    values = (strict_broad, counting, hierarchy, rolling, consortium, preprints)
    if any(value is None for value in values):
        raise ValueError("required sensitivity query returned no result")
    assert strict_broad is not None
    assert counting is not None
    assert hierarchy is not None
    assert rolling is not None
    assert consortium is not None
    assert preprints is not None
    scope_row = _institution_scope_comparison(scope_temporary)
    registry = yaml.safe_load(sources[6].read_text(encoding="utf-8"))
    review_status = str(registry.get("review_status", "unknown"))
    rows = [
        _comparison_row(
            "S01",
            "Strict versus Broad",
            "total annual edge observations",
            "strict",
            "broad",
            strict_broad[0],
            strict_broad[1],
        ),
        _comparison_row(
            "S02",
            "Full versus fractional counting",
            "aggregate edge weight (different counting units)",
            "full count",
            "fractional count",
            counting[0],
            counting[1],
        ),
        _comparison_row(
            "S03",
            "Organization versus umbrella",
            "total annual edge observations",
            "organization",
            "umbrella",
            hierarchy[0],
            hierarchy[1],
        ),
        _comparison_row(
            "S04",
            "Annual versus 3-year rolling network",
            "mean distinct edge count per window",
            "annual",
            "trailing 3-year union",
            rolling[0],
            rolling[1],
        ),
        _comparison_row(
            "S05",
            "Include versus exclude large consortium papers",
            "aggregate fractional edge weight",
            "include",
            "exclude institution_count >= warning threshold",
            consortium[0],
            consortium[1],
        ),
        scope_row,
        _comparison_row(
            "S07",
            "Published-only versus published plus preprints",
            "distinct eligible Works",
            "published primary",
            "published plus preprint sensitivity",
            preprints[0],
            preprints[1],
        ),
        _comparison_row(
            "S08",
            "Provisional versus reviewed Topic registry",
            "eligible Works",
            f"provisional ({review_status})",
            "human-reviewed",
            None,
            None,
            status="not_available_no_human_reviewed_registry",
        ),
    ]
    pq.write_table(pa.Table.from_pylist(rows), temporary, compression="zstd")
    metrics = parquet_metrics(
        temporary,
        primary_key=["comparison_id"],
        required_columns={
            "comparison_id",
            "comparison",
            "status",
            "major_change",
            "primary_result_overwritten",
        },
    )
    scope_metrics = parquet_metrics(
        scope_temporary,
        primary_key=["year", "corpus_view", "hierarchy_view", "scope_view"],
        required_columns={
            "year",
            "corpus_view",
            "hierarchy_view",
            "scope_view",
            "institution_work_count",
            "scope_definition",
        },
        year_column="year",
    )
    validation = duckdb.connect()
    try:
        checks = validation.execute(
            """
            SELECT
                count(DISTINCT comparison_id),
                count(*) FILTER (WHERE primary_result_overwritten),
                count(*) FILTER (WHERE status = 'complete'),
                count(*) FILTER (WHERE major_change)
            FROM read_parquet(?)
            """,
            [str(temporary)],
        ).fetchone()
    finally:
        validation.close()
    if checks is None or int(checks[0]) != 8 or int(checks[1]):
        for path in (temporary, scope_temporary):
            path.unlink(missing_ok=True)
        raise ValueError("sensitivity matrix coverage or primary-output isolation failed")
    os.replace(scope_temporary, scope_output)
    os.replace(temporary, output)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "source_sha256": [file_sha256(path) for path in sources],
                "major_change_threshold": _MAJOR_CHANGE_THRESHOLD,
            }
        ),
        "comparison_count": int(metrics["row_count"]),
        "completed_comparison_count": int(checks[2]),
        "major_change_count": int(checks[3]),
        "unavailable_comparison_count": 8 - int(checks[2]),
        "expanded_scope_materialized": True,
        "scope_row_count": int(scope_metrics["row_count"]),
        "primary_scope_institution_work_count": scope_row["baseline_value"],
        "expanded_scope_institution_work_count": scope_row["alternative_value"],
        "major_change_threshold": _MAJOR_CHANGE_THRESHOLD,
        "primary_result_overwritten": False,
        "outputs": {
            "sensitivity_matrix": str(output),
            "institution_scope_sensitivity_year": str(scope_output),
        },
        "generated_at_utc": _timestamp(),
    }


def _comparison_row(
    comparison_id: str,
    comparison: str,
    metric: str,
    baseline_label: str,
    alternative_label: str,
    baseline_value: float | int | None,
    alternative_value: float | int | None,
    *,
    status: str = "complete",
) -> dict[str, Any]:
    baseline = float(baseline_value) if baseline_value is not None else None
    alternative = float(alternative_value) if alternative_value is not None else None
    difference = (
        alternative - baseline if baseline is not None and alternative is not None else None
    )
    relative = (
        abs(difference) / max(abs(baseline), 1e-12)
        if difference is not None and baseline is not None
        else None
    )
    return {
        "comparison_id": comparison_id,
        "comparison": comparison,
        "metric": metric,
        "baseline_label": baseline_label,
        "alternative_label": alternative_label,
        "baseline_value": baseline,
        "alternative_value": alternative,
        "absolute_difference": difference,
        "absolute_relative_change": relative,
        "major_change": relative is not None and relative >= _MAJOR_CHANGE_THRESHOLD,
        "major_change_rule": f"absolute_relative_change >= {_MAJOR_CHANGE_THRESHOLD}",
        "status": status,
        "primary_result_overwritten": False,
    }


def write_sensitivity_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [
        ".agent/manifests/graph_metrics_year.json",
        ".agent/manifests/edges_year.json",
        ".agent/manifests/work_edges.json",
        ".agent/manifests/nodes_year.json",
        ".agent/manifests/work_institutions.json",
        ".agent/manifests/work_corpus.json",
    ]
    source_versions = {"sensitivity_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="sensitivity_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions=source_versions,
        source_manifests=source_manifests,
        command=command,
    )
    definitions = {
        "sensitivity_matrix": (
            ["comparison_id"],
            {"comparison_id", "comparison", "status", "major_change"},
            None,
        ),
        "institution_scope_sensitivity_year": (
            ["year", "corpus_view", "hierarchy_view", "scope_view"],
            {"year", "scope_view", "institution_work_count", "scope_definition"},
            "year",
        ),
    }
    for dataset_name, raw_path in summary["outputs"].items():
        primary_key, required_columns, year_column = definitions[dataset_name]
        write_parquet_manifest(
            path=raw_path,
            dataset_name=dataset_name,
            primary_key=primary_key,
            required_columns=required_columns,
            year_column=year_column,
            run_id=run_id,
            config_hashes=config_hashes,
            source_manifests=source_manifests,
            source_versions=source_versions,
            command=command,
        )


def _write_institution_scope_sensitivity(
    connection: duckdb.DuckDBPyConnection,
    work_institutions_path: Path,
    destination: Path,
) -> None:
    connection.execute(
        f"""
        COPY (
            WITH memberships AS (
                SELECT publication_year AS year, hierarchy_view, work_id, institution_id,
                       'strict' AS corpus_view, 'primary' AS scope_view
                FROM read_parquet(?)
                WHERE strict_primary AND is_primary_network_scope
                UNION ALL
                SELECT publication_year, hierarchy_view, work_id, institution_id,
                       'strict', 'expanded'
                FROM read_parquet(?)
                WHERE strict_primary AND is_target_macro_region
                  AND analytical_scope IN ('primary', 'secondary')
                UNION ALL
                SELECT publication_year, hierarchy_view, work_id, institution_id,
                       'broad', 'primary'
                FROM read_parquet(?)
                WHERE broad_primary AND is_primary_network_scope
                UNION ALL
                SELECT publication_year, hierarchy_view, work_id, institution_id,
                       'broad', 'expanded'
                FROM read_parquet(?)
                WHERE broad_primary AND is_target_macro_region
                  AND analytical_scope IN ('primary', 'secondary')
            )
            SELECT year, corpus_view, hierarchy_view, scope_view,
                   count(*)::BIGINT AS institution_work_count,
                   count(DISTINCT work_id)::BIGINT AS distinct_work_count,
                   count(DISTINCT institution_id)::BIGINT AS institution_count,
                   CASE scope_view
                       WHEN 'primary' THEN 'institution_types.analytical_scope = primary'
                       ELSE 'institution_types.analytical_scope IN (primary, secondary)'
                   END AS scope_definition
            FROM memberships
            GROUP BY year, corpus_view, hierarchy_view, scope_view
            ORDER BY year, corpus_view, hierarchy_view, scope_view
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """,
        [str(work_institutions_path)] * 4,
    )


def _institution_scope_comparison(scope_path: str | Path) -> dict[str, Any]:
    def unavailable() -> dict[str, Any]:
        return _comparison_row(
            "S06",
            "Primary institution types versus expanded types",
            "summed distinct Work-institution memberships",
            "primary institution types",
            "primary plus secondary institution types",
            None,
            None,
            status="not_available_expanded_scope_not_materialized",
        )

    path = Path(scope_path)
    if not path.is_file():
        return unavailable()
    connection = duckdb.connect()
    try:
        values = connection.execute(
            """
            SELECT
                sum(institution_work_count) FILTER (WHERE scope_view = 'primary'),
                sum(institution_work_count) FILTER (WHERE scope_view = 'expanded'),
                count(DISTINCT scope_definition),
                count(DISTINCT scope_view)
            FROM read_parquet(?)
            WHERE corpus_view = 'broad' AND hierarchy_view = 'organization'
            """,
            [str(path)],
        ).fetchone()
    except duckdb.Error:
        return unavailable()
    finally:
        connection.close()
    if (
        values is None
        or values[0] is None
        or values[1] is None
        or int(values[2]) != 2
        or int(values[3]) != 2
    ):
        return unavailable()
    return _comparison_row(
        "S06",
        "Primary institution types versus expanded types",
        "summed distinct Work-institution memberships",
        "primary institution types",
        "primary plus secondary institution types",
        values[0],
        values[1],
    )


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
