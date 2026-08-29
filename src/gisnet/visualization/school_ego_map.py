"""Predicate-pushed queries and exact figures for the School Ego Map."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Literal

import duckdb
import pandas as pd  # type: ignore[import-untyped]
import plotly.graph_objects as go  # type: ignore[import-untyped]

from gisnet.visualization.geographic_flows import (
    FLOW_LINE_WIDTH_DEFINITIONS,
    FLOW_REGION_COLORS,
    calibrated_line_width,
    great_circle_arc_coordinates,
)

SchoolEgoLevel = Literal["institution", "country", "macro_region"]
SchoolEgoMetric = Literal["fractional_volume", "normalized_intensity", "persistence"]

_LEVELS = frozenset({"institution", "country", "macro_region"})
_METRICS = frozenset({"fractional_volume", "normalized_intensity", "persistence"})

EGO_METRIC_LABELS: dict[SchoolEgoMetric, str] = {
    "fractional_volume": "Fractional collaboration volume",
    "normalized_intensity": "Normalized collaboration intensity",
    "persistence": "Collaboration persistence",
}

EGO_METRIC_DEFINITIONS: dict[SchoolEgoMetric, str] = {
    "fractional_volume": (
        "Exact fractional collaboration weight for an institution partner, or its sum across "
        "the retained institution partners in a country or macro-region."
    ),
    "normalized_intensity": (
        "Institution view: exact fractional edge weight divided by the geometric mean of both "
        "endpoint Work counts. Country and macro-region views: fractional-weighted mean across "
        "the retained institution-partner intensities."
    ),
    "persistence": (
        "Institution view: exact active-period share under the selected temporal contract. "
        "Country and macro-region views: fractional-weighted mean across retained institution "
        "partners."
    ),
}

EGO_LINE_WIDTH_DEFINITIONS: dict[SchoolEgoMetric, str] = {
    "fractional_volume": FLOW_LINE_WIDTH_DEFINITIONS["volume"],
    "normalized_intensity": FLOW_LINE_WIDTH_DEFINITIONS["normalized_intensity"],
    "persistence": "width_px = 0.8 + 7.2 * sqrt(min(persistence, 1.0))",
}


@dataclass(frozen=True)
class SchoolEgoSelection:
    """Stable-ID and display controls for one exact ego result frame."""

    school_id: str
    corpus_view: str
    period_key: str
    level: SchoolEgoLevel
    metric: SchoolEgoMetric
    top_n: int = 12

    def __post_init__(self) -> None:
        if not self.school_id:
            raise ValueError("school_id cannot be empty")
        if self.corpus_view not in {"strict", "broad"}:
            raise ValueError("corpus_view must be strict or broad")
        if not self.period_key:
            raise ValueError("period_key cannot be empty")
        if self.level not in _LEVELS:
            raise ValueError(f"unsupported School Ego Map level: {self.level}")
        if self.metric not in _METRICS:
            raise ValueError(f"unsupported School Ego Map metric: {self.metric}")
        if not isinstance(self.top_n, int) or isinstance(self.top_n, bool) or self.top_n < 1:
            raise ValueError("top_n must be a positive integer")


def query_school_ego_partners(
    partner_index_path: str | Path,
    *,
    school_id: str,
    corpus_view: str,
    period_key: str,
) -> pd.DataFrame:
    """Read one school's retained partners with Parquet predicate pushdown."""
    selection = SchoolEgoSelection(
        school_id=school_id,
        corpus_view=corpus_view,
        period_key=period_key,
        level="institution",
        metric="fractional_volume",
    )
    source = Path(partner_index_path)
    if not source.is_file():
        raise ValueError(f"school ego partner index does not exist: {source}")
    connection = duckdb.connect()
    try:
        return connection.execute(
            """
            SELECT *
            FROM read_parquet(?)
            WHERE school_id = ? AND corpus_view = ? AND period_key = ?
            ORDER BY partner_rank, partner_id
            """,
            [
                str(source),
                selection.school_id,
                selection.corpus_view,
                selection.period_key,
            ],
        ).fetchdf()
    finally:
        connection.close()


