"""Shared Streamlit and Plotly presentation components."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

REGION_COLORS = {
    "Europe": "#0072B2",
    "Asia": "#E69F00",
    "Americas": "#009E73",
    "Africa": "#CC79A7",
    "Oceania": "#56B4E9",
    "Other": "#6B7280",
    "Unknown": "#6B7280",
}
CATEGORY_COLORS = (
    "#0072B2",
    "#E69F00",
    "#009E73",
    "#CC79A7",
    "#56B4E9",
    "#D55E00",
    "#332288",
    "#88CCEE",
    "#44AA99",
    "#999933",
    "#882255",
    "#661100",
    "#6699CC",
)
SHARE_SCALE = px.colors.sequential.Cividis
PLOT_CONFIG = {
    "displaylogo": False,
    "responsive": True,
    "modeBarButtonsToRemove": ["lasso2d", "select2d"],
}
HUMAN_LABELS = {
    "fractional_strength": "Fractional strength",
    "macro_region": "Macro-region",
    "community_id": "Annual community",
    "continuity_id": "Continuity community",
    "pagerank": "PageRank",
    "work_count": "Works",
    "full_count": "Full count",
    "fractional_count": "Fractional count",
    "normalized_intensity": "Normalized intensity",
    "persistence_3y": "Persistence (3y)",
    "persistence_5y": "Persistence (5y)",
    "topic_families": "Topic families",
    "work_ids_sample": "Supporting Work IDs",
    "display_rank": "Rank",
    "target_id": "Stable partner ID",
    "target_name": "Partner",
    "target_country_name": "Partner country",
    "target_macro_region": "Partner macro-region",
    "selected_value": "Selected exact value",
    "institution_partner_count": "Retained institution partners",
    "window_months": "Rolling window (months)",
    "window_label": "Rolling horizon",
    "topic_rank": "Topic rank",
    "topic_family_share": "Topic share",
    "specialization_lift_global": "Lift vs global",
    "specialization_lift_macro_region": "Lift vs macro-region",
    "specialization_lift_country": "Lift vs country",
    "contributing_work_count": "Contributing Works",
    "proximity_rank": "Research-proximity rank",
    "index_match_status": "Stable-ID lookup status",
    "profile_row_status": "Profile row status",
    "full_work_count": "Works in selected window",
    "fractional_work_count": "Fractional Works",
    "international_collaboration_share": "International share",
    "cross_region_collaboration_share": "Cross-region share",
    "partner_institution_count": "Distinct institution partners",
    "partner_country_count": "Distinct partner countries",
    "effective_partner_count": "Effective partner count",
    "repeat_partner_ratio": "Repeat-partner ratio",
    "rolling_12m_activity_change": "Rolling 12m activity change",
    "rolling_12m_fractional_activity_change": "Rolling 12m fractional activity change",
    "degree": "Degree",
    "betweenness": "Betweenness",
    "bridge_score": "Bridge score",
    "citation_flow_in_full": "Incoming citation flow · full",
    "citation_flow_in_fractional": "Incoming citation flow · fractional",
    "citation_flow_out_full": "Outgoing citation flow · full",
    "citation_flow_out_fractional": "Outgoing citation flow · fractional",
    "institution_resolved_share": "Institution-resolved reference share",
    "reference_count": "References in coverage denominator",
    "institution_resolved_reference_count": "Institution-resolved references",
    "negative_lag_reference_count": "Negative-lag references",
    "public_edge_rank": "Public edge rank",
    "public_edge_limit": "Public edge limit per view",
    "public_selection_policy": "Public edge-selection policy",
    "is_institution_self_flow": "Institution self-flow",
    "negative_lag_full_count": "Negative-lag full count",
    "minimum_citation_lag_years": "Minimum citation lag (years)",
    "maximum_citation_lag_years": "Maximum citation lag (years)",
    "cosine_similarity": "Topic-profile cosine similarity",
    "shared_topic_count": "Shared provisional Topics",
    "source_neighbor_rank": "Source-neighbour rank",
    "target_neighbor_rank": "Target-neighbour rank",
    "vector_coverage_share": "Topic-vector coverage",
    "core_coverage_share": "Similarity-core coverage",
    "selected_core_institution_count": "Selected similarity-core institutions",
    "selected_similarity_edge_count": "Selected similarity edges",
    "maximum_institutions_per_view": "Maximum institutions per view",
    "top_k": "Top neighbours per institution",
    "minimum_similarity": "Source minimum similarity",
    "edge_selection_policy": "Source edge-selection policy",
    "layer_semantics": "Layer semantics",
    "directionality": "Directionality",
    "coverage_scope": "Coverage scope",
    "weight_semantics": "Weight semantics",
    "composite_weight_defined": "Composite weight defined",
    "comparison_boundary": "Comparison boundary",
}


def show_empty(message: str) -> None:
    st.info(f"No data match this filter combination. {message}")


def metric_text(row: pd.Series | None, column: str, format_spec: str) -> str:
    if row is None or column not in row or pd.isna(row[column]):
        return "N/A"
    return format(float(row[column]), format_spec)


def human_label(value: object) -> str:
    text = str(value)
    return HUMAN_LABELS.get(text, text.replace("_", " ").capitalize())


def category_color_map(values: pd.Series) -> dict[object, str]:
    labels = sorted(values.dropna().unique(), key=lambda value: str(value))
    return {
        label: CATEGORY_COLORS[index % len(CATEGORY_COLORS)] for index, label in enumerate(labels)
    }


def comparison_color_map(values: pd.Series) -> dict[str, str]:
    labels = sorted(str(value) for value in values.dropna().unique())
    return {
        label: REGION_COLORS.get(label.split(" → ", maxsplit=1)[0], CATEGORY_COLORS[index])
        for index, label in enumerate(labels)
    }


def style_figure(
    figure: go.Figure,
    *,
    height: int = 460,
    time_series: bool = False,
    cartesian: bool = True,
) -> go.Figure:
    figure.update_layout(
        template="plotly_white",
        colorway=list(CATEGORY_COLORS),
        height=height,
        margin={"l": 24, "r": 24, "t": 64, "b": 36},
        font={"color": "#0F172A", "size": 13},
        title={"font": {"size": 19}, "x": 0.0, "xanchor": "left"},
        legend={
            "orientation": "h",
            "yanchor": "bottom",
            "y": 1.02,
            "xanchor": "left",
            "x": 0,
            "title": None,
        },
        hoverlabel={"bgcolor": "white", "font": {"color": "#0F172A"}},
        hovermode="x unified" if time_series else "closest",
    )
    if cartesian:
        figure.update_xaxes(showgrid=False, linecolor="#CBD5E1", tickfont={"color": "#475569"})
        figure.update_yaxes(
            showgrid=True,
            gridcolor="#E2E8F0",
            zeroline=False,
            linecolor="#CBD5E1",
            tickfont={"color": "#475569"},
        )
    return figure


def show_chart(
    figure: go.Figure,
    *,
    height: int = 460,
    time_series: bool = False,
    cartesian: bool = True,
) -> None:
    st.plotly_chart(
        style_figure(figure, height=height, time_series=time_series, cartesian=cartesian),
        width="stretch",
        config=PLOT_CONFIG,
    )


def show_data(frame: pd.DataFrame, *, columns: list[str] | None = None) -> None:
    view = frame.loc[:, columns].copy() if columns is not None else frame.copy()
    st.dataframe(
        view.rename(columns={column: human_label(column) for column in view.columns}),
        width="stretch",
        hide_index=True,
    )
