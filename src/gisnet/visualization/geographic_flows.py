"""Reconciled query and figure helpers for the Geographic Flow Explorer."""

from __future__ import annotations

from dataclasses import dataclass
from math import acos, isfinite, log10, sqrt
from typing import Any, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import plotly.graph_objects as go  # type: ignore[import-untyped]
from numpy.typing import NDArray

GeographicLevel = Literal["macro_region", "subregion", "country"]
CountingMethod = Literal["fractional", "full"]
FlowMetric = Literal["volume", "partner_share", "normalized_intensity"]

_LEVELS = frozenset({"macro_region", "subregion", "country"})
_COUNTING_METHODS = frozenset({"fractional", "full"})
_METRICS = frozenset({"volume", "partner_share", "normalized_intensity"})

FLOW_REGION_COLORS: dict[str, str] = {
    "Europe": "#0072B2",
    "Asia": "#E69F00",
    "Americas": "#009E73",
    "Africa": "#CC79A7",
    "Oceania": "#56B4E9",
    "Other": "#6B7280",
    "Unknown": "#6B7280",
}
FLOW_LINE_WIDTH_MIN_PX = 0.8
FLOW_LINE_WIDTH_MAX_PX = 8.0
FLOW_LINE_WIDTH_RANGE_PX = FLOW_LINE_WIDTH_MAX_PX - FLOW_LINE_WIDTH_MIN_PX
FLOW_LINE_WIDTH_DEFINITIONS: dict[FlowMetric, str] = {
    "volume": ("width_px = min(8.0, 0.8 + 2.25 * log10(1 + selected collaboration weight))"),
    "partner_share": "width_px = 0.8 + 7.2 * sqrt(min(partner share, 1.0))",
    "normalized_intensity": ("width_px = 0.8 + 7.2 * sqrt(min(normalized intensity, 1.0))"),
}

METRIC_LABELS: dict[FlowMetric, str] = {
    "volume": "Collaboration volume",
    "partner_share": "Partner share",
    "normalized_intensity": "Normalized intensity",
}

METRIC_DEFINITIONS: dict[FlowMetric, str] = {
    "volume": (
        "Selected full or fractional collaboration weight summed over the inclusive complete-year "
        "window."
    ),
    "partner_share": (
        "Selected endpoint weight divided by all selected endpoint weight attached to the source "
        "geography; an internal flow contributes two source endpoints."
    ),
    "normalized_intensity": (
        "Fractional collaboration weight divided by the geometric mean of source and target "
        "full institutional Work-count denominators over the same corpus, hierarchy, and window."
    ),
}


@dataclass(frozen=True)
class GeographicFlowSelection:
    """Exact controls shared by geographic map and matrix modes."""

    geographic_level: GeographicLevel
    source_geography: str
    start_year: int
    end_year: int
    corpus_view: str
    hierarchy_view: str
    counting_method: CountingMethod
    metric: FlowMetric

    def __post_init__(self) -> None:
        if self.geographic_level not in _LEVELS:
            raise ValueError(f"unsupported geographic level: {self.geographic_level}")
        if self.counting_method not in _COUNTING_METHODS:
            raise ValueError(f"unsupported counting method: {self.counting_method}")
        if self.metric not in _METRICS:
            raise ValueError(f"unsupported flow metric: {self.metric}")
        if not self.source_geography:
            raise ValueError("source geography cannot be empty")
        if self.start_year > self.end_year:
            raise ValueError("start_year must be less than or equal to end_year")


@dataclass(frozen=True)
class FlowDisplayPolicy:
    """Deterministic display-only filters for selected-source flows."""

    top_n: int = 12
    minimum_weight: float = 0.0
    minimum_partner_share: float = 0.0

    def __post_init__(self) -> None:
        if self.top_n < 1:
            raise ValueError("top_n must be at least 1")
        if not isfinite(self.minimum_weight) or self.minimum_weight < 0:
            raise ValueError("minimum_weight must be a finite nonnegative number")
        if (
            not isfinite(self.minimum_partner_share)
            or self.minimum_partner_share < 0
            or self.minimum_partner_share > 1
        ):
            raise ValueError("minimum_partner_share must be between 0 and 1")


