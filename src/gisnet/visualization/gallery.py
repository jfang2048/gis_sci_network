"""Build compact, dependency-light SVGs for the repository result gallery."""

from __future__ import annotations

import argparse
import html
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from gisnet.atomic import atomic_write_text
from gisnet.dataset import file_sha256
from gisnet.state import RunLock, make_run_id

_FONT = "system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif"
_REGION_COLORS = {
    "Americas": "#0072B2",
    "Asia": "#E69F00",
    "Europe": "#009E73",
    "Africa": "#CC79A7",
    "Oceania": "#56B4E9",
    "Unknown": "#6B7280",
}


def build_readme_gallery(
    *,
    network_nodes_path: str | Path,
    network_edges_path: str | Path,
    topics_path: str | Path,
    network_figure_path: str | Path,
    topic_figure_path: str | Path,
) -> dict[str, Any]:
    """Render two deterministic README figures from the public dashboard snapshot."""
    nodes_path = Path(network_nodes_path)
    edges_path = Path(network_edges_path)
    topic_data_path = Path(topics_path)
    for source in (nodes_path, edges_path, topic_data_path):
        if not source.is_file():
            raise ValueError(f"gallery input does not exist: {source}")

    nodes = pd.read_parquet(nodes_path)
    edges = pd.read_parquet(edges_path)
    topics = pd.read_parquet(topic_data_path)
    years = set(nodes["year"]) & set(edges["year"]) & set(topics["year"])
    if not years:
        raise ValueError("gallery inputs have no common year")
    year = int(max(years))

    nodes = _view(nodes, year).sort_values("core_rank").head(100).copy()
    node_ids = set(nodes["institution_id"].astype(str))
    edges = _view(edges, year)
    edges = edges[
        edges["source_id"].astype(str).isin(node_ids)
        & edges["target_id"].astype(str).isin(node_ids)
    ]
    edges = edges.sort_values(
        ["fractional_count", "source_id", "target_id"],
        ascending=[False, True, True],
    ).head(220)
    topics = (
        _view(topics, year)
        .sort_values(["fractional_count", "topic_family"], ascending=[False, True])
        .head(8)
        .copy()
    )
    if nodes.empty or edges.empty or topics.empty:
        raise ValueError("gallery view is empty")

    network_output = Path(network_figure_path)
    topic_output = Path(topic_figure_path)
    network_output.parent.mkdir(parents=True, exist_ok=True)
    topic_output.parent.mkdir(parents=True, exist_ok=True)
    _write_network_svg(network_output, year, nodes, edges)
    _write_topic_svg(topic_output, year, topics)
    return {
        "year": year,
        "corpus_view": "broad",
        "hierarchy_view": "organization",
        "network_node_count": len(nodes),
        "network_edge_count": len(edges),
        "topic_family_count": len(topics),
        "source_sha256": {
            "network_nodes": file_sha256(nodes_path),
            "network_edges": file_sha256(edges_path),
            "topics": file_sha256(topic_data_path),
        },
        "outputs": {
            "network_snapshot_svg": str(network_output),
            "topic_family_profile_svg": str(topic_output),
        },
    }


def _view(frame: pd.DataFrame, year: int) -> pd.DataFrame:
    return frame[
        (frame["year"] == year)
        & (frame["corpus_view"] == "broad")
        & (frame["hierarchy_view"] == "organization")
    ]