def build_school_ego_view(
    partners: pd.DataFrame,
    anchors: pd.DataFrame,
    selection: SchoolEgoSelection,
) -> pd.DataFrame:
    """Build the exact institution or retained-partner geography frame used by map and table."""
    if partners.empty:
        return _empty_ego_view()
    _require_columns(
        partners,
        {
            "school_id",
            "school_name",
            "school_latitude",
            "school_longitude",
            "school_coordinate_source",
            "partner_id",
            "partner_name",
            "partner_country",
            "partner_country_name",
            "partner_macro_region",
            "partner_latitude",
            "partner_longitude",
            "partner_coordinate_source",
            "fractional_count",
            "normalized_intensity",
            "persistence",
            "full_count",
            "distinct_work_count",
            "source_work_count",
            "target_work_count",
            "period_key",
            "period_label",
            "time_basis",
            "persistence_definition",
        },
        "school ego partner rows",
    )
    scoped = partners.loc[
        (partners["school_id"].astype(str) == selection.school_id)
        & (partners["period_key"].astype(str) == selection.period_key)
        & (partners["corpus_view"].astype(str) == selection.corpus_view)
    ].copy()
    if scoped.empty:
        return _empty_ego_view()
    if scoped["school_id"].nunique() != 1:
        raise ValueError("school ego rows must contain exactly one source school")

    if selection.level == "institution":
        view = _institution_view(scoped)
    else:
        view = _geography_view(scoped, anchors, level=selection.level)
    if view.empty:
        return _empty_ego_view()

    metric_column = {
        "fractional_volume": "fractional_count",
        "normalized_intensity": "normalized_intensity",
        "persistence": "persistence",
    }[selection.metric]
    view["selected_value"] = view[metric_column].astype(float)
    if (~view["selected_value"].map(isfinite)).any() or (view["selected_value"] < 0).any():
        raise ValueError("School Ego Map selected values must be finite and nonnegative")
    view = (
        view.sort_values(
            ["selected_value", "target_name", "target_id"],
            ascending=[False, True, True],
            kind="stable",
        )
        .head(selection.top_n)
        .copy()
    )
    view["display_rank"] = range(1, len(view) + 1)
    if selection.metric == "fractional_volume":
        view["calibrated_width_px"] = view["selected_value"].map(
            lambda value: calibrated_line_width(float(value), "volume")
        )
    else:
        view["calibrated_width_px"] = view["selected_value"].map(
            lambda value: calibrated_line_width(float(value), "normalized_intensity")
        )
    view["metric"] = selection.metric
    view["metric_label"] = EGO_METRIC_LABELS[selection.metric]
    view["metric_definition"] = EGO_METRIC_DEFINITIONS[selection.metric]
    view["line_width_definition"] = EGO_LINE_WIDTH_DEFINITIONS[selection.metric]
    view["level"] = selection.level
    view["has_source_coordinates"] = (
        view[["source_latitude", "source_longitude"]].notna().all(axis=1)
    )
    view["has_target_coordinates"] = (
        view[["target_latitude", "target_longitude"]].notna().all(axis=1)
    )
    view["is_mappable"] = view["has_source_coordinates"] & view["has_target_coordinates"]
    return view.reset_index(drop=True)