def flow_source_options(
    flows: pd.DataFrame,
    anchors: pd.DataFrame,
    *,
    geographic_level: GeographicLevel,
    start_year: int,
    end_year: int,
    corpus_view: str,
    hierarchy_view: str,
) -> list[tuple[str, str]]:
    """Return stable ``(geography, display label)`` source choices with observed flows."""
    _require_columns(
        flows,
        {
            "year",
            "corpus_view",
            "hierarchy_view",
            "geographic_level",
            "source_geography",
            "target_geography",
        },
        "flow matrix",
    )
    _require_columns(
        anchors,
        {"geographic_level", "geography", "display_name"},
        "geographic anchors",
    )
    selected = flows.loc[
        flows["year"].between(start_year, end_year)
        & (flows["corpus_view"] == corpus_view)
        & (flows["hierarchy_view"] == hierarchy_view)
        & (flows["geographic_level"] == geographic_level)
    ]
    values = set(str(value) for value in selected["source_geography"])
    values.update(str(value) for value in selected["target_geography"])
    labels = anchors.loc[
        (anchors["geographic_level"] == geographic_level)
        & anchors["geography"].astype(str).isin(values),
        ["geography", "display_name"],
    ]
    label_map = {
        str(row.geography): str(row.display_name) for row in labels.itertuples(index=False)
    }
    return sorted(
        ((value, label_map.get(value, value)) for value in values),
        key=lambda item: (item[1].casefold(), item[0]),
    )