def _write_network_svg(
    path: Path,
    year: int,
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
) -> None:
    width, height = 1200, 720
    plot_left, plot_top, plot_width, plot_height = 65, 125, 810, 510
    panel_left = 915
    minimum_x, maximum_x = float(nodes["x"].min()), float(nodes["x"].max())
    minimum_y, maximum_y = float(nodes["y"].min()), float(nodes["y"].max())
    x_span = maximum_x - minimum_x or 1.0
    y_span = maximum_y - minimum_y or 1.0

    def position(x_value: Any, y_value: Any) -> tuple[float, float]:
        x = plot_left + (float(x_value) - minimum_x) / x_span * plot_width
        y = plot_top + (maximum_y - float(y_value)) / y_span * plot_height
        return x, y

    positions = {
        str(row.institution_id): position(row.x, row.y) for row in nodes.itertuples(index=False)
    }
    maximum_edge = max(float(edges["fractional_count"].max()), 1.0)
    strengths = nodes["fractional_strength"].astype(float)
    minimum_strength = float(strengths.min())
    strength_span = float(strengths.max()) - minimum_strength or 1.0
    labels = _labelled_nodes(nodes)
    label_numbers = {
        str(row.institution_id): index
        for index, row in enumerate(labels.itertuples(index=False), start=1)
    }
    description = (
        f"Fixed-layout collaboration network for {year}, using the 100 highest-ranked institutions "
        f"and {len(edges)} strongest fractional collaboration edges within that core. Node color "
        "shows macro-region and numbered labels identify selected institutions."
    )
    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" '
            'aria-labelledby="network-title network-description">'
        ),
        f'<title id="network-title">{year} institutional collaboration core</title>',
        f'<desc id="network-description">{html.escape(description)}</desc>',
        '<rect width="100%" height="100%" fill="white"/>',
        _text(65, 42, f"{year} institutional collaboration core", 26, weight=700),
        _text(
            65,
            72,
            (
                "Broad corpus · organization hierarchy · 100 nodes · "
                f"{len(edges)} strongest internal edges"
            ),
            15,
            fill="#475569",
        ),
        '<rect x="52" y="105" width="842" height="550" rx="12" fill="#F8FAFC" stroke="#CBD5E1"/>',
    ]
    for row in edges.itertuples(index=False):
        source = positions.get(str(row.source_id))
        target = positions.get(str(row.target_id))
        if source is None or target is None:
            continue
        edge_width = 0.45 + 3.0 * math.sqrt(float(row.fractional_count) / maximum_edge)
        color = _REGION_COLORS.get(
            str(row.source_region) if row.source_region == row.target_region else "Unknown",
            _REGION_COLORS["Unknown"],
        )
        parts.append(
            f'<line x1="{source[0]:.2f}" y1="{source[1]:.2f}" '
            f'x2="{target[0]:.2f}" y2="{target[1]:.2f}" '
            f'stroke="{color}" stroke-opacity="0.24" stroke-width="{edge_width:.2f}"/>'
        )
    for row in reversed(list(nodes.itertuples(index=False))):
        x, y = positions[str(row.institution_id)]
        radius = 3.4 + 8.6 * math.sqrt(
            max(float(row.fractional_strength) - minimum_strength, 0.0) / strength_span
        )
        color = _REGION_COLORS.get(str(row.macro_region), _REGION_COLORS["Unknown"])
        parts.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="{radius:.2f}" fill="{color}" '
            'fill-opacity="0.90" stroke="white" stroke-width="1.2"/>'
        )
        number = label_numbers.get(str(row.institution_id))
        if number is not None:
            parts.extend(
                [
                    (
                        f'<circle cx="{x:.2f}" cy="{y:.2f}" r="10" '
                        'fill="#0F172A" stroke="white" stroke-width="1.5"/>'
                    ),
                    _text(x, y + 4, str(number), 11, fill="white", weight=700, anchor="middle"),
                ]
            )

    parts.extend(
        [
            _text(panel_left, 125, "Selected institutions", 18, weight=700),
            _text(panel_left, 149, "Numbers are labels, not ranks", 13, fill="#64748B"),
        ]
    )
    for index, row in enumerate(labels.itertuples(index=False), start=1):
        y = 182 + (index - 1) * 48
        name = _truncate(str(row.display_name), 30)
        region = str(row.macro_region)
        parts.extend(
            [
                f'<circle cx="{panel_left + 10}" cy="{y - 4}" r="10" fill="#0F172A"/>',
                _text(
                    panel_left + 10,
                    y,
                    str(index),
                    11,
                    fill="white",
                    weight=700,
                    anchor="middle",
                ),
                _text(panel_left + 29, y - 3, name, 13, weight=600),
                _text(panel_left + 29, y + 15, region, 12, fill="#64748B"),
            ]
        )
    legend_y = 582
    parts.append(_text(panel_left, legend_y, "Macro-region", 14, weight=700))
    visible_regions = [
        region
        for region in ["Americas", "Asia", "Europe", "Africa", "Oceania", "Unknown"]
        if region in set(nodes["macro_region"].fillna("Unknown").astype(str))
    ]
    for index, region in enumerate(visible_regions):
        x = panel_left + (index % 2) * 125
        y = legend_y + 24 + (index // 2) * 24
        parts.extend(
            [
                f'<circle cx="{x + 6}" cy="{y - 4}" r="6" fill="{_REGION_COLORS[region]}"/>',
                _text(x + 18, y, region, 12, fill="#334155"),
            ]
        )
    parts.append(
        _text(
            65,
            690,
            (
                "Node size: fractional strength · Edge selection/width: fractional "
                "collaboration weight · fixed full-period layout"
            ),
            13,
            fill="#475569",
        )
    )
    parts.append("</svg>")
    atomic_write_text(path, "\n".join(parts) + "\n")


def _labelled_nodes(nodes: pd.DataFrame) -> pd.DataFrame:
    selected: list[str] = []
    for institution_id in nodes.head(4)["institution_id"].astype(str):
        selected.append(institution_id)
    for _, group in nodes.groupby("macro_region", sort=True):
        institution_id = str(group.sort_values("core_rank").iloc[0]["institution_id"])
        if institution_id not in selected:
            selected.append(institution_id)
    return (
        nodes.assign(_id=nodes["institution_id"].astype(str))
        .set_index("_id")
        .loc[selected[:8]]
        .reset_index(drop=True)
    )


def _write_topic_svg(path: Path, year: int, topics: pd.DataFrame) -> None:
    width, height = 1200, 720
    left, top, plot_width, row_height = 365, 130, 720, 58
    maximum = float(topics["fractional_count"].max()) or 1.0
    axis_max = math.ceil(maximum / 1000.0) * 1000.0
    description = (
        f"Horizontal bar chart of the eight Topic families with the greatest fractional edge "
        f"weight in the {year} Broad organization-view dashboard core."
    )
    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" '
            'aria-labelledby="topic-title topic-description">'
        ),
        f'<title id="topic-title">{year} collaboration by Topic family</title>',
        f'<desc id="topic-description">{html.escape(description)}</desc>',
        '<rect width="100%" height="100%" fill="white"/>',
        _text(60, 42, f"{year} collaboration by Topic family", 26, weight=700),
        _text(
            60,
            72,
            "Broad corpus · organization hierarchy · thresholded dashboard core",
            15,
            fill="#475569",
        ),
    ]
    for tick in range(6):
        value = axis_max * tick / 5
        x = left + plot_width * tick / 5
        parts.extend(
            [
                (
                    f'<line x1="{x:.2f}" y1="105" x2="{x:.2f}" y2="604" '
                    'stroke="#E2E8F0" stroke-width="1"/>'
                ),
                _text(x, 628, f"{value:,.0f}", 12, fill="#64748B", anchor="middle"),
            ]
        )
    for index, row in enumerate(topics.itertuples(index=False)):
        y = top + index * row_height
        value = float(row.fractional_count)
        bar_width = plot_width * value / axis_max
        label = str(row.topic_family).replace("_", " ").title().replace("Gis", "GIS")
        parts.extend(
            [
                _text(left - 18, y + 23, label, 16, fill="#1E293B", anchor="end"),
                (
                    f'<rect x="{left}" y="{y}" width="{bar_width:.2f}" '
                    'height="34" rx="4" fill="#0072B2"/>'
                ),
                _text(left + bar_width + 10, y + 23, f"{value:,.1f}", 14, weight=700),
            ]
        )
    parts.extend(
        [
            _text(
                left + plot_width / 2,
                662,
                "Fractional collaboration weight",
                14,
                weight=700,
                anchor="middle",
            ),
            _text(
                60,
                695,
                (
                    "Topic values cover only the fixed-layout core edges shown by the public "
                    "dashboard; they are not full-corpus totals."
                ),
                13,
                fill="#475569",
            ),
            "</svg>",
        ]
    )
    atomic_write_text(path, "\n".join(parts) + "\n")