def build_school_ego_map_figure(
    mapped_view: pd.DataFrame,
    selection: SchoolEgoSelection,
) -> go.Figure:
    """Render only the exact mapped rows supplied by the adjacent companion table."""
    figure = go.Figure()
    if mapped_view.empty:
        return figure
    if not mapped_view["is_mappable"].astype(bool).all():
        raise ValueError("school ego map input contains rows without sourced coordinates")
    for row in mapped_view.itertuples(index=False):
        longitudes, latitudes = great_circle_arc_coordinates(
            float(row.source_latitude),
            float(row.source_longitude),
            float(row.target_latitude),
            float(row.target_longitude),
        )
        hover = _hover_text(row)
        color = _region_color(str(row.target_macro_region))
        figure.add_trace(
            go.Scattergeo(
                lon=longitudes,
                lat=latitudes,
                mode="lines",
                line={"width": float(row.calibrated_width_px), "color": color},
                hovertext=[hover] * len(longitudes),
                customdata=[[row.target_id, row.selected_value]] * len(longitudes),
                hovertemplate="%{hovertext}<extra></extra>",
                showlegend=False,
                meta="school-ego-arc",
            )
        )
    region_order = {name: index for index, name in enumerate(FLOW_REGION_COLORS)}
    regions = sorted(
        {str(value) for value in mapped_view["target_macro_region"]},
        key=lambda value: (region_order.get(value, len(region_order)), value),
    )
    for region in regions:
        targets = mapped_view.loc[mapped_view["target_macro_region"].astype(str) == region]
        rows = list(targets.itertuples(index=False))
        figure.add_trace(
            go.Scattergeo(
                lon=targets["target_longitude"],
                lat=targets["target_latitude"],
                mode="markers",
                text=targets["target_name"],
                hovertext=[_hover_text(row) for row in rows],
                customdata=targets[["target_id", "selected_value"]].to_numpy(),
                marker={
                    "size": 10,
                    "color": _region_color(region),
                    "line": {"width": 0.9, "color": "white"},
                },
                hovertemplate="%{hovertext}<extra></extra>",
                name=f"Partner · {region}",
                legendgroup=f"school-ego-{region}",
                meta="school-ego-partner-markers",
            )
        )
    source = mapped_view.iloc[0]
    figure.add_trace(
        go.Scattergeo(
            lon=[source["source_longitude"]],
            lat=[source["source_latitude"]],
            mode="markers",
            text=[source["source_name"]],
            customdata=[[source["source_id"]]],
            marker={
                "size": 18,
                "color": _region_color(str(source["source_macro_region"])),
                "symbol": "diamond",
                "line": {"width": 1.8, "color": "#111827"},
            },
            hovertemplate=(
                f"Selected school<br>{source['source_name']}<br>Stable ID "
                f"{source['source_id']}<extra></extra>"
            ),
            name="Selected school",
            meta="school-ego-source-marker",
        )
    )
    figure.update_geos(
        projection_type="natural earth",
        showframe=False,
        showcoastlines=True,
        coastlinecolor="#94A3B8",
        showland=True,
        landcolor="#F8FAFC",
        showcountries=True,
        countrycolor="#CBD5E1",
    )
    figure.update_layout(
        title=f"{source['source_name']} · {source['period_label']}",
        legend={"title": {"text": "Partner macro-region"}},
        margin={"l": 10, "r": 10, "t": 55, "b": 10},
    )
    return figure


def _institution_view(scoped: pd.DataFrame) -> pd.DataFrame:
    return scoped.rename(
        columns={
            "school_id": "source_id",
            "school_name": "source_name",
            "school_country": "source_country",
            "school_macro_region": "source_macro_region",
            "school_subregion": "source_subregion",
            "school_latitude": "source_latitude",
            "school_longitude": "source_longitude",
            "school_coordinate_source": "source_coordinate_source",
            "partner_id": "target_id",
            "partner_name": "target_name",
            "partner_country": "target_country",
            "partner_country_name": "target_country_name",
            "partner_macro_region": "target_macro_region",
            "partner_subregion": "target_subregion",
            "partner_latitude": "target_latitude",
            "partner_longitude": "target_longitude",
            "partner_coordinate_source": "target_coordinate_source",
        }
    ).assign(institution_partner_count=1)