def build_flow_view(
    flows: pd.DataFrame,
    geography_outputs: pd.DataFrame,
    anchors: pd.DataFrame,
    selection: GeographicFlowSelection,
) -> pd.DataFrame:
    """Build one exact directed source row used by both explorer modes."""
    _require_columns(
        flows,
        {
            "year",
            "corpus_view",
            "hierarchy_view",
            "geographic_level",
            "source_geography",
            "target_geography",
            "full_count",
            "fractional_count",
        },
        "flow matrix",
    )
    _require_columns(
        geography_outputs,
        {
            "year",
            "corpus_view",
            "hierarchy_view",
            "geographic_level",
            "geography",
            "full_work_count",
        },
        "geography outputs",
    )
    _require_columns(
        anchors,
        {
            "geographic_level",
            "geography",
            "display_name",
            "macro_region",
            "latitude",
            "longitude",
            "anchor_method",
            "coordinate_source",
            "coordinate_license",
            "coordinate_license_url",
            "source_dataset_sha256",
        },
        "geographic anchors",
    )

    selected_flows = flows.loc[
        flows["year"].between(selection.start_year, selection.end_year)
        & (flows["corpus_view"] == selection.corpus_view)
        & (flows["hierarchy_view"] == selection.hierarchy_view)
        & (flows["geographic_level"] == selection.geographic_level)
    ].copy()
    if selected_flows.empty:
        return _empty_flow_view()
    aggregated = (
        selected_flows.groupby(["source_geography", "target_geography"], as_index=False, sort=True)[
            ["full_count", "fractional_count"]
        ]
        .sum()
        .sort_values(["source_geography", "target_geography"], kind="stable")
    )
    cross = aggregated.loc[aggregated["source_geography"] != aggregated["target_geography"]].copy()
    reverse = cross.rename(
        columns={
            "source_geography": "target_geography",
            "target_geography": "source_geography",
        }
    )
    directed = pd.concat([aggregated, reverse], ignore_index=True)
    directed["is_internal"] = directed["source_geography"] == directed["target_geography"]

    selected_outputs = geography_outputs.loc[
        geography_outputs["year"].between(selection.start_year, selection.end_year)
        & (geography_outputs["corpus_view"] == selection.corpus_view)
        & (geography_outputs["hierarchy_view"] == selection.hierarchy_view)
        & (geography_outputs["geographic_level"] == selection.geographic_level)
    ]
    denominators = selected_outputs.groupby("geography", sort=True)["full_work_count"].sum()
    directed["source_full_work_count"] = directed["source_geography"].map(denominators)
    directed["target_full_work_count"] = directed["target_geography"].map(denominators)
    if directed[["source_full_work_count", "target_full_work_count"]].isna().any().any():
        missing = sorted(
            set(
                directed.loc[directed["source_full_work_count"].isna(), "source_geography"].astype(
                    str
                )
            )
            | set(
                directed.loc[directed["target_full_work_count"].isna(), "target_geography"].astype(
                    str
                )
            )
        )
        raise ValueError(f"geographic flow denominators are missing for: {missing}")
    denominator = np.sqrt(
        directed["source_full_work_count"].astype(float)
        * directed["target_full_work_count"].astype(float)
    )
    directed["normalized_intensity"] = (
        directed["fractional_count"].astype(float).div(denominator.where(denominator > 0))
    )

    weight_column = (
        "fractional_count" if selection.counting_method == "fractional" else "full_count"
    )
    directed["selected_weight"] = directed[weight_column].astype(float)
    directed["source_endpoint_weight"] = directed["selected_weight"]
    directed.loc[directed["is_internal"], "source_endpoint_weight"] *= 2.0
    directed["total_source_endpoint_weight"] = directed.groupby("source_geography", sort=False)[
        "source_endpoint_weight"
    ].transform("sum")
    directed["partner_share"] = directed["source_endpoint_weight"].div(
        directed["total_source_endpoint_weight"].where(directed["total_source_endpoint_weight"] > 0)
    )
    metric_column = {
        "volume": "selected_weight",
        "partner_share": "partner_share",
        "normalized_intensity": "normalized_intensity",
    }[selection.metric]
    directed["selected_value"] = directed[metric_column].astype(float)
    source_row = directed.loc[directed["source_geography"] == selection.source_geography].copy()
    if source_row.empty:
        return _empty_flow_view()

    selected_anchors = anchors.loc[anchors["geographic_level"] == selection.geographic_level].copy()
    if selected_anchors["geography"].duplicated().any():
        raise ValueError("geographic anchors must be unique by level and geography")
    source_anchor = selected_anchors.add_prefix("source_")
    target_anchor = selected_anchors.add_prefix("target_")
    source_row = source_row.merge(
        source_anchor,
        left_on="source_geography",
        right_on="source_geography",
        how="left",
        validate="many_to_one",
    ).merge(
        target_anchor,
        left_on="target_geography",
        right_on="target_geography",
        how="left",
        validate="many_to_one",
    )
    coordinate_columns = [
        "source_latitude",
        "source_longitude",
        "target_latitude",
        "target_longitude",
    ]
    if source_row[coordinate_columns].isna().any().any():
        missing = sorted(
            set(
                source_row.loc[
                    source_row[["source_latitude", "source_longitude"]].isna().any(axis=1),
                    "source_geography",
                ].astype(str)
            )
            | set(
                source_row.loc[
                    source_row[["target_latitude", "target_longitude"]].isna().any(axis=1),
                    "target_geography",
                ].astype(str)
            )
        )
        raise ValueError(f"sourced geographic anchors are missing for: {missing}")
    source_row["metric"] = selection.metric
    source_row["metric_label"] = METRIC_LABELS[selection.metric]
    source_row["metric_definition"] = METRIC_DEFINITIONS[selection.metric]
    source_row["line_width_definition"] = FLOW_LINE_WIDTH_DEFINITIONS[selection.metric]
    source_row["counting_method"] = selection.counting_method
    source_row["start_year"] = selection.start_year
    source_row["end_year"] = selection.end_year
    source_row["window_label"] = _window_label(selection.start_year, selection.end_year)
    source_row["calibrated_width_px"] = source_row["selected_value"].map(
        lambda value: calibrated_line_width(float(value), selection.metric)
    )
    return source_row.sort_values(
        ["selected_value", "target_display_name", "target_geography"],
        ascending=[False, True, True],
        kind="stable",
    ).reset_index(drop=True)


def calibrated_line_width(value: float, metric: FlowMetric) -> float:
    """Map one exact metric value to a filter-invariant display width in pixels."""
    if metric not in _METRICS:
        raise ValueError(f"unsupported flow metric: {metric}")
    if not isfinite(value) or value < 0:
        raise ValueError("flow width value must be a finite nonnegative number")
    if metric == "volume":
        return min(FLOW_LINE_WIDTH_MAX_PX, FLOW_LINE_WIDTH_MIN_PX + 2.25 * log10(1 + value))
    bounded = min(value, 1.0)
    return FLOW_LINE_WIDTH_MIN_PX + FLOW_LINE_WIDTH_RANGE_PX * sqrt(bounded)