def _text(
    x: float,
    y: float,
    value: str,
    size: int,
    *,
    fill: str = "#0F172A",
    weight: int | None = None,
    anchor: str | None = None,
) -> str:
    attributes = [
        f'x="{x:.2f}"',
        f'y="{y:.2f}"',
        f'font-family="{_FONT}"',
        f'font-size="{size}"',
        f'fill="{fill}"',
    ]
    if weight is not None:
        attributes.append(f'font-weight="{weight}"')
    if anchor is not None:
        attributes.append(f'text-anchor="{anchor}"')
    return f"<text {' '.join(attributes)}>{html.escape(value)}</text>"


def _truncate(value: str, limit: int) -> str:
    return value if len(value) <= limit else f"{value[: limit - 1].rstrip()}…"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("dashboard/data"))
    parser.add_argument("--output-dir", type=Path, default=Path("figures"))
    args = parser.parse_args(argv)
    with RunLock(run_id=make_run_id(), task_id="GISNET-095"):
        result = build_readme_gallery(
            network_nodes_path=args.data_dir / "network_nodes.parquet",
            network_edges_path=args.data_dir / "network_edges.parquet",
            topics_path=args.data_dir / "topics.parquet",
            network_figure_path=args.output_dir / "network_snapshot.svg",
            topic_figure_path=args.output_dir / "topic_family_profile.svg",
        )
    print(
        f"Built {result['network_node_count']}-node network and "
        f"{result['topic_family_count']}-family Topic figures for {result['year']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
