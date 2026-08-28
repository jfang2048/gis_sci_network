"""Processed-data-only Streamlit dashboard for the GIS collaboration network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from gisnet.visualization.dashboard_filters import (
    control_is_enabled,
    dimension_options,
    filter_geographic_view,
    partner_share_view,
)
from gisnet.visualization.geographic_flows import (
    METRIC_DEFINITIONS,
    CountingMethod,
    FlowMetric,
    GeographicFlowSelection,
    GeographicLevel,
    build_flow_map_figure,
    build_flow_matrix_figure,
    build_flow_view,
    flow_source_options,
)
from gisnet.visualization.network_view import visible_accessibility_sentence
from gisnet.visualization.pair_explorer import (
    build_pair_timeline,
    identity_rows,
    institution_labels,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "dashboard" / "data"
PAGES = (
    "Overview",
    "Region trends",
    "Geographic flows",
    "Institutional network",
    "Institution explorer",
    "Topic-family comparison",
    "Methods and limitations",
    "Data quality",
)
PAGE_DETAILS = {
    "Overview": (
        "A global view of GIS collaboration",
        "Scale, structure, and regional orientation in the selected complete year.",
    ),
    "Region trends": (
        "Regional collaboration over time",
        "Partner composition and exact region flows across complete calendar years.",
    ),
    "Geographic flows": (
        "Geographic Flow Explorer",
        "Select one geography and trace its exact collaboration volume, share, or intensity.",
    ),
    "Institutional network": (
        "The institutional collaboration core",
        "A fixed layout for comparing structure without year-to-year position changes.",
    ),
    "Institution explorer": (
        "Trace one institutional partnership",
        "Counts, intensity, persistence, Topic families, and stable identities over time.",
    ),
    "Topic-family comparison": (
        "How methodological families connect institutions",
        "Annual collaboration weight within the thresholded fixed-layout core.",
    ),
    "Methods and limitations": (
        "How to read these results",
        "Definitions, interpretation boundaries, provisional decisions, and geographic "
        "conventions.",
    ),
    "Data quality": (
        "Evidence behind the snapshot",
        "Sensitivity, coverage, continuity, versions, and integrity metadata.",
    ),
}

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
}

st.set_page_config(
    page_title="GIS Scientific Collaboration Network",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="auto",
)


@st.cache_data(show_spinner=False)
def load_metadata() -> dict[str, object]:
    path = DATA / "metadata.json"
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


@st.cache_resource(show_spinner=False)
def load_table(name: str) -> pd.DataFrame:
    path = DATA / f"{name}.parquet"
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_parquet(path)


def require_table(name: str, *, columns: set[str] | None = None) -> pd.DataFrame:
    """Load a required snapshot table and fail explicitly when its contract is incomplete."""
    frame = load_table(name)
    if frame.empty:
        st.error(
            f"The dashboard snapshot is incomplete: `{name}.parquet` is missing or empty. "
            "Rebuild the processed dashboard bundle."
        )
        st.stop()
    missing = sorted((columns or set()).difference(frame.columns))
    if missing:
        st.error(
            f"The dashboard snapshot is incompatible: `{name}.parquet` lacks "
            f"{', '.join(missing)}. Rebuild the processed dashboard bundle."
        )
        st.stop()
    return frame


def filtered_view(frame: pd.DataFrame, year: int, corpus: str, hierarchy: str) -> pd.DataFrame:
    if frame.empty:
        return frame
    mask = (
        (frame["year"] == year)
        & (frame["corpus_view"] == corpus)
        & (frame["hierarchy_view"] == hierarchy)
    )
    return frame.loc[mask].copy()


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


def region_comparison_rows(
    frame: pd.DataFrame,
    *,
    weight_column: str,
    region_pair: str,
) -> pd.DataFrame:
    """Return comparable directional shares for the selected macro-region view."""
    normalized = frame.rename(
        columns={"source_region": "source_geography", "target_region": "target_geography"}
    )
    directed = partner_share_view(normalized, weight_column=weight_column)
    if directed.empty:
        return directed
    if region_pair == "All":
        selected = directed.loc[
            directed["is_local"] & directed["source_geography"].isin(("Europe", "Asia", "Americas"))
        ].copy()
        selected["comparison"] = selected["source_geography"]
        return selected
    source, target = region_pair.split(" — ", maxsplit=1)
    if source == target:
        selected = directed.loc[
            (directed["source_geography"] == source) & (directed["target_geography"] == target)
        ].copy()
    else:
        selected = directed.loc[
            ((directed["source_geography"] == source) & (directed["target_geography"] == target))
            | ((directed["source_geography"] == target) & (directed["target_geography"] == source))
        ].copy()
    selected["comparison"] = selected["source_geography"] + " → " + selected["target_geography"]
    return selected


metadata = load_metadata()
if not metadata:
    st.error(
        "Dashboard data are missing. Run "
        "`uv run python -m gisnet.cli build-dashboard-data --resume`."
    )
    st.stop()
missing_metadata = sorted(
    {"data_version", "methods_version", "tables", "active_umbrella_collapse_count"}.difference(
        metadata
    )
)
if missing_metadata or not isinstance(metadata["tables"], dict):
    st.error(
        "The dashboard metadata contract is incomplete. Missing or invalid fields: "
        f"{', '.join(missing_metadata) if missing_metadata else 'tables'}. Rebuild the bundle."
    )
    st.stop()

graph_metrics = require_table("graph_metrics", columns={"year", "corpus_view", "hierarchy_view"})
topics = require_table("topics", columns={"year", "corpus_view", "hierarchy_view", "topic_family"})
trends = require_table(
    "trends",
    columns={
        "year",
        "corpus_view",
        "hierarchy_view",
        "source_region",
        "target_region",
        "region_pair",
    },
)
filter_dimensions = require_table(
    "filter_dimensions",
    columns={
        "year",
        "corpus_view",
        "hierarchy_view",
        "dimension",
        "value",
    },
)

years = sorted(int(value) for value in graph_metrics["year"].dropna().unique())
corpora = sorted(str(value) for value in graph_metrics["corpus_view"].dropna().unique())
hierarchies = sorted(str(value) for value in graph_metrics["hierarchy_view"].dropna().unique())

st.sidebar.title("GIS collaboration")
page = st.sidebar.selectbox("Page", PAGES)
st.sidebar.subheader("Global filters")
year = st.sidebar.select_slider(
    "Year",
    options=years,
    value=years[-1],
    disabled=not control_is_enabled(page, "Year"),
    help="This control is disabled when it does not affect the selected page.",
)
corpus = st.sidebar.selectbox(
    "Corpus view",
    corpora,
    index=corpora.index("broad"),
    disabled=not control_is_enabled(page, "Corpus view"),
    help="This control is disabled when it does not affect the selected page.",
)
hierarchy = st.sidebar.selectbox(
    "Hierarchy view",
    hierarchies,
    index=hierarchies.index("organization"),
    disabled=not control_is_enabled(page, "Hierarchy view"),
    help="This control is disabled when it does not affect the selected page.",
)
region_pairs = dimension_options(trends, "region_pair", corpus=corpus, hierarchy=hierarchy)
countries = dimension_options(
    filter_dimensions, "country_name", year=year, corpus=corpus, hierarchy=hierarchy
)
subregions = dimension_options(
    filter_dimensions, "subregion", year=year, corpus=corpus, hierarchy=hierarchy
)
institution_types = dimension_options(
    filter_dimensions,
    "institution_category",
    year=year,
    corpus=corpus,
    hierarchy=hierarchy,
)
topic_families = dimension_options(topics, "topic_family", corpus=corpus, hierarchy=hierarchy)
counting = st.sidebar.radio(
    "Counting method",
    ("Fractional", "Full"),
    horizontal=True,
    disabled=not control_is_enabled(page, "Counting method"),
    help="This control is disabled when it does not affect the selected page.",
)
region_pair = st.sidebar.selectbox(
    "Macro-region pair",
    ("All", *region_pairs),
    disabled=not control_is_enabled(page, "Macro-region pair"),
    help="This control is disabled when it does not affect the selected page.",
)
with st.sidebar.expander(
    "Geographic and content filters",
    expanded=page in {"Geographic flows", "Institutional network"},
):
    country = st.selectbox(
        "Country",
        ("All", *countries),
        disabled=not control_is_enabled(page, "Country"),
        help="This control is disabled when it does not affect the selected page.",
    )
    subregion = st.selectbox(
        "Subregion",
        ("All", *subregions),
        disabled=not control_is_enabled(page, "Subregion"),
        help="This control is disabled when it does not affect the selected page.",
    )
    institution_type = st.selectbox(
        "Institution type",
        ("All", *institution_types),
        disabled=not control_is_enabled(page, "Institution type"),
        help="This control is disabled when it does not affect the selected page.",
    )
    topic_family = st.selectbox(
        "Topic family",
        ("All", *topic_families),
        disabled=not control_is_enabled(page, "Topic family"),
        help="This control is disabled when it does not affect the selected page.",
    )
    consortium_policy = st.selectbox(
        "Consortium policy",
        ("Primary configured policy", "Exclude warning-size consortium edges"),
        disabled=not control_is_enabled(page, "Consortium policy"),
        help="This control is disabled when it does not affect the selected page.",
    )
st.sidebar.divider()
st.sidebar.caption(f"Data: {metadata['data_version']}")
st.sidebar.caption(f"Methods: {metadata['methods_version']}")
st.sidebar.caption("Local processed snapshot; ordinary viewing makes no OpenAlex requests.")
st.sidebar.warning(
    "Provisional corpus boundary · human review pending. See Methods and limitations."
)

page_title, page_description = PAGE_DETAILS[page]
st.caption("GIS COLLABORATION NETWORK · PROCESSED SNAPSHOT · 2010-2025 COMPLETE YEARS")
st.title(page_title)
st.caption(page_description)
metadata_collapse_count = metadata.get("active_umbrella_collapse_count")
if not isinstance(metadata_collapse_count, int | float):
    st.error("The dashboard metadata lacks a numeric umbrella-collapse count. Rebuild the bundle.")
    st.stop()
active_collapse_count = int(metadata_collapse_count)
if (
    control_is_enabled(page, "Hierarchy view")
    and hierarchy == "umbrella"
    and active_collapse_count == 0
):
    st.warning(
        "Umbrella hierarchy warning: there are zero active collapse rules or relationships. The "
        "umbrella view is currently equivalent to the organization view; no hierarchy is inferred."
    )

weight_column = "fractional_count" if counting == "Fractional" else "full_count"

if page == "Overview":
    current = filtered_view(graph_metrics, year, corpus, hierarchy)
    row = current.iloc[0] if not current.empty else None
    primary_metrics = st.columns(3)
    primary_metrics[0].metric("Institutions", metric_text(row, "node_count", ",.0f"))
    primary_metrics[1].metric("Edges", metric_text(row, "edge_count", ",.0f"))
    primary_metrics[2].metric("Density", metric_text(row, "density", ".4f"))
    secondary_metrics = st.columns(2)
    secondary_metrics[0].metric("Modularity", metric_text(row, "modularity", ".3f"))
    secondary_metrics[1].metric(
        "Largest component",
        metric_text(row, "largest_connected_component_share", ".1%"),
    )
    view_trends = trends.loc[
        (trends["corpus_view"] == corpus) & (trends["hierarchy_view"] == hierarchy)
    ].copy()
    comparison = region_comparison_rows(
        view_trends,
        weight_column=weight_column,
        region_pair=region_pair,
    )
    if comparison.empty:
        show_empty("Choose a different macro-region pair.")
    else:
        figure = px.line(
            comparison.sort_values("year"),
            x="year",
            y="partner_share",
            color="comparison",
            line_dash="comparison",
            color_discrete_map=comparison_color_map(comparison["comparison"]),
            title=f"Regional partner share over time — {counting.lower()} counting",
            labels={
                "partner_share": "Share of collaboration endpoints",
                "year": "Publication year",
                "comparison": "Region / direction",
            },
        )
        figure.update_traces(line={"width": 2.6})
        figure.add_vline(x=year, line_dash="dot", line_color="#64748B")
        figure.update_yaxes(tickformat=".0%", range=[0, 1])
        show_chart(figure, time_series=True)
        st.caption(
            "Shares use collaboration endpoints, so an internal link contributes two local "
            "endpoints while a cross-region link contributes one endpoint to each region."
        )
    st.info(
        "Read shares as collaboration composition, not productivity or causal impact. "
        "Exact values, checksums, and limitations are available on Data quality."
    )

elif page == "Region trends":
    matrix = require_table("matrix")
    view_trends = trends.loc[
        (trends["corpus_view"] == corpus) & (trends["hierarchy_view"] == hierarchy)
    ].copy()
    comparison = region_comparison_rows(
        view_trends,
        weight_column=weight_column,
        region_pair=region_pair,
    )
    if comparison.empty:
        show_empty("Choose another region pair or view.")
    else:
        figure = px.line(
            comparison.sort_values("year"),
            x="year",
            y="partner_share",
            color="comparison",
            line_dash="comparison",
            color_discrete_map=comparison_color_map(comparison["comparison"]),
            title=f"Regional collaboration composition — {counting.lower()} counting",
            labels={
                "partner_share": "Share of collaboration endpoints",
                "comparison": "Region / direction",
            },
        )
        figure.update_traces(line={"width": 2.6})
        figure.add_vline(x=year, line_dash="dot", line_color="#64748B")
        figure.update_yaxes(tickformat=".0%", range=[0, 1])
        show_chart(figure, time_series=True)
        st.caption(
            "The default compares within-region proportions, not absolute collaboration totals. "
            "Select a cross-region pair to compare each direction against its own region total."
        )
    cells = filtered_view(matrix, year, corpus, hierarchy)
    cells = cells.loc[cells["geographic_level"] == "macro_region"].copy()
    if cells.empty:
        show_empty("No matrix is available for this year and view.")
    else:
        partner_cells = partner_share_view(cells, weight_column=weight_column)
        matrix_labels = sorted(
            set(partner_cells["source_geography"]) | set(partner_cells["target_geography"])
        )
        grid = pd.DataFrame(index=matrix_labels, columns=matrix_labels, dtype=float)
        for _, cell in partner_cells.iterrows():
            value = float(cell["partner_share"])
            grid.loc[cell["source_geography"], cell["target_geography"]] = value
        figure = px.imshow(
            grid,
            text_auto=".1%",
            color_continuous_scale=SHARE_SCALE,
            zmin=0,
            zmax=1,
            title=f"{year} partner-share matrix — {counting.lower()} counting",
            labels={
                "x": "Partner region",
                "y": "Source region",
                "color": "Endpoint share",
            },
        )
        show_chart(figure)
        st.caption(
            "Each source-region row sums to 100%. Diagonal cells are within-region shares; "
            "blank cells are missing/no observed flow, not imputed zeros."
        )
        with st.expander("View exact matrix data"):
            show_data(
                partner_cells,
                columns=[
                    "source_geography",
                    "target_geography",
                    "full_count",
                    "fractional_count",
                    "endpoint_weight",
                    "total_endpoint_weight",
                    "partner_share",
                ],
            )

elif page == "Geographic flows":
    map_nodes = require_table("map_nodes")
    map_edges = require_table("map_edges")
    map_coverage = require_table("map_coverage")
    geography_dimensions = require_table(
        "geography_dimensions",
        columns={"country_code", "country_name", "macro_region", "subregion"},
    )
    matrix = require_table("matrix")
    geography_anchors = require_table(
        "geography_anchors",
        columns={
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
    )
    geography_outputs = require_table(
        "geography_outputs",
        columns={
            "year",
            "corpus_view",
            "hierarchy_view",
            "geographic_level",
            "geography",
            "full_work_count",
        },
    )
    st.caption(
        "Primary question: which geography collaborates with which geography? Both modes query "
        "the complete annual institution-flow aggregates and show the same exact selected values."
    )
    level_labels = {
        "Macro-region": "macro_region",
        "Subregion": "subregion",
        "Country": "country",
    }
    metric_labels = {
        "Collaboration volume": "volume",
        "Partner share": "partner_share",
        "Normalized intensity": "normalized_intensity",
    }
    flow_controls = st.columns([1.0, 1.35, 1.35])
    with flow_controls[0]:
        level_label = st.selectbox("Geographic level", tuple(level_labels))
    with flow_controls[1]:
        year_window = st.select_slider(
            "Complete-year window",
            options=years,
            value=(years[-1], years[-1]),
        )
    with flow_controls[2]:
        flow_metric_label = st.selectbox("Flow metric", tuple(metric_labels))
    geographic_level = cast(GeographicLevel, level_labels[level_label])
    flow_metric = cast(FlowMetric, metric_labels[flow_metric_label])
    start_year, end_year = (int(year_window[0]), int(year_window[1]))
    source_options = flow_source_options(
        matrix,
        geography_anchors,
        geographic_level=geographic_level,
        start_year=start_year,
        end_year=end_year,
        corpus_view=corpus,
        hierarchy_view=hierarchy,
    )
    source_labels = {value: label for value, label in source_options}
    if not source_options:
        show_empty("Choose another geographic level, window, corpus, or hierarchy.")
    else:
        default_source = next(
            (value for value, _ in source_options if value == "Asia"), source_options[0][0]
        )
        source_geography = st.selectbox(
            "Source geography",
            [value for value, _ in source_options],
            index=[value for value, _ in source_options].index(default_source),
            format_func=lambda value: source_labels[str(value)],
        )
        selection = GeographicFlowSelection(
            geographic_level=geographic_level,
            source_geography=str(source_geography),
            start_year=start_year,
            end_year=end_year,
            corpus_view=corpus,
            hierarchy_view=hierarchy,
            counting_method=cast(CountingMethod, counting.lower()),
            metric=flow_metric,
        )
        flow_view = build_flow_view(matrix, geography_outputs, geography_anchors, selection)
        st.caption(METRIC_DEFINITIONS[flow_metric])
        if flow_metric == "normalized_intensity":
            st.info(
                "Normalized intensity always uses fractional flow by definition; the counting "
                "control remains visible for the exact companion volumes and partner share."
            )
        if flow_view.empty:
            show_empty("The selected source has no observed collaboration flow in this window.")
        else:
            map_tab, matrix_tab = st.tabs(["Flow map", "Origin-destination matrix"])
            with map_tab:
                show_chart(
                    build_flow_map_figure(flow_view, selection),
                    height=580,
                    cartesian=False,
                )
                st.caption(
                    "Straight lines identify all observed selected-source flows. Width is constant "
                    "in GISNET-130; calibrated arc filtering and width semantics belong to "
                    "GISNET-131."
                )
            with matrix_tab:
                matrix_height = min(900, max(360, 34 * len(flow_view) + 180))
                show_chart(
                    build_flow_matrix_figure(flow_view, selection),
                    height=matrix_height,
                )
                st.caption(
                    "This selected-origin matrix row contains exactly the same destinations and "
                    "values as the map; an absent sparse row means no observed flow, not an "
                    "imputed zero."
                )
            st.subheader("Exact selected flows")
            show_data(
                flow_view,
                columns=[
                    "source_geography",
                    "source_display_name",
                    "target_geography",
                    "target_display_name",
                    "selected_value",
                    "full_count",
                    "fractional_count",
                    "partner_share",
                    "normalized_intensity",
                    "source_full_work_count",
                    "target_full_work_count",
                ],
            )
            anchor_policy = metadata.get("geographic_flow_explorer", {})
            if isinstance(anchor_policy, dict):
                st.caption(
                    f"Anchors: {anchor_policy.get('anchor_method', 'sourced display anchors')}. "
                    f"Source/license: {anchor_policy.get('coordinate_source', 'OpenAlex')} · "
                    f"{anchor_policy.get('coordinate_license', 'CC0')}."
                )

    with st.expander("Institution-level links (optional sourced-coordinate subset)"):
        st.caption(
            "This drilldown is intentionally secondary because missing institution coordinates can "
            "bias it. Filters for subregion, institution type, Topic family, region pair, and "
            "consortium policy apply here."
        )
        coverage = filtered_view(map_coverage, year, corpus, hierarchy)
        if coverage.empty:
            st.info("Coordinate coverage is unavailable for this selected view.")
        else:
            coverage_row = coverage.iloc[0]
            coverage_columns = st.columns(3)
            coverage_columns[0].metric(
                "Coordinate coverage", f"{coverage_row['node_coordinate_coverage_share']:.2%}"
            )
            coverage_columns[1].metric(
                "Nodes with coordinates", f"{int(coverage_row['coordinate_node_count']):,}"
            )
            coverage_columns[2].metric(
                "Nodes missing coordinates",
                f"{int(coverage_row['missing_coordinate_node_count']):,}",
            )
            st.warning(
                f"Only {int(coverage_row['coordinate_node_count']):,} of "
                f"{int(coverage_row['total_node_count']):,} node observations have a complete "
                "sourced coordinate pair. No coordinates are invented."
            )

        base_nodes = filtered_view(map_nodes, year, corpus, hierarchy)
        base_edges = filtered_view(map_edges, year, corpus, hierarchy)
        nodes, partner_nodes, edges = filter_geographic_view(
            base_nodes,
            base_edges,
            country=country,
            subregion=subregion,
            institution_type=institution_type,
            region_pair=region_pair,
            topic_family=topic_family,
            exclude_warning_size_consortia=consortium_policy.startswith("Exclude"),
        )
        if nodes.empty:
            show_empty("Coordinate coverage is sparse; broaden the country/type filters.")
        else:
            if edges.empty:
                visible_edges = edges
                st.info("No coordinate-complete institution links match these filters.")
            else:
                maximum_edges = min(100, len(edges))
                edge_limit = st.slider(
                    "Visible institution-link limit",
                    0,
                    maximum_edges,
                    min(25, maximum_edges),
                    step=5 if maximum_edges >= 5 else 1,
                )
                visible_edges = (
                    edges.sort_values(
                        [weight_column, "source_id", "target_id"],
                        ascending=[False, True, True],
                        kind="stable",
                    ).head(edge_limit)
                    if edge_limit
                    else edges.iloc[0:0]
                )
            visible_endpoint_ids = set(str(value) for value in visible_edges["source_id"])
            visible_endpoint_ids.update(str(value) for value in visible_edges["target_id"])
            partner_nodes = partner_nodes.loc[
                partner_nodes["institution_id"].isin(visible_endpoint_ids)
            ]
            institution_figure = go.Figure()
            maximum_weight = (
                float(visible_edges[weight_column].max()) if not visible_edges.empty else 0.0
            )
            for _, edge in visible_edges.iterrows():
                relative_weight = (
                    float(edge[weight_column]) / maximum_weight if maximum_weight > 0 else 0.0
                )
                hover = (
                    f"{edge['source_name']} — {edge['target_name']}<br>"
                    f"{counting} weight: {float(edge[weight_column]):.3g}<br>"
                    f"Normalized intensity: {float(edge['normalized_intensity']):.3g}"
                )
                institution_figure.add_trace(
                    go.Scattergeo(
                        lon=[edge["source_longitude"], edge["target_longitude"]],
                        lat=[edge["source_latitude"], edge["target_latitude"]],
                        mode="lines",
                        line={
                            "width": 0.6 + 2.4 * relative_weight**0.5,
                            "color": "rgba(37,99,235,0.35)",
                        },
                        text=[hover, hover],
                        hovertemplate="%{text}<extra></extra>",
                        showlegend=False,
                    )
                )
            if not partner_nodes.empty:
                institution_figure.add_trace(
                    go.Scattergeo(
                        lon=partner_nodes["longitude"],
                        lat=partner_nodes["latitude"],
                        text=partner_nodes["display_name"] + " · " + partner_nodes["country_name"],
                        customdata=partner_nodes[["institution_id", "work_count"]],
                        hovertemplate=(
                            "%{text}<br>ID %{customdata[0]}<br>Works %{customdata[1]}"
                            "<br>Partner outside node filters<extra></extra>"
                        ),
                        mode="markers",
                        marker={
                            "size": 8,
                            "color": "#64748b",
                            "opacity": 0.55,
                            "line": {"width": 0.4, "color": "white"},
                        },
                        name="Partner endpoints outside node filters",
                    )
                )
            institution_figure.add_trace(
                go.Scattergeo(
                    lon=nodes["longitude"],
                    lat=nodes["latitude"],
                    text=nodes["display_name"] + " · " + nodes["country_name"],
                    customdata=nodes[["institution_id", "work_count"]],
                    hovertemplate=(
                        "%{text}<br>ID %{customdata[0]}<br>Works %{customdata[1]}<extra></extra>"
                    ),
                    mode="markers",
                    marker={
                        "size": 10,
                        "color": "#0072B2",
                        "line": {"width": 0.5, "color": "white"},
                    },
                    name="Institutions matching node filters",
                )
            )
            institution_figure.update_geos(
                showland=True,
                landcolor="#f1f5f9",
                showcountries=True,
                projection_type="natural earth",
            )
            show_chart(institution_figure, height=560, cartesian=False)
            st.caption(
                f"Showing {len(visible_edges)} links ranked by {counting.lower()} weight; "
                "the released map subset was first capped by the non-primary visualization "
                "score. Line width is relative within this displayed subset and is not "
                "comparable across filters."
            )

elif page == "Institutional network":
    network_nodes = require_table("network_nodes")
    network_edges = require_table("network_edges")
    community_continuity = require_table("community_continuity")
    nodes = filtered_view(network_nodes, year, corpus, hierarchy)
    edges = filtered_view(network_edges, year, corpus, hierarchy)
    continuity = filtered_view(community_continuity, year, corpus, hierarchy).rename(
        columns={"annual_community_id": "community_id"}
    )
    if not continuity.empty:
        nodes = nodes.merge(
            continuity[["community_id", "continuity_id", "low_overlap_uncertain"]],
            on="community_id",
            how="left",
        )
    if country != "All":
        nodes = nodes.loc[nodes["country_name"] == country]
    if subregion != "All":
        nodes = nodes.loc[nodes["subregion"] == subregion]
    if institution_type != "All":
        nodes = nodes.loc[nodes["institution_category"] == institution_type]
    if topic_family != "All":
        edges = edges.loc[edges["topic_families"].apply(lambda values: topic_family in values)]
    if region_pair != "All":
        pairs = edges.apply(
            lambda row: " — ".join(sorted((row["source_region"], row["target_region"]))), axis=1
        )
        edges = edges.loc[pairs == region_pair]
    if consortium_policy.startswith("Exclude"):
        edges = edges.loc[edges["large_consortium_work_count"] == 0]
    available_metrics = ("work_count", "degree", "fractional_strength", "pagerank")
    size_metric = st.selectbox(
        "Node-size metric", available_metrics, index=2, format_func=human_label
    )
    color_metric = st.radio(
        "Node color",
        ("macro_region", "community_id", "continuity_id"),
        horizontal=True,
        format_func=human_label,
    )
    minimum = float(edges[weight_column].quantile(0.5)) if not edges.empty else 0.0
    minimum_weight = st.number_input(
        f"Minimum {counting.lower()} edge weight",
        min_value=0.0,
        value=max(0.0, minimum),
        format="%.4f",
    )
    edges = edges.loc[edges[weight_column] >= minimum_weight]
    visible_ids = set(nodes["institution_id"])
    edges = edges.loc[edges["source_id"].isin(visible_ids) & edges["target_id"].isin(visible_ids)]
    if nodes.empty:
        show_empty("Broaden institution filters.")
    else:
        figure = go.Figure()
        if not edges.empty:
            xs: list[float | None] = []
            ys: list[float | None] = []
            for _, edge in edges.iterrows():
                xs.extend([edge["source_x"], edge["target_x"], None])
                ys.extend([edge["source_y"], edge["target_y"], None])
            figure.add_trace(
                go.Scatter(
                    x=xs,
                    y=ys,
                    mode="lines",
                    line={"width": 0.7, "color": "rgba(100,116,139,0.25)"},
                    hoverinfo="skip",
                    name="Collaborations",
                )
            )
        maximum_size = max(float(nodes[size_metric].max()), 1e-12)
        color_map = (
            REGION_COLORS
            if color_metric == "macro_region"
            else category_color_map(nodes[color_metric])
        )
        for category, group in nodes.groupby(color_metric, dropna=False):
            sizes = 7 + 23 * (group[size_metric].astype(float) / maximum_size) ** 0.5
            figure.add_trace(
                go.Scatter(
                    x=group["x"],
                    y=group["y"],
                    mode="markers",
                    name=str(category) if pd.notna(category) else "Unassigned",
                    text=group["display_name"],
                    customdata=group[["institution_id", "work_count", "degree", "pagerank"]],
                    marker={
                        "size": sizes,
                        "opacity": 0.82,
                        "color": color_map.get(category, "#6B7280"),
                        "line": {"width": 0.4, "color": "white"},
                    },
                    hovertemplate=(
                        "%{text}<br>ID %{customdata[0]}<br>Works %{customdata[1]}"
                        "<br>Degree %{customdata[2]}<br>PageRank %{customdata[3]:.4g}"
                        "<extra></extra>"
                    ),
                )
            )
        figure.update_layout(
            xaxis={"visible": False},
            yaxis={"visible": False, "scaleanchor": "x", "scaleratio": 1},
            margin={"l": 0, "r": 0, "t": 15, "b": 0},
            legend_title=color_metric,
        )
        show_chart(figure, height=620)
        st.caption(
            f"Node size = {human_label(size_metric)}; node color = {human_label(color_metric)}; "
            "edge width is constant. "
            f"The {counting.lower()} collaboration weight controls edge inclusion, with visible "
            f"minimum {minimum_weight:.4g}."
        )
        st.info(
            visible_accessibility_sentence(
                year=year,
                corpus_view=corpus,
                hierarchy_view=hierarchy,
                node_count=len(nodes),
                edge_count=len(edges),
                cross_region_edge_count=int(
                    (edges["source_region"] != edges["target_region"]).sum()
                ),
                counting_method=counting,
                minimum_weight=(float(edges[weight_column].min()) if not edges.empty else None),
                size_metric=size_metric,
                color_metric=human_label(color_metric).casefold(),
            )
        )

elif page == "Institution explorer":
    network_edges = require_table("network_edges")
    institution_identities = require_table("institution_identities")
    pair_data = network_edges.loc[
        (network_edges["corpus_view"] == corpus) & (network_edges["hierarchy_view"] == hierarchy)
    ].copy()
    institution_names = institution_labels(pair_data)
    ordered_ids = sorted(
        institution_names,
        key=lambda identifier: (institution_names[identifier].casefold(), identifier),
    )
    if len(ordered_ids) < 2:
        show_empty("No institution pairs are available in this view.")
    else:
        left, right = st.columns(2)
        institution_a = left.selectbox(
            "Institution A", ordered_ids, format_func=institution_names.get
        )
        institution_b = right.selectbox(
            "Institution B",
            ordered_ids,
            index=min(1, len(ordered_ids) - 1),
            format_func=institution_names.get,
        )
        pair = pd.DataFrame()
        if institution_a == institution_b:
            show_empty("Choose two different stable institution IDs.")
        else:
            pair = build_pair_timeline(pair_data, institution_a, institution_b, years=years)
        has_observed_pair = (
            institution_a != institution_b
            and pair[["full_count", "fractional_count"]].to_numpy().any()
        )
        if institution_a != institution_b and not has_observed_pair:
            show_empty("This thresholded public snapshot has no visible edge for the selected IDs.")
        elif has_observed_pair:
            figure = go.Figure()
            figure.add_trace(
                go.Scatter(
                    x=pair["year"],
                    y=pair["full_count"],
                    name="Full count",
                    mode="lines",
                    line={"color": "#0072B2", "width": 2.6},
                )
            )
            figure.add_trace(
                go.Scatter(
                    x=pair["year"],
                    y=pair["fractional_count"],
                    name="Fractional count",
                    mode="lines",
                    line={"color": "#E69F00", "dash": "dash", "width": 2.6},
                )
            )
            figure.update_layout(
                title=f"{institution_names[institution_a]} ↔ {institution_names[institution_b]}",
                yaxis_title="Collaboration weight",
            )
            show_chart(figure, time_series=True)
            with st.expander("View annual partnership data"):
                show_data(
                    pair,
                    columns=[
                        "year",
                        "full_count",
                        "fractional_count",
                        "normalized_intensity",
                        "persistence_3y",
                        "persistence_5y",
                        "topic_families",
                        "work_ids_sample",
                    ],
                )
            st.caption(
                "Stable institution IDs are shown in brackets; missing years use zero counts "
                "and missing intensity/persistence."
            )
        st.subheader("Organization and umbrella identities")
        for selected_id, selected_label in (
            (institution_a, institution_names[institution_a]),
            (institution_b, institution_names[institution_b]),
        ):
            st.markdown(f"**{selected_label}**")
            identities = identity_rows(
                institution_identities, selected_id, hierarchy_view=hierarchy
            )
            if identities.empty:
                st.caption("No released hierarchy mapping is available for this ID.")
            else:
                show_data(identities)

elif page == "Topic-family comparison":
    view = topics.loc[
        (topics["corpus_view"] == corpus) & (topics["hierarchy_view"] == hierarchy)
    ].copy()
    if topic_family != "All":
        view = view.loc[view["topic_family"] == topic_family]
    if view.empty:
        show_empty("Choose another Topic family or network view.")
    else:
        top = view.groupby("topic_family")[weight_column].sum().nlargest(12).index
        view = view.loc[view["topic_family"].isin(top)]
        figure = px.line(
            view.sort_values("year"),
            x="year",
            y=weight_column,
            color="topic_family",
            line_dash="topic_family",
            color_discrete_map=category_color_map(view["topic_family"]),
            labels={
                weight_column: f"{counting} visible-core edge weight",
                "topic_family": "Topic family",
            },
        )
        figure.update_traces(line={"width": 2.4})
        figure.add_vline(x=year, line_dash="dot", line_color="#64748B")
        show_chart(figure, time_series=True)
        st.caption(
            "Topic-family comparison covers the thresholded fixed-layout core, "
            "not all stored edges."
        )

elif page == "Methods and limitations":
    st.markdown(
        """
