"""Stable annual collaboration matrices and geographic drilldown tables."""

from __future__ import annotations

import html
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "region-collaboration-matrix-2026-08-05-v1"


def build_collaboration_matrix(
    flows_path: str | Path,
    *,
    output_path: str | Path,
    figure_path: str | Path,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Add stable ordering and explicit sparse semantics to every flow level."""
    source = Path(flows_path)
    if not source.is_file():
        raise ValueError(f"matrix input does not exist: {source}")
    output = Path(output_path)
    figure = Path(figure_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    figure.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".parquet.tmp")
    temporary.unlink(missing_ok=True)
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        connection.execute(
            f"""
            COPY (
                WITH geographies AS (
                    SELECT geographic_level, geography,
                           dense_rank() OVER (
                               PARTITION BY geographic_level ORDER BY geography
                           )::INTEGER AS stable_order
                    FROM (
                        SELECT DISTINCT geographic_level, source_geography AS geography
                        FROM read_parquet(?)
                        UNION
                        SELECT DISTINCT geographic_level, target_geography AS geography
                        FROM read_parquet(?)
                    )
                )
                SELECT
                    flows.*,
                    source_order.stable_order AS source_order,
                    target_order.stable_order AS target_order,
                    'observed_nonzero' AS cell_status,
                    'absent row means no observed flow; value is not imputed as zero'
                        AS absent_cell_semantics
                FROM read_parquet(?) flows
                INNER JOIN geographies source_order
                    ON source_order.geographic_level = flows.geographic_level
                   AND source_order.geography = flows.source_geography
                INNER JOIN geographies target_order
                    ON target_order.geographic_level = flows.geographic_level
                   AND target_order.geography = flows.target_geography
                ORDER BY year, corpus_view, hierarchy_view, geographic_level,
                         source_order, target_order
            ) TO '{_literal(temporary)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [str(source), str(source), str(source)],
        )
        latest_year = connection.execute(
            "SELECT max(year) FROM read_parquet(?)", [str(temporary)]
        ).fetchone()
        if latest_year is None:
            raise ValueError("matrix input is empty")
        rows = connection.execute(
            """
            SELECT source_geography, target_geography, fractional_count
            FROM read_parquet(?)
            WHERE year = ? AND corpus_view = 'broad'
              AND hierarchy_view = 'organization' AND geographic_level = 'macro_region'
            ORDER BY source_order, target_order
            """,
            [str(temporary), int(latest_year[0])],
        ).fetchall()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    metrics = parquet_metrics(
        temporary,
        primary_key=[
            "year",
            "corpus_view",
            "hierarchy_view",
            "geographic_level",
            "source_geography",
            "target_geography",
        ],
        required_columns={
            "year",
            "geographic_level",
            "source_order",
            "target_order",
            "full_count",
            "fractional_count",
            "normalized_share",
            "cell_status",
        },
        year_column="year",
    )
    validation = duckdb.connect()
    try:
        checks = validation.execute(
            """
            WITH expected AS (
                SELECT year, corpus_view, hierarchy_view, geographic_level,
                       sum(full_count) AS full_count, sum(fractional_count) AS fractional_count
                FROM read_parquet(?) GROUP BY ALL
            ), actual AS (
                SELECT year, corpus_view, hierarchy_view, geographic_level,
                       sum(full_count) AS full_count, sum(fractional_count) AS fractional_count
                FROM read_parquet(?) GROUP BY ALL
            )
            SELECT count(*) FILTER (
                WHERE expected.full_count != actual.full_count
                   OR abs(expected.fractional_count - actual.fractional_count) > 1e-8
            ), count(*)
            FROM expected INNER JOIN actual USING (
                year, corpus_view, hierarchy_view, geographic_level
            )
            """,
            [str(source), str(temporary)],
        ).fetchone()
    finally:
        validation.close()
    if checks is None or int(checks[0]):
        temporary.unlink(missing_ok=True)
        raise ValueError("matrix totals do not reconcile with region-flow data")
    _write_matrix_svg(figure, int(latest_year[0]), rows)
    os.replace(temporary, output)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {"stage_version": _STAGE_VERSION, "flows_sha256": file_sha256(source)}
        ),
        "matrix_and_drilldown_row_count": int(metrics["row_count"]),
        "reconciled_group_count": int(checks[1]),
        "reconciliation_failure_count": int(checks[0]),
        "geographic_levels": ["macro_region", "subregion", "country"],
        "sparse_cell_semantics": "absent row is missing/no observed flow, never an imputed zero",
        "latest_figure_year": int(latest_year[0]),
        "outputs": {
            "collaboration_matrix_year": str(output),
            "region_matrix_svg": str(figure),
        },
        "generated_at_utc": _timestamp(),
    }