def filter_readable_flows(view: pd.DataFrame, policy: FlowDisplayPolicy) -> pd.DataFrame:
    """Apply thresholds, then retain the deterministic Top N cross-geography flows.

    Internal flow is not an arc and therefore does not consume a Top N slot. It remains in the
    exact result frame only when it passes the same weight and partner-share thresholds.
    """
    if view.empty:
        return view.copy()
    _require_columns(
        view,
        {
            "target_geography",
            "target_display_name",
            "is_internal",
            "selected_weight",
            "partner_share",
            "selected_value",
            "calibrated_width_px",
        },
        "geographic flow view",
    )
    eligible = view.loc[
        (view["selected_weight"].astype(float) >= policy.minimum_weight)
        & (view["partner_share"].astype(float) >= policy.minimum_partner_share)
    ].copy()
    cross = (
        eligible.loc[~eligible["is_internal"].astype(bool)]
        .sort_values(
            ["selected_value", "target_display_name", "target_geography"],
            ascending=[False, True, True],
            kind="stable",
        )
        .head(policy.top_n)
        .copy()
    )
    cross["display_rank"] = range(1, len(cross) + 1)
    internal = eligible.loc[eligible["is_internal"].astype(bool)].copy()
    internal["display_rank"] = 0
    displayed = pd.concat([cross, internal], ignore_index=True)
    if displayed.empty:
        return displayed
    displayed["display_top_n"] = policy.top_n
    displayed["display_minimum_weight"] = policy.minimum_weight
    displayed["display_minimum_partner_share"] = policy.minimum_partner_share
    return displayed.sort_values(
        ["is_internal", "display_rank", "target_display_name", "target_geography"],
        ascending=[True, True, True, True],
        kind="stable",
    ).reset_index(drop=True)


