"""Reconciled query and figure helpers for the Geographic Flow Explorer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd  # type: ignore[import-untyped]
import plotly.graph_objects as go  # type: ignore[import-untyped]

GeographicLevel = Literal["macro_region", "subregion", "country"]
CountingMethod = Literal["fractional", "full"]
FlowMetric = Literal["volume", "partner_share", "normalized_intensity"]

_LEVELS = frozenset({"macro_region", "subregion", "country"})
_COUNTING_METHODS = frozenset({"fractional", "full"})
_METRICS = frozenset({"volume", "partner_share", "normalized_intensity"})

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
    source_row["counting_method"] = selection.counting_method
    source_row["start_year"] = selection.start_year
    source_row["end_year"] = selection.end_year
    source_row["window_label"] = _window_label(selection.start_year, selection.end_year)
    return source_row.sort_values(
        ["selected_value", "target_display_name", "target_geography"],
        ascending=[False, True, True],
        kind="stable",
    ).reset_index(drop=True)


def build_flow_map_figure(view: pd.DataFrame, selection: GeographicFlowSelection) -> go.Figure:
    """Render source-to-partner lines and sourced anchors from one exact flow view."""
    figure = go.Figure()
    if view.empty:
        return figure
    line_color = "rgba(0,114,178,0.42)"
    for row in view.loc[~view["is_internal"]].itertuples(index=False):
        hover = _hover_text(row)
        figure.add_trace(
            go.Scattergeo(
                lon=[row.source_longitude, row.target_longitude],
                lat=[row.source_latitude, row.target_latitude],
                mode="lines",
                line={"width": 1.25, "color": line_color},
                text=[hover, hover],
                customdata=[[row.target_geography, row.selected_value]] * 2,
                hovertemplate="%{text}<extra></extra>",
                showlegend=False,
            )
        )
    marker_customdata = view[
        [
            "target_geography",
            "selected_value",
            "full_count",
            "fractional_count",
            "partner_share",
            "normalized_intensity",
        ]
    ].to_numpy()
    figure.add_trace(
        go.Scattergeo(
            lon=view["target_longitude"],
            lat=view["target_latitude"],
            mode="markers",
            text=view["target_display_name"],
            customdata=marker_customdata,
            marker={
                "size": 11,
                "color": view["selected_value"],
                "colorscale": "Cividis",
                "showscale": True,
                "colorbar": {"title": {"text": METRIC_LABELS[selection.metric]}},
                "line": {"width": 0.7, "color": "white"},
            },
            hovertemplate=(
                "%{text}<br>ID %{customdata[0]}<br>Selected value %{customdata[1]:.6g}"
                "<br>Full volume %{customdata[2]:.6g}"
                "<br>Fractional volume %{customdata[3]:.6g}"
                "<br>Partner share %{customdata[4]:.3%}"
                "<br>Normalized intensity %{customdata[5]:.6g}<extra></extra>"
            ),
            name="Partner geography",
        )
    )
    source = view.iloc[0]
    internal = view.loc[view["is_internal"]]
    if internal.empty:
        source_hover = "Selected source<br>%{text}<br>No observed internal flow<extra></extra>"
        source_customdata = [[None, None, None, None]]
    else:
        internal_row = internal.iloc[0]
        source_hover = (
            "Selected source<br>%{text}<br>Internal selected value %{customdata[0]:.6g}"
            "<br>Internal full volume %{customdata[1]:.6g}"
            "<br>Internal fractional volume %{customdata[2]:.6g}"
            "<br>Internal partner share %{customdata[3]:.3%}<extra></extra>"
        )
        source_customdata = [
            [
                internal_row["selected_value"],
                internal_row["full_count"],
                internal_row["fractional_count"],
                internal_row["partner_share"],
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
                "color": "#D55E00",
                "symbol": "diamond",
                "line": {"width": 1.2, "color": "white"},
            },
            hovertemplate=source_hover,
            name="Selected source",
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
        title=(f"{source['source_display_name']} collaboration flows · {source['window_label']}")
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
            colorscale="Cividis",
            colorbar=colorbar,
            hovertemplate=(
                "Origin %{x}<br>Destination %{y}<br>ID %{customdata[0]}"
                "<br>Selected value %{customdata[1]:.6g}"
                "<br>Full volume %{customdata[2]:.6g}"
                "<br>Fractional volume %{customdata[3]:.6g}"
                "<br>Partner share %{customdata[4]:.3%}"
                "<br>Normalized intensity %{customdata[5]:.6g}<extra></extra>"
            ),
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
        f"Selected value: {float(row.selected_value):.6g}<br>"
        f"Full volume: {float(row.full_count):.6g}<br>"
        f"Fractional volume: {float(row.fractional_count):.6g}<br>"
        f"Partner share: {float(row.partner_share):.3%}<br>"
        f"Normalized intensity: {float(row.normalized_intensity):.6g}"
    )


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
            "source_display_name",
            "target_display_name",
            "source_latitude",
            "source_longitude",
            "target_latitude",
            "target_longitude",
        ]
    )
