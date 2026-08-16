"""Annual regional trend series and dependency-free publication SVG exports."""

from __future__ import annotations

import html
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "annual-region-trends-2026-08-17-v2"
_COLORS = ("#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#6B7280")
_DASHES = ("", "10 4", "3 3", "10 3 3 3", "6 3", "2 3")
_FONT = "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"


def build_annual_trends(
    flows_path: str | Path,
    *,
    output_path: str | Path,
    trend_figure_path: str | Path,
    comparison_figure_path: str | Path,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Create macro-region trend data and static SVG figures."""
    flows = Path(flows_path)
    if not flows.is_file():
        raise ValueError(f"trend input does not exist: {flows}")
    output = Path(output_path)
    trend_figure = Path(trend_figure_path)
    comparison_figure = Path(comparison_figure_path)
    for path in (output, trend_figure, comparison_figure):
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".parquet.tmp")
    temporary.unlink(missing_ok=True)
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        connection.execute(
            f"""
            COPY (
                SELECT
                    year,
                    corpus_view,
                    hierarchy_view,
                    source_geography AS source_region,
                    target_geography AS target_region,
                    source_geography || ' — ' || target_geography AS region_pair,
                    source_geography = target_geography AS is_intra_region,
                    full_count,
                    fractional_count,
                    normalized_share,
                    distinct_work_count,
                    distinct_institution_pair_count,
                    'complete calendar year' AS year_status,
                    'full_count or fractional_count as selected' AS units_note
                FROM read_parquet(?)
                WHERE geographic_level = 'macro_region'
                ORDER BY year, corpus_view, hierarchy_view, source_region, target_region
            ) TO '{_literal(temporary)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [str(flows)],
        )
        broad_rows = connection.execute(
            """
            SELECT year, region_pair, fractional_count
            FROM read_parquet(?)
            WHERE corpus_view = 'broad' AND hierarchy_view = 'organization'
              AND region_pair IN (
                'Americas — Americas',
                'Americas — Asia',
                'Americas — Europe',
                'Asia — Asia',
                'Asia — Europe',
                'Europe — Europe'
              )
            ORDER BY region_pair, year
            """,
            [str(temporary)],
        ).fetchall()
        comparison_rows = connection.execute(
            """
            SELECT
                year,
                corpus_view || ' / ' || hierarchy_view AS series,
                sum(fractional_count) AS fractional_count
            FROM read_parquet(?)
            WHERE source_region != target_region
            GROUP BY year, corpus_view, hierarchy_view
            ORDER BY series, year
            """,
            [str(temporary)],
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
            "source_region",
            "target_region",
        ],
        required_columns={
            "year",
            "corpus_view",
            "hierarchy_view",
            "region_pair",
            "full_count",
            "fractional_count",
            "normalized_share",
        },
        year_column="year",
    )
    if not broad_rows or not comparison_rows:
        temporary.unlink(missing_ok=True)
        raise ValueError("trend series are empty")
    _write_svg(
        trend_figure,
        title="Annual regional GIS collaboration, 2010-2025",
        subtitle="Broad corpus · organization hierarchy · fractional edge weight · complete years",
        rows=[(int(year), str(series), float(value)) for year, series, value in broad_rows],
        y_label="Fractional collaboration weight",
    )
    _write_svg(
        comparison_figure,
        title="Strict/Broad and organization/umbrella comparison",
        subtitle="All cross-region pairs · fractional edge weight · complete calendar years",
        rows=[(int(year), str(series), float(value)) for year, series, value in comparison_rows],
        y_label="Cross-region fractional weight",
        overlap_note=(
            "Organization and umbrella series are identical in this release because no active "
            "collapse changes these aggregates."
        ),
    )
    os.replace(temporary, output)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "flows_sha256": file_sha256(flows),
            }
        ),
        "trend_row_count": int(metrics["row_count"]),
        "year_minimum": int(metrics["min_year"]),
        "year_maximum": int(metrics["max_year"]),
        "partial_years_included": False,
        "static_format": "SVG",
        "outputs": {
            "trend_series_year": str(output),
            "annual_region_trends_svg": str(trend_figure),
            "view_comparison_svg": str(comparison_figure),
        },
        "generated_at_utc": _timestamp(),
    }


def write_trend_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [
        ".agent/manifests/region_flows_year.json",
    ]
    source_versions = {"trend_figure_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="annual_trends_summary",
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
        path=summary["outputs"]["trend_series_year"],
        dataset_name="trend_series_year",
        primary_key=[
            "year",
            "corpus_view",
            "hierarchy_view",
            "source_region",
            "target_region",
        ],
        required_columns={"year", "region_pair", "full_count", "fractional_count"},
        year_column="year",
        run_id=run_id,
        config_hashes=config_hashes,
        source_manifests=source_manifests,
        source_versions=source_versions,
        command=command,
    )