def build_flow_map_figure(view: pd.DataFrame, selection: GeographicFlowSelection) -> go.Figure:
    """Render calibrated great-circle arcs and region-emphasized sourced anchors."""
    figure = go.Figure()
    if view.empty:
        return figure
    for row in view.loc[~view["is_internal"]].itertuples(index=False):
        longitudes, latitudes = great_circle_arc_coordinates(
            float(row.source_latitude),
            float(row.source_longitude),
            float(row.target_latitude),
            float(row.target_longitude),
        )
        hover = _hover_text(row)
        line_color = _region_color(str(row.target_macro_region))
        figure.add_trace(
            go.Scattergeo(
                lon=longitudes,
                lat=latitudes,
                mode="lines",
                line={"width": float(row.calibrated_width_px), "color": line_color},
                hovertext=[hover] * len(longitudes),
                customdata=[[row.target_geography, row.selected_value]] * len(longitudes),
                hovertemplate="%{hovertext}<extra></extra>",
                showlegend=False,
                meta="flow-arc",
            )
        )
    macro_order = {name: index for index, name in enumerate(FLOW_REGION_COLORS)}
    partner_regions = sorted(
        {str(value) for value in view["target_macro_region"]},
        key=lambda value: (macro_order.get(value, len(macro_order)), value),
    )
    for region in partner_regions:
        partners = view.loc[view["target_macro_region"].astype(str) == region]
        partner_rows = list(partners.itertuples(index=False))
        marker_mode = "markers+text" if selection.geographic_level == "macro_region" else "markers"
        figure.add_trace(
            go.Scattergeo(
                lon=partners["target_longitude"],
                lat=partners["target_latitude"],
                mode=marker_mode,
                text=[_visible_target_label(row, selection.metric) for row in partner_rows],
                textposition="top center",
                textfont={"size": 11, "color": "#111827"},
                hovertext=[_hover_text(row) for row in partner_rows],
                customdata=partners[
                    [
                        "target_geography",
                        "selected_value",
                        "full_count",
                        "fractional_count",
                        "partner_share",
                        "normalized_intensity",
                        "calibrated_width_px",
                    ]
                ].to_numpy(),
                marker={
                    "size": 11,
                    "color": _region_color(region),
                    "line": {"width": 0.9, "color": "white"},
                },
                hovertemplate="%{hovertext}<extra></extra>",
                name=f"Partner · {region}",
                legendgroup=f"partner-{region}",
                meta="flow-partner-markers",
            )
        )
    source = view.iloc[0]
    internal = view.loc[view["is_internal"]]
    if internal.empty:
        source_hover = (
            f"Selected source<br>{source['source_display_name']}"
            f"<br>ID {source['source_geography']}<br>No displayed internal flow"
        )
        source_customdata = [[None, None, None, None, None]]
    else:
        internal_row = internal.iloc[0]
        source_hover = (
            f"Selected source<br>{source['source_display_name']}"
            f"<br>ID {source['source_geography']}"
            f"<br>Internal selected value {_exact_number(internal_row['selected_value'])}"
            f"<br>Internal full volume {_exact_number(internal_row['full_count'])}"
            f"<br>Internal fractional volume {_exact_number(internal_row['fractional_count'])}"
            f"<br>Internal partner share {_exact_number(internal_row['partner_share'])} "
            f"({float(internal_row['partner_share']):.3%})"
            f"<br>Normalized intensity {_exact_number(internal_row['normalized_intensity'])}"
        )
        source_customdata = [
            [
                internal_row["selected_value"],
                internal_row["full_count"],
                internal_row["fractional_count"],
                internal_row["partner_share"],
                internal_row["normalized_intensity"],
            ]
        ]
    figure.add_trace(
        go.Scattergeo(
            lon=[source["source_longitude"]],
            lat=[source["source_latitude"]],
            mode="markers",
            text=[source["source_display_name"]],
            customdata=source_customdata,
            marker={
                "size": 17,
                "color": _region_color(str(source["source_macro_region"])),
                "symbol": "diamond",
                "line": {"width": 1.8, "color": "#111827"},
            },
            hovertemplate=f"{source_hover}<extra></extra>",
            name="Selected source",
            meta="flow-source-marker",
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
        title=(f"{source['source_display_name']} collaboration flows · {source['window_label']}"),
        legend={"title": {"text": "Target macro-region"}},
        margin={"l": 10, "r": 10, "t": 55, "b": 10},
    )
    return figure


def build_flow_matrix_figure(view: pd.DataFrame, selection: GeographicFlowSelection) -> go.Figure:
    """Render the exact selected-origin matrix row used by the flow map."""
    figure = go.Figure()
    if view.empty:
        return figure
    ordered = view.sort_values(
        ["target_display_name", "target_geography"], kind="stable"
    ).reset_index(drop=True)
    values = ordered["selected_value"].astype(float).tolist()
    customdata = np.array(
        [
            [
                row.target_geography,
                row.selected_value,
                row.full_count,
                row.fractional_count,
                row.partner_share,
                row.normalized_intensity,
            ]
            for row in ordered.itertuples(index=False)
        ],
        dtype=object,
    ).reshape(len(ordered), 1, 6)
    hovertext = np.array(
        [[_hover_text(row)] for row in ordered.itertuples(index=False)], dtype=object
    )
    colorbar = {"title": {"text": METRIC_LABELS[selection.metric]}}
    heatmap_kwargs: dict[str, object] = {}
    if selection.metric == "partner_share":
        heatmap_kwargs = {"zmin": 0.0, "zmax": 1.0}
    figure.add_trace(
        go.Heatmap(
            z=[[value] for value in values],
            x=[str(ordered.iloc[0]["source_display_name"])],
            y=ordered["target_display_name"].astype(str).tolist(),
            customdata=customdata,
            hovertext=hovertext,
            colorscale="Cividis",
            colorbar=colorbar,
            hovertemplate="%{hovertext}<extra></extra>",
            **heatmap_kwargs,
        )
    )
    figure.update_layout(
        title=(f"Origin-destination matrix row · {ordered.iloc[0]['window_label']}"),
        xaxis_title="Selected source geography",
        yaxis_title="Partner geography",
    )
    return figure


def _hover_text(row: Any) -> str:
    return (
        f"{row.source_display_name} → {row.target_display_name}<br>"
        f"IDs: {row.source_geography} → {row.target_geography}<br>"
        f"Selected value: {_exact_number(row.selected_value)}<br>"
        f"Full volume: {_exact_number(row.full_count)}<br>"
        f"Fractional volume: {_exact_number(row.fractional_count)}<br>"
        f"Partner share: {_exact_number(row.partner_share)} ({float(row.partner_share):.3%})<br>"
        f"Normalized intensity: {_exact_number(row.normalized_intensity)}<br>"
        f"Calibrated width: {_exact_number(row.calibrated_width_px)} px"
    )


def great_circle_arc_coordinates(
    source_latitude: float,
    source_longitude: float,
    target_latitude: float,
    target_longitude: float,
    *,
    point_count: int = 32,
) -> tuple[list[float], list[float]]:
    """Return deterministic spherical interpolation points between two sourced anchors."""
    coordinates = [source_latitude, source_longitude, target_latitude, target_longitude]
    if not all(isfinite(value) for value in coordinates):
        raise ValueError("arc coordinates must be finite")
    if not -90 <= source_latitude <= 90 or not -90 <= target_latitude <= 90:
        raise ValueError("arc latitude must be between -90 and 90")
    if not -180 <= source_longitude <= 180 or not -180 <= target_longitude <= 180:
        raise ValueError("arc longitude must be between -180 and 180")
    if point_count < 2:
        raise ValueError("point_count must be at least 2")

    def unit_vector(latitude: float, longitude: float) -> NDArray[np.float64]:
        latitude_radians = np.radians(latitude)
        longitude_radians = np.radians(longitude)
        return np.array(
            [
                np.cos(latitude_radians) * np.cos(longitude_radians),
                np.cos(latitude_radians) * np.sin(longitude_radians),
                np.sin(latitude_radians),
            ],
            dtype=float,
        )

    source = unit_vector(source_latitude, source_longitude)
    target = unit_vector(target_latitude, target_longitude)
    angle = acos(float(np.clip(np.dot(source, target), -1.0, 1.0)))
    fractions = np.linspace(0.0, 1.0, point_count)
    if angle < 1e-12 or abs(np.sin(angle)) < 1e-12:
        latitudes = np.linspace(source_latitude, target_latitude, point_count)
        longitudes = np.linspace(source_longitude, target_longitude, point_count)
        return longitudes.tolist(), latitudes.tolist()
    sin_angle = np.sin(angle)
    vectors = np.array(
        [
            (np.sin((1.0 - fraction) * angle) / sin_angle) * source
            + (np.sin(fraction * angle) / sin_angle) * target
            for fraction in fractions
        ]
    )
    latitudes = np.degrees(np.arctan2(vectors[:, 2], np.hypot(vectors[:, 0], vectors[:, 1])))
    longitudes = np.degrees(np.unwrap(np.arctan2(vectors[:, 1], vectors[:, 0])))
    return longitudes.tolist(), latitudes.tolist()


def _visible_target_label(row: Any, metric: FlowMetric) -> str:
    value = float(row.selected_value)
    if metric == "partner_share":
        formatted = f"{value:.1%}"
    elif metric == "volume":
        formatted = f"{value:,.3g}"
    else:
        formatted = f"{value:.3g}"
    return f"{row.target_display_name}<br>{formatted}"


def _region_color(region: str) -> str:
    return FLOW_REGION_COLORS.get(region, FLOW_REGION_COLORS["Unknown"])


def _exact_number(value: Any) -> str:
    return repr(float(value))


def _window_label(start_year: int, end_year: int) -> str:
    return str(start_year) if start_year == end_year else f"{start_year}-{end_year}"


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{label} lacks required columns: {missing}")


def _empty_flow_view() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "source_geography",
            "target_geography",
            "full_count",
            "fractional_count",
            "partner_share",
            "normalized_intensity",
            "selected_value",
            "selected_weight",
            "calibrated_width_px",
            "source_display_name",
            "target_display_name",
            "source_macro_region",
            "target_macro_region",
            "source_latitude",
            "source_longitude",
            "target_latitude",
            "target_longitude",
        ]
    )