def _geography_view(
    scoped: pd.DataFrame,
    anchors: pd.DataFrame,
    *,
    level: Literal["country", "macro_region"],
) -> pd.DataFrame:
    _require_columns(
        anchors,
        {"geographic_level", "geography", "latitude", "longitude", "coordinate_source"},
        "geographic anchors",
    )
    group_id = "partner_country" if level == "country" else "partner_macro_region"
    group_name = "partner_country_name" if level == "country" else "partner_macro_region"
    known = scoped.loc[scoped[group_id].notna()].copy()
    if known.empty:
        return _empty_ego_view()
    known["normalized_weighted"] = known["normalized_intensity"].astype(float) * known[
        "fractional_count"
    ].astype(float)
    known["persistence_weighted"] = known["persistence"].astype(float) * known[
        "fractional_count"
    ].astype(float)
    source_columns = [
        "school_id",
        "school_name",
        "school_country",
        "school_macro_region",
        "school_subregion",
        "school_latitude",
        "school_longitude",
        "school_coordinate_source",
        "period_key",
        "period_label",
        "time_basis",
        "corpus_view",
        "persistence_definition",
    ]
    geography_groups = [group_id] if group_id == group_name else [group_id, group_name]
    grouped = (
        known.groupby([*source_columns, *geography_groups], as_index=False, dropna=False)
        .agg(
            full_count=("full_count", "sum"),
            fractional_count=("fractional_count", "sum"),
            distinct_work_count=("distinct_work_count", "sum"),
            source_work_count=("source_work_count", "first"),
            target_work_count=("target_work_count", "sum"),
            normalized_weighted=("normalized_weighted", "sum"),
            persistence_weighted=("persistence_weighted", "sum"),
            institution_partner_count=("partner_id", "nunique"),
        )
        .rename(
            columns={
                "school_id": "source_id",
                "school_name": "source_name",
                "school_country": "source_country",
                "school_macro_region": "source_macro_region",
                "school_subregion": "source_subregion",
                "school_latitude": "source_latitude",
                "school_longitude": "source_longitude",
                "school_coordinate_source": "source_coordinate_source",
            }
        )
    )
    grouped = grouped.rename(columns={group_id: "target_id"})
    if group_name == group_id:
        grouped["target_name"] = grouped["target_id"]
    else:
        grouped = grouped.rename(columns={group_name: "target_name"})
    denominator = grouped["fractional_count"].where(grouped["fractional_count"] > 0)
    grouped["normalized_intensity"] = grouped["normalized_weighted"].div(denominator)
    grouped["persistence"] = grouped["persistence_weighted"].div(denominator)
    grouped["target_country"] = grouped["target_id"] if level == "country" else None
    grouped["target_country_name"] = grouped["target_name"] if level == "country" else None
    if level == "macro_region":
        grouped["target_macro_region"] = grouped["target_id"]
    else:
        macro_by_country = known.groupby("partner_country", dropna=False)[
            "partner_macro_region"
        ].first()
        grouped["target_macro_region"] = grouped["target_id"].map(macro_by_country)
    grouped["target_subregion"] = None
    anchor_level = "country" if level == "country" else "macro_region"
    selected_anchors = anchors.loc[anchors["geographic_level"] == anchor_level].rename(
        columns={
            "geography": "target_id",
            "latitude": "target_latitude",
            "longitude": "target_longitude",
            "coordinate_source": "target_coordinate_source",
        }
    )
    return grouped.merge(
        selected_anchors[
            ["target_id", "target_latitude", "target_longitude", "target_coordinate_source"]
        ],
        on="target_id",
        how="left",
        validate="many_to_one",
    )


def _hover_text(row: Any) -> str:
    return (
        f"{row.source_name} → {row.target_name}<br>"
        f"Stable IDs: {row.source_id} → {row.target_id}<br>"
        f"Selected value: {_exact_number(row.selected_value)}<br>"
        f"Fractional volume: {_exact_number(row.fractional_count)}<br>"
        f"Normalized intensity: {_exact_number(row.normalized_intensity)}<br>"
        f"Persistence: {_exact_number(row.persistence)}<br>"
        f"Retained institution partners: {int(row.institution_partner_count)}"
    )


def _region_color(region: str) -> str:
    return FLOW_REGION_COLORS.get(region, FLOW_REGION_COLORS["Unknown"])


def _exact_number(value: Any) -> str:
    return repr(float(value))


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{label} lacks required columns: {missing}")


def _empty_ego_view() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "source_id",
            "source_name",
            "target_id",
            "target_name",
            "fractional_count",
            "normalized_intensity",
            "persistence",
            "selected_value",
            "source_latitude",
            "source_longitude",
            "target_latitude",
            "target_longitude",
            "is_mappable",
        ]
    )