### Primary analysis
- **Corpus:** Strict and Broad GIS Topic views, 2010-2025 complete calendar years.
- **Institutions:** organization and documented umbrella views; stable OpenAlex/ROR
  identifiers are preserved.
- **Edges:** undirected institutional co-authorship with full and fractional counting.
- **Intensity:** fractional edge weight divided by the geometric mean of institutional output.
- **Persistence:** fixed-denominator trailing 3-year and 5-year windows; early years are
  flagged incomplete.
- **Metrics:** weighted centrality, PageRank, components, assortativity, bridge score,
  and Leiden communities.
- **Layout:** one seeded full-period aggregate layout reused across all years.

### Interpretation boundaries
- The visualization score is **non-primary** and only ranks edges for display.
- Map coordinates are shown only when sourced; no missing coordinate is guessed or imputed.
- Topic classifications are provisional and have not received human review; no automated
  judgment is presented as human review.
- Collaboration is co-authorship, not citation flow, knowledge flow, or research similarity.
- Missing matrix cells mean no observed flow in the sparse table; they are not silently
  replaced by zero.
- Public dashboard tables are aggregate/thresholded extracts; full local processed Parquets
  remain rebuildable.

### Geographic naming
UN M49-style macro-regions and subregions are used as analytical groupings. Geographic
labels do not express a political position.
        """
    )
    limitations = metadata.get("known_limitations", [])
    if isinstance(limitations, list):
        st.subheader("Snapshot-specific limitations")
        for limitation in limitations:
            st.write(f"- {limitation}")

elif page == "Data quality":
    sensitivity = require_table("sensitivity")
    map_coverage = require_table("map_coverage")
    community_continuity = require_table("community_continuity")
    community_transitions = require_table("community_transitions")
    st.subheader("Required sensitivity matrix")
    show_data(
        sensitivity,
        columns=[
            "comparison_id",
            "comparison",
            "baseline_label",
            "alternative_label",
            "absolute_relative_change",
            "major_change",
            "status",
        ],
    )
    st.subheader("Coordinate coverage")
    coverage = filtered_view(map_coverage, year, corpus, hierarchy)
    if coverage.empty:
        show_empty("No coverage row exists for this view.")
    else:
        show_data(coverage)
    st.subheader("Community continuity")
    continuity_view = filtered_view(community_continuity, year, corpus, hierarchy)
    transition_view = community_transitions.loc[
        (community_transitions["transition_year"] == year)
        & (community_transitions["corpus_view"] == corpus)
        & (community_transitions["hierarchy_view"] == hierarchy)
    ].copy()
    if continuity_view.empty:
        show_empty("No community continuity rows exist for this view.")
    else:
        columns = st.columns(3)
        columns[0].metric("Continuity IDs", continuity_view["continuity_id"].nunique())
        columns[1].metric("Uncertain matches", int(continuity_view["low_overlap_uncertain"].sum()))
        columns[2].metric("Transition events", len(transition_view))
        if not transition_view.empty:
            show_data(
                transition_view["event_type"].value_counts().rename_axis("event_type").reset_index()
            )
        st.caption(
            "Continuity uses deterministic adjacent-year Jaccard assignment; "
            "selected matches below 0.25 are explicitly uncertain."
        )
    st.subheader("Version and integrity metadata")
    table_rows = metadata.get("tables", {})
    if isinstance(table_rows, dict):
        inventory = pd.DataFrame(
            [
                {"table": name, "rows": info.get("row_count"), "sha256": info.get("sha256")}
                for name, info in table_rows.items()
                if isinstance(info, dict)
            ]
        )
        with st.expander("Snapshot table inventory"):
            show_data(inventory)
    with st.expander("Raw machine-readable metadata"):
        st.json(metadata)