def write_matrix_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [".agent/manifests/region_flows_year.json"]
    source_versions = {"matrix_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="collaboration_matrix_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions=source_versions,
        source_manifests=source_manifests,
        command=command,
    )
    write_parquet_manifest(
        path=summary["outputs"]["collaboration_matrix_year"],
        dataset_name="collaboration_matrix_year",
        primary_key=[
            "year",
            "corpus_view",
            "hierarchy_view",
            "geographic_level",
            "source_geography",
            "target_geography",
        ],
        required_columns={
            "year",
            "geographic_level",
            "full_count",
            "fractional_count",
            "normalized_share",
            "cell_status",
        },
        year_column="year",
        run_id=run_id,
        config_hashes=config_hashes,
        source_manifests=source_manifests,
        source_versions=source_versions,
        command=command,
    )


def _write_matrix_svg(path: Path, year: int, rows: list[tuple[Any, ...]]) -> None:
    labels = sorted({str(row[0]) for row in rows} | {str(row[1]) for row in rows})
    values = {(str(source), str(target)): float(value) for source, target, value in rows}
    maximum = max(values.values(), default=1.0) or 1.0
    cell, left, top = 86, 210, 110
    width, height = left + cell * len(labels) + 40, top + cell * len(labels) + 90
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        (
            '<text x="24" y="36" font-family="sans-serif" font-size="23" '
            f'font-weight="700">Broad organization macro-region matrix, {year}</text>'
        ),
        (
            '<text x="24" y="66" font-family="sans-serif" font-size="14" '
            'fill="#475569">Fractional collaboration weight; exact values are in '
            "collaboration_matrix_year.parquet</text>"
        ),
    ]
    for row_index, source in enumerate(labels):
        y = top + row_index * cell
        parts.append(
            f'<text x="{left - 12}" y="{y + cell / 2 + 5}" text-anchor="end" '
            f'font-family="sans-serif" font-size="13">{html.escape(source)}</text>'
        )
        for column_index, target in enumerate(labels):
            x = left + column_index * cell
            value = values.get((source, target), values.get((target, source)))
            if value is None:
                color, label = "#f8fafc", "missing"
            else:
                shade = int(245 - 185 * value / maximum)
                color, label = f"rgb({shade},{shade + 12},255)", f"{value:,.0f}"
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell}" height="{cell}" '
                f'fill="{color}" stroke="white"/>'
            )
            parts.append(
                f'<text x="{x + cell / 2}" y="{y + cell / 2 + 5}" '
                f'text-anchor="middle" font-family="sans-serif" font-size="11">{label}</text>'
            )
    for column_index, target in enumerate(labels):
        x_position = left + column_index * cell + cell / 2
        parts.append(
            f'<text transform="translate({x_position} {top - 10}) rotate(-40)" '
            f'text-anchor="start" font-family="sans-serif" font-size="13">'
            f"{html.escape(target)}</text>"
        )
    parts.append("</svg>\n")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(parts), encoding="utf-8")
    os.replace(temporary, path)


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