def _write_svg(
    path: Path,
    *,
    title: str,
    subtitle: str,
    rows: list[tuple[int, str, float]],
    y_label: str,
    overlap_note: str | None = None,
) -> None:
    width, height = 1200, 720
    left, right, top, bottom = 100, 48, 120, 210
    plot_width = width - left - right
    plot_height = height - top - bottom
    years = sorted({row[0] for row in rows})
    series_names = sorted({row[1] for row in rows})
    maximum = _nice_axis_max(max(row[2] for row in rows))
    year_span = max(years[-1] - years[0], 1)
    description = (
        f"Line chart with {len(series_names)} series from {years[0]} through {years[-1]}. "
        "Line colors and dash patterns distinguish series; all years are complete calendar years."
    )
    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" '
            'aria-labelledby="chart-title chart-description">'
        ),
        f'<title id="chart-title">{html.escape(title)}</title>',
        f'<desc id="chart-description">{html.escape(description)}</desc>',
        '<rect width="100%" height="100%" fill="white"/>',
        (
            f'<text x="{left}" y="40" font-family="{_FONT}" font-size="25" '
            f'font-weight="700">{html.escape(title)}</text>'
        ),
        (
            f'<text x="{left}" y="70" font-family="{_FONT}" font-size="15" '
            f'fill="#475569">{html.escape(subtitle)}</text>'
        ),
    ]
    if overlap_note and _has_overlapping_series(rows):
        parts.append(
            f'<text x="{left}" y="96" font-family="{_FONT}" font-size="13" '
            f'fill="#475569">{html.escape(overlap_note)}</text>'
        )
    for tick in range(6):
        value = maximum * tick / 5
        y = top + plot_height * (1 - tick / 5)
        parts.append(
            f'<line x1="{left}" y1="{y:.2f}" x2="{width - right}" y2="{y:.2f}" stroke="#e2e8f0"/>'
        )
        parts.append(
            f'<text x="{left - 12}" y="{y + 5:.2f}" text-anchor="end" '
            f'font-family="{_FONT}" font-size="12" fill="#64748b">'
            f"{value:,.0f}</text>"
        )
    labelled_years = [year for year in years if (year - years[0]) % 2 == 0]
    if years[-1] not in labelled_years:
        labelled_years.append(years[-1])
    for year in labelled_years:
        x = left + (year - years[0]) / year_span * plot_width
        parts.append(
            f'<text x="{x:.2f}" y="{height - bottom + 28}" text-anchor="middle" '
            f'font-family="{_FONT}" font-size="12" fill="#475569">{year}</text>'
        )
    by_series: dict[str, list[tuple[int, float]]] = {}
    for year, series, value in rows:
        by_series.setdefault(series, []).append((year, value))
    for index, series in enumerate(series_names):
        color = _COLORS[index % len(_COLORS)]
        dash = _DASHES[index % len(_DASHES)]
        dash_attribute = f' stroke-dasharray="{dash}"' if dash else ""
        points = " ".join(
            (
                f"{left + (year - years[0]) / year_span * plot_width:.2f},"
                f"{top + plot_height * (1 - value / maximum):.2f}"
            )
            for year, value in sorted(by_series[series])
        )
        parts.append(
            f'<polyline points="{points}" fill="none" stroke="{color}" stroke-width="3"'
            f'{dash_attribute} stroke-linecap="round" stroke-linejoin="round"/>'
        )
        final_year, final_value = max(by_series[series])
        final_x = left + (final_year - years[0]) / year_span * plot_width
        final_y = top + plot_height * (1 - final_value / maximum)
        parts.append(
            f'<circle cx="{final_x:.2f}" cy="{final_y:.2f}" r="4" fill="white" '
            f'stroke="{color}" stroke-width="2"/>'
        )
        legend_x = left + (index % 3) * 350
        legend_y = 628 + (index // 3) * 28
        parts.append(
            f'<line x1="{legend_x}" y1="{legend_y}" x2="{legend_x + 28}" '
            f'y2="{legend_y}" stroke="{color}" stroke-width="4"{dash_attribute}/>'
        )
        parts.append(
            f'<text x="{legend_x + 36}" y="{legend_y + 5}" '
            f'font-family="{_FONT}" font-size="13">{html.escape(series)}</text>'
        )
    parts.append(
        f'<text transform="translate(24 {top + plot_height / 2}) rotate(-90)" '
        f'text-anchor="middle" font-family="{_FONT}" font-size="14">'
        f"{html.escape(y_label)}</text>"
    )
    parts.append(
        f'<text x="{left + plot_width / 2}" y="570" text-anchor="middle" '
        f'font-family="{_FONT}" font-size="14">'
        "Publication year (complete calendar years only)</text>"
    )
    parts.append("</svg>\n")
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("\n".join(parts), encoding="utf-8")
    os.replace(temporary, path)


def _nice_axis_max(value: float) -> float:
    if value <= 0:
        return 1.0
    target = value * 1.05
    magnitude = 10 ** math.floor(math.log10(target))
    normalized = target / magnitude
    step = next(candidate for candidate in (1.0, 2.0, 5.0, 10.0) if normalized <= candidate)
    return float(step * magnitude)


def _has_overlapping_series(rows: list[tuple[int, str, float]]) -> bool:
    by_series: dict[str, tuple[tuple[int, float], ...]] = {}
    names = {series for _, series, _ in rows}
    for series in names:
        by_series[series] = tuple(
            sorted((year, value) for year, candidate, value in rows if candidate == series)
        )
    fingerprints: dict[tuple[tuple[int, float], ...], int] = {}
    for values in by_series.values():
        fingerprints[values] = fingerprints.get(values, 0) + 1
    return any(count > 1 for count in fingerprints.values())


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
