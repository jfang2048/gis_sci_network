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
    FLOW_LINE_WIDTH_DEFINITIONS,
    METRIC_DEFINITIONS,
    CountingMethod,
    FlowDisplayPolicy,
    FlowMetric,
    GeographicFlowSelection,
    GeographicLevel,
    build_flow_map_figure,
    build_flow_matrix_figure,
    build_flow_view,
    filter_readable_flows,
    flow_source_options,
)
from gisnet.visualization.network_view import visible_accessibility_sentence
from gisnet.visualization.pair_explorer import (
    build_pair_timeline,
    identity_rows,
    institution_labels,
)
from gisnet.visualization.school_compare import (
    align_school_profiles,
    comparison_activity_horizons,
    comparison_topic_view,
)
from gisnet.visualization.school_ego_map import (
    EGO_METRIC_LABELS,
    SchoolEgoLevel,
    SchoolEgoMetric,
    SchoolEgoSelection,
    build_school_ego_map_figure,
    build_school_ego_view,
    query_school_ego_partners,
)
from gisnet.visualization.school_profile import (
    activity_horizon_view,
    profile_quality_messages,
    query_school_profile,
    query_school_profiles,
    query_school_topics,
    query_school_topics_for_schools,
    research_neighbor_view,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "dashboard" / "data"
PAGES = (
    "School Finder",
    "School Profile",
    "Compare Schools",
    "Geographic Flows",
    "Institutional Network",
    "Global Trends",
    "Methods and Data Quality",
)
PAGE_DETAILS = {
    "School Finder": (
        "Find an eligible research institution",
        "Search the complete stable-ID school index before interpreting networks or rankings.",
    ),
    "School Profile": (
        "School Profile",
        "Start from one stable-ID institution, then inspect separately bounded activity, Topic, "
        "partner, network, citation-flow, research-proximity, and quality evidence.",
    ),
    "Compare Schools": (
        "Compare Schools",
        "Compare two to four institutions on shared axes with exact values and no hidden "
        "per-school normalization.",
    ),
    "Geographic Flows": (
        "Geographic Flow Explorer",
        "Select one geography and trace its exact collaboration volume, share, or intensity.",
    ),
    "Institutional Network": (
        "Institutional Network",
        "Inspect the fixed-layout collaboration core or trace one stable-ID institution pair.",
    ),
    "Global Trends": (
        "Global Trends",
        "Preserved complete-year overview, regional trends, and Topic-family history.",
    ),
    "Methods and Data Quality": (
        "Methods and Data Quality",
        "Definitions, limitations, sensitivity, coverage, versions, and integrity evidence.",
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


SCHOOL_INDEX_COLUMNS = {
    "school_id",
    "display_name",
    "country_code",
    "country_name",
    "macro_region",
    "subregion",
    "institution_category",
    "latest_supported_month",
    "broad_work_count",
    "strict_work_count",
    "recent_24m_work_count",
    "date_coverage_ratio",
    "identity_status",
    "identity_resolution_confidence",
    "identity_quality_flags",
    "has_ambiguous_name_match",
    "in_prior_visualization_core",
    "has_retained_ego_partners",
}


def school_selector_records(
    frame: pd.DataFrame,
    *,
    prefer_retained_partners: bool = False,
) -> tuple[pd.DataFrame, list[str], dict[str, str], str]:
    """Return stable-ID options, searchable labels, and one evidence-backed default."""
    rows = frame.sort_values(["display_name", "school_id"], kind="stable").reset_index(drop=True)
    options = [str(value) for value in rows["school_id"]]
    labels = {
        str(row.school_id): (
            f"{row.display_name} · "
            f"{row.country_name if pd.notna(row.country_name) else 'Unknown country'} · "
            f"{row.school_id}"
        )
        for row in rows.itertuples(index=False)
    }
    defaults = rows
    if prefer_retained_partners:
        defaults = rows.loc[rows["has_retained_ego_partners"].astype(bool)]
    defaults = defaults.sort_values(
        ["recent_24m_work_count", "display_name", "school_id"],
        ascending=[False, True, True],
        kind="stable",
    )
    default = str(defaults.iloc[0]["school_id"]) if not defaults.empty else options[0]
    return rows, options, labels, default


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
    expanded=page in {"Geographic Flows", "Institutional Network"},
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
    "Provisional corpus boundary · human review pending. See Methods and Data Quality."
)

page_title, page_description = PAGE_DETAILS[page]
st.caption("GIS COLLABORATION NETWORK · PROCESSED SNAPSHOT · 2010-2025 COMPLETE YEARS")
st.title(page_title)
st.caption(page_description)
if page == "School Finder":
    st.caption(
        "Context · stable school identity · complete historical Strict and Broad corpus counts · "
        "Broad recent 24-month activity · exact-date coverage and identity quality remain visible."
    )
elif page == "Compare Schools":
    st.caption(
        f"Context · {corpus.title()} corpus · stable school identity · selectable rolling "
        "publication activity and Topics · separately bounded complete-year network and "
        "citation-flow evidence · missing values remain explicit."
    )
elif page == "School Profile":
    st.caption(
        f"Context · {corpus.title()} corpus · stable school identity · rolling publication "
        "activity is separate from complete-year network, citation-flow, and research-proximity "
        "context · missing values remain explicit."
    )
else:
    st.caption(
        f"Context · {corpus.title()} corpus · {hierarchy} identity · complete-year scientific "
        "history through 2025 · provisional Topic boundary."
    )

view = page
if page == "Geographic Flows":
    view = "Geographic flows"
elif page == "Institutional Network":
    network_view = st.radio(
        "Network view",
        ("Collaboration core", "Institution pair history"),
        horizontal=True,
    )
    view = (
        "Institutional network" if network_view == "Collaboration core" else "Institution explorer"
    )
elif page == "Global Trends":
    global_trends_view = st.radio(
        "Global Trends view",
        ("Overview", "Regional trends", "Topic families"),
        horizontal=True,
    )
    view = {
        "Overview": "Overview",
        "Regional trends": "Region trends",
        "Topic families": "Topic-family comparison",
    }[global_trends_view]
elif page == "Methods and Data Quality":
    evidence_view = st.radio(
        "Evidence view",
        ("Methods and limitations", "Data quality"),
        horizontal=True,
    )
    view = evidence_view

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

if view == "School Finder":
    school_index = require_table("school_index", columns=SCHOOL_INDEX_COLUMNS)
    finder_columns = st.columns(3)
    macro_regions = sorted(str(value) for value in school_index["macro_region"].dropna().unique())
    selected_macro_region = finder_columns[0].selectbox(
        "Finder macro-region",
        ("All", *macro_regions),
    )
    region_candidates = school_index
    if selected_macro_region != "All":
        region_candidates = region_candidates.loc[
            region_candidates["macro_region"] == selected_macro_region
        ]
    finder_countries = sorted(
        str(value) for value in region_candidates["country_name"].dropna().unique()
    )
    selected_country = finder_columns[1].selectbox(
        "Finder country",
        ("All", *finder_countries),
    )
    finder_categories = sorted(
        str(value) for value in region_candidates["institution_category"].dropna().unique()
    )
    selected_category = finder_columns[2].selectbox(
        "Finder institution category",
        ("All", *finder_categories),
    )
    finder_view = region_candidates
    if selected_country != "All":
        finder_view = finder_view.loc[finder_view["country_name"] == selected_country]
    if selected_category != "All":
        finder_view = finder_view.loc[finder_view["institution_category"] == selected_category]
    if finder_view.empty:
        show_empty("Broaden the School Finder geography or institution-category filters.")
    else:
        school_rows, school_options, school_labels, default_school = school_selector_records(
            finder_view
        )
        st.metric("Eligible schools matching filters", f"{len(school_rows):,}")
        selected_school_id = st.selectbox(
            "School (type a name, country, or stable ID)",
            school_options,
            index=school_options.index(default_school),
            format_func=school_labels.get,
            key="school_finder_school",
            help=(
                "Search labels include the institution name, country, and stable ID. The stable "
                "ID—not the display name—is the selected entity key."
            ),
        )
        selected_school = school_rows.loc[
            school_rows["school_id"].astype(str) == selected_school_id
        ].iloc[0]
        identity_columns = st.columns(4)
        identity_columns[0].metric("Stable school ID", selected_school_id)
        identity_columns[1].metric("Country", str(selected_school["country_name"]))
        identity_columns[2].metric("Macro-region", str(selected_school["macro_region"]))
        identity_columns[3].metric(
            "Institution category", human_label(selected_school["institution_category"])
        )
        evidence_columns = st.columns(4)
        evidence_columns[0].metric(
            "Broad Works · complete history", f"{int(selected_school['broad_work_count']):,}"
        )
        evidence_columns[1].metric(
            "Strict Works · complete history", f"{int(selected_school['strict_work_count']):,}"
        )
        evidence_columns[2].metric(
            "Broad Works · recent 24m",
            f"{int(selected_school['recent_24m_work_count']):,}",
        )
        evidence_columns[3].metric(
            "Exact-date coverage", f"{float(selected_school['date_coverage_ratio']):.1%}"
        )
        if bool(selected_school["has_ambiguous_name_match"]):
            st.warning(
                "At least one indexed alias also matches another stable institution. This "
                "selection remains explicit because the stable school ID is shown."
            )
        if not bool(selected_school["in_prior_visualization_core"]):
            st.success(
                "This institution is outside the prior thresholded visualization core and is "
                "still findable through the complete school index."
            )
        st.subheader("Selected institution context")
        show_data(
            selected_school.to_frame().T,
            columns=[
                "school_id",
                "display_name",
                "country_code",
                "country_name",
                "subregion",
                "macro_region",
                "institution_category",
                "identity_status",
                "identity_resolution_confidence",
                "identity_quality_flags",
                "latest_supported_month",
            ],
        )
        st.caption(
            "School is interface shorthand for an eligible research institution, not a claim "
            "about degree programs or institutional quality. Continue with the same stable ID on "
            "School Profile or Compare Schools."
        )

elif view == "School Profile":
    school_index = require_table("school_index", columns=SCHOOL_INDEX_COLUMNS)
    profile_policy = metadata.get("school_profile")
    if not isinstance(profile_policy, dict):
        st.error("The dashboard metadata lacks the School Profile contract. Rebuild the bundle.")
        st.stop()
    supported_windows = profile_policy.get("supported_rolling_windows")
    if not isinstance(supported_windows, list):
        st.error("The dashboard metadata has no supported School Profile rolling windows.")
        st.stop()
    window_options = sorted(
        {
            int(row["window_months"])
            for row in supported_windows
            if isinstance(row, dict) and isinstance(row.get("window_months"), int | float)
        }
    )
    if not window_options:
        st.error("The dashboard metadata has no valid School Profile rolling-window values.")
        st.stop()

    school_rows, school_options, school_labels, default_school = school_selector_records(
        school_index,
        prefer_retained_partners=True,
    )
    profile_controls = st.columns((2.4, 1.0))
    selected_school_id = profile_controls[0].selectbox(
        "School (type a name, country, or stable ID)",
        school_options,
        index=school_options.index(default_school),
        format_func=school_labels.get,
        help="Names are searchable labels; the stable school ID is the profile query key.",
    )
    default_window = int(profile_policy.get("default_rolling_window_months", 24))
    selected_window = int(
        profile_controls[1].selectbox(
            "Profile rolling window",
            window_options,
            index=(window_options.index(default_window) if default_window in window_options else 0),
            format_func=lambda value: f"Rolling {value} months",
        )
    )
    selected_school = school_rows.loc[
        school_rows["school_id"].astype(str) == selected_school_id
    ].iloc[0]
    profile_rows = query_school_profile(
        DATA / "school_profiles.parquet",
        school_id=selected_school_id,
        corpus_view=corpus,
        window_months=selected_window,
    )
    profile = profile_rows.iloc[0] if not profile_rows.empty else None
    topic_view = query_school_topics(
        DATA / "school_topic_profiles.parquet",
        school_id=selected_school_id,
        corpus_view=corpus,
        window_months=selected_window,
    )

    st.header("1. Identity and geography")
    identity_columns = st.columns(4)
    identity_columns[0].metric("Stable school ID", selected_school_id)
    identity_columns[1].metric("Country", str(selected_school["country_name"]))
    identity_columns[2].metric("Subregion", str(selected_school["subregion"]))
    identity_columns[3].metric("Macro-region", str(selected_school["macro_region"]))
    show_data(
        selected_school.to_frame().T,
        columns=[
            "school_id",
            "display_name",
            "country_code",
            "country_name",
            "subregion",
            "macro_region",
            "institution_category",
            "identity_status",
            "identity_resolution_confidence",
            "identity_quality_flags",
        ],
    )
    st.caption(
        "School is interface shorthand for this stable-ID eligible research institution. "
        "Identity evidence is not an institutional-quality or degree-program claim."
    )
    if not bool(selected_school["in_prior_visualization_core"]):
        st.success(
            "This school is outside the prior thresholded visualization core. Its partners "
            "remain available because this profile queries the per-school index directly."
        )

    st.header("2. Recent activity and trend")
    if profile is None:
        st.info(
            f"No {corpus.title()} profile row exists for this stable school ID and rolling "
            f"{selected_window}-month window; no activity value is imputed."
        )
    else:
        st.caption(
            f"Rolling window {profile['window_start']} through {profile['window_end']} · "
            f"{corpus.title()} corpus · {float(profile['coverage_ratio']):.1%} window coverage · "
            "publication month is observation time, not collaboration-start time."
        )
        recent_metrics = st.columns(4)
        recent_metrics[0].metric(
            "Works in selected window", metric_text(profile, "full_work_count", ",.0f")
        )
        recent_metrics[1].metric(
            "Fractional Works", metric_text(profile, "fractional_work_count", ",.2f")
        )
        recent_metrics[2].metric(
            "International collaboration share",
            metric_text(profile, "international_collaboration_share", ".1%"),
        )
        recent_metrics[3].metric(
            "Cross-region collaboration share",
            metric_text(profile, "cross_region_collaboration_share", ".1%"),
        )
        activity = activity_horizon_view(profile)
        available_activity = activity.loc[activity["work_count"].notna()].copy()
        if available_activity.empty:
            st.info("No source-stored rolling activity horizon is available; none is imputed.")
        else:
            activity_figure = px.bar(
                available_activity,
                x="window_label",
                y="work_count",
                text="work_count",
                title="Source-stored recent activity horizons",
                labels={"window_label": "Rolling publication window", "work_count": "Works"},
            )
            show_chart(activity_figure, height=370)
            show_data(activity, columns=["window_label", "window_months", "work_count"])
        st.caption(
            "The 12-, 24-, and 36-month bars are cumulative rolling horizons ending at the same "
            "latest supported month; they are not independent annual bins. Complete-year network "
            "and bibliometric context appears separately below."
        )

    st.header("3. Topic profile")
    if topic_view.empty:
        status = (
            str(profile["topic_profile_support_status"])
            if profile is not None and pd.notna(profile["topic_profile_support_status"])
            else "unavailable"
        )
        st.info(
            f"Topic-profile support status is `{status}` for this selection; no Topic share or "
            "specialization value is imputed."
        )
    else:
        displayed_topics = topic_view.head(10).copy()
        topic_figure = px.bar(
            displayed_topics.sort_values("topic_family_share"),
            x="topic_family_share",
            y="topic_family",
            orientation="h",
            text=displayed_topics.sort_values("topic_family_share")["topic_family_share"].map(
                lambda value: f"{value:.1%}"
            ),
            title="Topic-family distribution in the selected rolling window",
            labels={
                "topic_family_share": "Share of supported Topic weight",
                "topic_family": "Topic family",
            },
        )
        topic_figure.update_xaxes(tickformat=".0%")
        show_chart(topic_figure, height=max(360, 38 * len(displayed_topics) + 150))
        show_data(
            topic_view,
            columns=[
                "topic_rank",
                "topic_family",
                "topic_family_share",
                "contributing_work_count",
                "specialization_lift_global",
                "specialization_lift_macro_region",
                "specialization_lift_country",
            ],
        )
    st.warning(
        "Topic distribution and specialization describe research emphasis under the provisional "
        "Topic registry. They are not collaboration, research quality, or admissions evidence."
    )

    st.header("4. Institutional partners")
    school_ego_policy = metadata.get("school_ego_map")
    if not isinstance(school_ego_policy, dict):
        st.error("The dashboard metadata lacks the School Ego Map contract. Rebuild the bundle.")
        st.stop()
    period_rows = school_ego_policy.get("periods")
    if not isinstance(period_rows, list):
        st.error("The dashboard metadata has no supported School Ego Map periods.")
        st.stop()
    partner_periods = {
        str(row["period_key"]): str(row["period_label"])
        for row in period_rows
        if isinstance(row, dict) and "period_key" in row and "period_label" in row
    }
    if not partner_periods:
        st.error("The dashboard metadata has no valid School Ego Map period labels.")
        st.stop()
    default_partner_period = f"rolling_{selected_window}m"
    if default_partner_period not in partner_periods:
        default_partner_period = next(iter(partner_periods))
    profile_level_labels = {
        "Partner institutions": "institution",
        "Partner countries": "country",
        "Partner macro-regions": "macro_region",
    }
    profile_metric_by_label = {label: metric for metric, label in EGO_METRIC_LABELS.items()}
    partner_controls = st.columns(4)
    selected_partner_period = partner_controls[0].selectbox(
        "Collaboration period",
        tuple(partner_periods),
        index=tuple(partner_periods).index(default_partner_period),
        format_func=partner_periods.get,
    )
    selected_partner_level_label = partner_controls[1].selectbox(
        "Partner level", tuple(profile_level_labels)
    )
    selected_partner_metric_label = partner_controls[2].selectbox(
        "Ego-map metric", tuple(profile_metric_by_label)
    )
    selected_partner_top_n = int(
        partner_controls[3].number_input(
            "Top partners",
            min_value=1,
            max_value=int(school_ego_policy.get("retained_partner_limit_per_school_period", 50)),
            value=12,
            step=1,
        )
    )
    selected_partner_level = cast(
        SchoolEgoLevel, profile_level_labels[selected_partner_level_label]
    )
    selected_partner_metric = cast(
        SchoolEgoMetric, profile_metric_by_label[selected_partner_metric_label]
    )
    partners = query_school_ego_partners(
        DATA / "school_ego_partners.parquet",
        school_id=selected_school_id,
        corpus_view=corpus,
        period_key=selected_partner_period,
    )
    geography_anchors = pd.DataFrame()
    institution_partner_view = pd.DataFrame()
    if partners.empty:
        st.info(
            "No retained institutional collaboration partner exists for this exact school, "
            "corpus, and collaboration period; no partner is inferred."
        )
    else:
        geography_anchors = require_table(
            "geography_anchors",
            columns={
                "geographic_level",
                "geography",
                "latitude",
                "longitude",
                "coordinate_source",
            },
        )
        institution_partner_view = build_school_ego_view(
            partners,
            geography_anchors,
            SchoolEgoSelection(
                school_id=selected_school_id,
                corpus_view=corpus,
                period_key=selected_partner_period,
                level="institution",
                metric=selected_partner_metric,
                top_n=selected_partner_top_n,
            ),
        )
        show_data(
            institution_partner_view,
            columns=[
                "display_rank",
                "target_name",
                "target_id",
                "target_country_name",
                "target_macro_region",
                "full_count",
                "fractional_count",
                "normalized_intensity",
                "persistence",
            ],
        )
        st.caption(
            "Co-authorship is observed publication collaboration. Partners come from this "
            "school's retained per-school index rather than a global visualization threshold; "
            f"exact stable IDs and values remain visible for "
            f"{partner_periods[selected_partner_period]}."
        )

    st.header("5. Partner geography")
    if partners.empty:
        st.info("Partner geography is unavailable because this selection has no retained partner.")
    else:
        partner_selection = SchoolEgoSelection(
            school_id=selected_school_id,
            corpus_view=corpus,
            period_key=selected_partner_period,
            level=selected_partner_level,
            metric=selected_partner_metric,
            top_n=selected_partner_top_n,
        )
        partner_geography_view = build_school_ego_view(
            partners,
            geography_anchors,
            partner_selection,
        )
        mapped_geography = partner_geography_view.loc[
            partner_geography_view["is_mappable"].astype(bool)
        ].copy()
        map_column, geography_table_column = st.columns((1.55, 1.0))
        with map_column:
            if mapped_geography.empty:
                st.info(
                    "No selected partner geography has complete sourced display coordinates; "
                    "exact values remain in the table."
                )
            else:
                show_chart(
                    build_school_ego_map_figure(mapped_geography, partner_selection),
                    height=520,
                    cartesian=False,
                )
        with geography_table_column:
            st.subheader("Exact mapped partners")
            show_data(
                mapped_geography,
                columns=[
                    "display_rank",
                    "target_name",
                    "target_id",
                    "selected_value",
                    "fractional_count",
                    "normalized_intensity",
                    "persistence",
                    "institution_partner_count",
                ],
            )
        st.caption(
            "The school-centred map and adjacent table use the same exact selected value and "
            "retained partner rows. Geography aggregates sum fractional volume and use "
            "fractional-volume-weighted intensity or persistence; missing coordinates never "
            "create invented map points."
        )
        unmapped_geography = partner_geography_view.loc[
            ~partner_geography_view["is_mappable"].astype(bool)
        ].copy()
        if not unmapped_geography.empty:
            with st.expander("Exact partners without complete sourced coordinates"):
                show_data(
                    unmapped_geography,
                    columns=[
                        "display_rank",
                        "target_name",
                        "target_id",
                        "selected_value",
                        "fractional_count",
                        "normalized_intensity",
                        "persistence",
                        "institution_partner_count",
                    ],
                )
                st.caption("These exact rows are retained and are not plotted or imputed.")

    st.header("6. Annual network position")
    if profile is None or not str(profile.get("annual_network_support_status", "")).startswith(
        "available"
    ):
        status = (
            str(profile["annual_network_support_status"])
            if profile is not None and pd.notna(profile["annual_network_support_status"])
            else "unavailable"
        )
        st.info(f"Annual network-position support status is `{status}`; no value is imputed.")
    else:
        network_metrics = st.columns(4)
        network_metrics[0].metric("Degree", metric_text(profile, "degree", ",.0f"))
        network_metrics[1].metric("PageRank", metric_text(profile, "pagerank", ".4g"))
        network_metrics[2].metric("Betweenness", metric_text(profile, "betweenness", ".4g"))
        network_metrics[3].metric("Bridge score", metric_text(profile, "bridge_score", ".4g"))
        show_data(
            profile.to_frame().T,
            columns=[
                "annual_graph_year",
                "annual_graph_boundary",
                "annual_network_support_status",
                "degree",
                "pagerank",
                "betweenness",
                "betweenness_method",
                "bridge_score",
                "community_id",
                "community_continuity_id",
                "community_status",
            ],
        )
        st.caption(
            "Network position uses the separately bounded complete "
            f"{int(profile['annual_graph_year'])} co-authorship graph, not the selected rolling "
            "window and not a global university score."
        )

    st.header("7. Citation influence")
    if profile is None or not str(profile.get("citation_flow_support_status", "")).startswith(
        "available"
    ):
        status = (
            str(profile["citation_flow_support_status"])
            if profile is not None and pd.notna(profile["citation_flow_support_status"])
            else "unavailable"
        )
        st.info(f"Citation-flow support status is `{status}`; no influence value is imputed.")
    else:
        citation_metrics = st.columns(4)
        citation_metrics[0].metric(
            "Incoming full citation flow", metric_text(profile, "citation_flow_in_full", ",.0f")
        )
        citation_metrics[1].metric(
            "Incoming fractional flow",
            metric_text(profile, "citation_flow_in_fractional", ",.2f"),
        )
        citation_metrics[2].metric(
            "Outgoing full citation flow", metric_text(profile, "citation_flow_out_full", ",.0f")
        )
        citation_metrics[3].metric(
            "Outgoing fractional flow",
            metric_text(profile, "citation_flow_out_fractional", ",.2f"),
        )
        show_data(
            profile.to_frame().T,
            columns=[
                "citation_flow_year",
                "citation_flow_boundary",
                "citation_flow_support_status",
                "citation_flow_in_full",
                "citation_flow_in_fractional",
                "citation_flow_fractional_in_strength",
                "citation_flow_out_full",
                "citation_flow_out_fractional",
            ],
        )
    st.caption(
        "Citation flow is a directed, closed-corpus knowledge-flow proxy for the separately "
        "labelled complete year. It is not co-authorship, causal impact, or institutional quality."
    )

    st.header("8. Research-neighbour institutions")
    st.warning(
        "Topic similarity represents research proximity, not collaboration. A nearby institution "
        "may have no co-authorship relationship with the selected school."
    )
    if profile is None:
        st.info("Research-proximity evidence is unavailable; no neighbour is imputed.")
    else:
        research_neighbors = research_neighbor_view(profile, school_index)
        if research_neighbors.empty:
            st.info(
                f"Research-proximity support status is "
                f"`{profile['topic_similarity_support_status']}` and no neighbour ID is stored; "
                "none is inferred."
            )
        else:
            proximity_metrics = st.columns(3)
            proximity_metrics[0].metric(
                "Supported neighbours",
                metric_text(profile, "topic_similarity_neighbor_count", ",.0f"),
            )
            proximity_metrics[1].metric(
                "Maximum Topic similarity",
                metric_text(profile, "topic_similarity_maximum", ".3f"),
            )
            proximity_metrics[2].metric(
                "Mean Topic similarity", metric_text(profile, "topic_similarity_mean", ".3f")
            )
            show_data(
                research_neighbors,
                columns=[
                    "proximity_rank",
                    "school_id",
                    "display_name",
                    "country_name",
                    "macro_region",
                    "index_match_status",
                ],
            )
            st.caption(
                f"Neighbour IDs use the complete {int(profile['topic_similarity_year'])} annual "
                "Topic-vector comparison boundary. The released profile stores neighbour order "
                "and summary similarities, not an invented pair score."
            )

    st.header("9. Date and data quality")
    if profile is None:
        st.info(
            "No profile row is available for this stable ID, corpus, and rolling window. "
            "All dependent values remain explicitly unavailable."
        )
    else:
        quality_metrics = st.columns(3)
        quality_metrics[0].metric(
            "Exact publication-date coverage",
            metric_text(profile, "date_coverage_ratio", ".1%"),
        )
        quality_metrics[1].metric(
            "Rolling-window coverage", metric_text(profile, "coverage_ratio", ".1%")
        )
        quality_metrics[2].metric(
            "Observed / eligible months",
            (f"{int(profile['observed_month_count'])} / {int(profile['eligible_month_count'])}"),
        )
        messages = profile_quality_messages(
            profile,
            low_date_coverage_threshold=float(
                profile_policy.get("low_date_coverage_display_threshold", 0.8)
            ),
        )
        if messages:
            for message in messages:
                st.warning(message)
        else:
            st.success(
                "No profile-level low-coverage or incomplete-window diagnostic is triggered for "
                "this selection."
            )
        show_data(
            profile.to_frame().T,
            columns=[
                "profile_support_status",
                "window_start",
                "window_end",
                "observed_month_count",
                "eligible_month_count",
                "coverage_ratio",
                "is_complete_window",
                "date_coverage_ratio",
                "date_coverage_status",
                "date_coverage_basis",
                "identity_quality_flags",
                "quality_flags",
                "publication_time_interpretation",
            ],
        )
        st.caption(
            "Coverage is disclosed for the selected corpus and boundaries. Empty, unsupported, "
            "incomplete, and low-coverage evidence is never converted to zero or otherwise imputed."
        )

elif view == "Compare Schools":
    school_index = require_table("school_index", columns=SCHOOL_INDEX_COLUMNS)
    comparison_policy = metadata.get("school_comparison")
    if not isinstance(comparison_policy, dict):
        st.error("The dashboard metadata lacks the School Comparison contract. Rebuild the bundle.")
        st.stop()
    supported_windows = comparison_policy.get("supported_rolling_window_months")
    if not isinstance(supported_windows, list) or not supported_windows:
        st.error("The dashboard metadata has no supported School Comparison rolling windows.")
        st.stop()
    window_options = sorted(
        int(value) for value in supported_windows if isinstance(value, int | float)
    )
    school_rows, school_options, school_labels, _ = school_selector_records(school_index)
    defaults = (
        school_rows.sort_values(
            ["recent_24m_work_count", "display_name", "school_id"],
            ascending=[False, True, True],
            kind="stable",
        )["school_id"]
        .astype(str)
        .head(2)
        .tolist()
    )
    comparison_controls = st.columns((2.4, 1.0))
    selected_school_ids = comparison_controls[0].multiselect(
        "Schools (select two to four)",
        school_options,
        default=defaults,
        max_selections=int(comparison_policy.get("maximum_school_count", 4)),
        format_func=school_labels.get,
        help="Selections use stable school IDs; names and countries are search labels only.",
    )
    default_window = int(comparison_policy.get("default_rolling_window_months", 24))
    selected_window = int(
        comparison_controls[1].selectbox(
            "Comparison rolling window",
            window_options,
            index=(window_options.index(default_window) if default_window in window_options else 0),
            format_func=lambda value: f"Rolling {value} months",
        )
    )
    if len(selected_school_ids) < 2:
        st.info("Select at least two institutions. A maximum of four can share the same axes.")
    else:
        profile_rows = query_school_profiles(
            DATA / "school_profiles.parquet",
            school_ids=selected_school_ids,
            corpus_view=corpus,
            window_months=selected_window,
        )
        topic_rows = query_school_topics_for_schools(
            DATA / "school_topic_profiles.parquet",
            school_ids=selected_school_ids,
            corpus_view=corpus,
            window_months=selected_window,
        )
        comparison = align_school_profiles(
            profile_rows,
            school_rows,
            school_ids=selected_school_ids,
        )
        missing_profiles = comparison.loc[
            comparison["profile_row_status"] != "available", "school_id"
        ].astype(str)
        if not missing_profiles.empty:
            st.warning(
                "No source profile exists for: "
                + ", ".join(missing_profiles)
                + ". Missing values remain unavailable rather than being set to zero."
            )

        available_windows = comparison.loc[
            comparison["profile_row_status"] == "available",
            ["window_start", "window_end", "coverage_ratio"],
        ]
        if available_windows.empty:
            st.info("No selected school has a source profile under this corpus/window selection.")
        else:
            window_starts = sorted(
                str(value) for value in available_windows["window_start"].dropna().unique()
            )
            window_ends = sorted(
                str(value) for value in available_windows["window_end"].dropna().unique()
            )
            st.caption(
                f"Exact source rows · {corpus.title()} corpus · rolling {selected_window} months · "
                f"window starts {', '.join(window_starts)} · window ends {', '.join(window_ends)}. "
                "Coverage is school-specific and remains visible in the exact table."
            )

        st.header("1. Recent output and rolling trend")
        output_order = (
            comparison.sort_values(
                ["full_work_count", "school_label"],
                ascending=[False, True],
                na_position="last",
                kind="stable",
            )["school_label"]
            .astype(str)
            .tolist()
        )
        output_figure = px.bar(
            comparison,
            x="full_work_count",
            y="school_label",
            orientation="h",
            title=f"Works in the selected rolling {selected_window}-month window",
            labels={
                "full_work_count": "Included institutional Works",
                "school_label": "Institution",
            },
            text="full_work_count",
        )
        output_figure.update_yaxes(categoryorder="array", categoryarray=output_order[::-1])
        output_figure.update_xaxes(rangemode="tozero")
        show_chart(output_figure, height=350)

        horizon_view = comparison_activity_horizons(comparison)
        horizon_figure = px.line(
            horizon_view,
            x="window_months",
            y="work_count",
            color="school_label",
            markers=True,
            title="Source-stored rolling activity horizons",
            labels={
                "window_months": "Cumulative rolling horizon (months)",
                "work_count": "Included institutional Works",
                "school_label": "Institution",
            },
        )
        horizon_figure.update_xaxes(tickmode="array", tickvals=[12, 24, 36])
        horizon_figure.update_yaxes(rangemode="tozero")
        show_chart(horizon_figure, height=390)
        st.caption(
            "The 12-, 24-, and 36-month values are cumulative rolling horizons ending at the "
            "same source month, not independent annual bins. Counts use one shared y-axis."
        )

        trend_order = (
            comparison.sort_values(
                ["rolling_12m_activity_change", "school_label"],
                ascending=[False, True],
                na_position="last",
                kind="stable",
            )["school_label"]
            .astype(str)
            .tolist()
        )
        trend_values = comparison["rolling_12m_activity_change"].dropna().astype(float)
        trend_extent = max((float(trend_values.abs().max()) if not trend_values.empty else 0), 0.05)
        trend_figure = px.bar(
            comparison,
            x="rolling_12m_activity_change",
            y="school_label",
            orientation="h",
            title="Current rolling 12 months relative to the preceding 12 months",
            labels={
                "rolling_12m_activity_change": "Relative change",
                "school_label": "Institution",
            },
            text=comparison["rolling_12m_activity_change"].map(
                lambda value: "N/A" if pd.isna(value) else f"{float(value):.1%}"
            ),
        )
        trend_figure.update_xaxes(
            range=[-trend_extent * 1.1, trend_extent * 1.1], tickformat=".0%", zeroline=True
        )
        trend_figure.update_yaxes(categoryorder="array", categoryarray=trend_order[::-1])
        show_chart(trend_figure, height=350)
        st.caption(
            "Relative change uses the source profile's immediately preceding 12-month "
            "denominator. Insufficient prior activity remains N/A rather than zero."
        )

        st.header("2. Topic distribution")
        topic_view = comparison_topic_view(
            topic_rows,
            comparison,
            top_n=int(comparison_policy.get("displayed_topic_family_limit", 6)),
        )
        if topic_view.empty:
            st.info("No observed Topic-family shares exist for these source profiles.")
        else:
            topic_view["topic_label"] = topic_view["topic_family"].map(human_label)
            topic_order = (
                topic_view.sort_values("topic_order", kind="stable")["topic_label"]
                .drop_duplicates()
                .astype(str)
                .tolist()
            )
            topic_figure = px.bar(
                topic_view,
                x="topic_family_share",
                y="topic_label",
                color="school_label",
                barmode="group",
                orientation="h",
                title="Leading observed Topic-family shares across selected schools",
                labels={
                    "topic_family_share": "Share of assigned Topic weight",
                    "topic_label": "Provisional Topic family",
                    "school_label": "Institution",
                },
                text=topic_view["topic_family_share"].map(lambda value: f"{float(value):.1%}"),
            )
            topic_figure.update_xaxes(range=[0, 1], tickformat=".0%")
            topic_figure.update_yaxes(categoryorder="array", categoryarray=topic_order[::-1])
            show_chart(topic_figure, height=440)
            show_data(
                topic_view,
                columns=[
                    "school_id",
                    "school_label",
                    "topic_family",
                    "topic_family_share",
                    "contributing_work_count",
                    "topic_profile_support_status",
                    "provisional_topic_registry",
                ],
            )
        st.warning(
            "Topic families are provisional. An absent school-Topic row is not plotted and is "
            "not converted to a zero share."
        )

        st.header("3. Collaboration orientation")
        orientation = comparison.melt(
            id_vars=["school_id", "school_label", "selection_order"],
            value_vars=[
                "international_collaboration_share",
                "cross_region_collaboration_share",
            ],
            var_name="orientation_metric",
            value_name="share",
        )
        orientation["orientation_metric"] = orientation["orientation_metric"].map(
            {
                "international_collaboration_share": "International",
                "cross_region_collaboration_share": "Cross-macro-region",
            }
        )
        orientation_figure = px.bar(
            orientation,
            x="orientation_metric",
            y="share",
            color="school_label",
            barmode="group",
            title="Collaboration orientation shares",
            labels={
                "orientation_metric": "Orientation",
                "share": "Share of included institutional Works",
                "school_label": "Institution",
            },
            text=orientation["share"].map(
                lambda value: "N/A" if pd.isna(value) else f"{float(value):.1%}"
            ),
        )
        orientation_figure.update_yaxes(range=[0, 1], tickformat=".0%")
        show_chart(orientation_figure, height=390)
        st.caption(
            "Both metrics divide source-classified collaborative Works by all included "
            "institutional Works in the selected corpus/window. The zero-to-one scale and "
            "denominator are shared across schools."
        )

        st.header("4. Partner diversity")
        partner_counts = comparison.melt(
            id_vars=["school_id", "school_label", "selection_order"],
            value_vars=[
                "partner_institution_count",
                "partner_country_count",
                "effective_partner_count",
            ],
            var_name="partner_metric",
            value_name="partner_value",
        )
        partner_counts["partner_metric"] = partner_counts["partner_metric"].map(human_label)
        partner_figure = px.bar(
            partner_counts,
            x="partner_metric",
            y="partner_value",
            color="school_label",
            barmode="group",
            title="Distinct and effective partner counts",
            labels={
                "partner_metric": "Partner-diversity measure",
                "partner_value": "Partner count",
                "school_label": "Institution",
            },
            text="partner_value",
        )
        partner_figure.update_yaxes(rangemode="tozero")
        show_chart(partner_figure, height=410)
        st.caption(
            "All schools share one zero-based count scale. Institution and country values are "
            "distinct partner counts; effective partner count is the inverse concentration of "
            "fractional partner weights. Repeat-partner ratio is retained in the exact table."
        )

        st.header("5. Annual network position")
        st.caption(
            "Each panel has a zero baseline and a scale shared by all selected schools. Panels "
            "use independent axes because degree, PageRank, betweenness, and bridge score have "
            "different units; no cross-metric comparison or composite score is implied."
        )
        centrality_columns = st.columns(2)
        for metric_index, (metric, title, axis_label, format_spec) in enumerate(
            (
                ("degree", "Degree", "Annual co-authorship partners", ",.0f"),
                ("pagerank", "PageRank", "Annual PageRank", ".4f"),
                ("betweenness", "Betweenness", "Annual betweenness", ".4f"),
                ("bridge_score", "Bridge score", "Annual bridge score", ".4f"),
            )
        ):
            metric_frame = comparison.sort_values(
                [metric, "school_label"],
                ascending=[False, True],
                na_position="last",
                kind="stable",
            )
            metric_order = metric_frame["school_label"].astype(str).tolist()
            metric_text_values = metric_frame[metric].map(
                lambda value, spec=format_spec: (
                    "N/A" if pd.isna(value) else format(float(value), spec)
                )
            )
            centrality_figure = px.bar(
                metric_frame,
                x=metric,
                y="school_label",
                orientation="h",
                title=title,
                labels={metric: axis_label, "school_label": "Institution"},
                text=metric_text_values,
            )
            centrality_figure.update_xaxes(rangemode="tozero")
            centrality_figure.update_yaxes(categoryorder="array", categoryarray=metric_order[::-1])
            with centrality_columns[metric_index % 2]:
                show_chart(centrality_figure, height=330)
        graph_years = sorted(
            str(int(value)) for value in comparison["annual_graph_year"].dropna().unique()
        )
        graph_boundaries = sorted(
            str(value) for value in comparison["annual_graph_boundary"].dropna().unique()
        )
        st.caption(
            "Annual graph years: "
            + (", ".join(graph_years) if graph_years else "unavailable")
            + " · boundaries: "
            + ("; ".join(graph_boundaries) if graph_boundaries else "unavailable")
            + ". Unsupported network rows remain N/A."
        )

        st.header("6. Citation influence")
        citation_flows = comparison.melt(
            id_vars=["school_id", "school_label", "selection_order"],
            value_vars=["citation_flow_in_fractional", "citation_flow_out_fractional"],
            var_name="citation_direction",
            value_name="citation_value",
        )
        citation_flows["citation_direction"] = citation_flows["citation_direction"].map(
            {
                "citation_flow_in_fractional": "Incoming",
                "citation_flow_out_fractional": "Outgoing",
            }
        )
        citation_figure = px.bar(
            citation_flows,
            x="citation_direction",
            y="citation_value",
            color="school_label",
            barmode="group",
            title="Directed fractional citation flow",
            labels={
                "citation_direction": "Direction",
                "citation_value": "Fractional citation-flow weight",
                "school_label": "Institution",
            },
            text="citation_value",
        )
        citation_figure.update_yaxes(rangemode="tozero")
        show_chart(citation_figure, height=390)
        citation_years = sorted(
            str(int(value)) for value in comparison["citation_flow_year"].dropna().unique()
        )
        citation_boundaries = sorted(
            str(value) for value in comparison["citation_flow_boundary"].dropna().unique()
        )
        st.caption(
            "Incoming and outgoing values use one shared zero-based fractional-weight scale. "
            "Citation flow is a directed closed-corpus knowledge-flow proxy, not co-authorship, "
            "causal influence, or institutional quality. Years: "
            + (", ".join(citation_years) if citation_years else "unavailable")
            + " · boundaries: "
            + ("; ".join(citation_boundaries) if citation_boundaries else "unavailable")
            + "."
        )

        st.header("7. Exact Profile source metrics and boundaries")
        show_data(
            comparison,
            columns=[
                "school_id",
                "display_name",
                "country_name",
                "macro_region",
                "subregion",
                "institution_category",
                "corpus_view",
                "window_start",
                "window_end",
                "window_months",
                "profile_row_status",
                "profile_support_status",
                "full_work_count",
                "fractional_work_count",
                "recent_12m_work_count",
                "recent_24m_work_count",
                "recent_36m_work_count",
                "rolling_12m_activity_change",
                "rolling_12m_fractional_activity_change",
                "momentum_support_status",
                "international_collaboration_share",
                "cross_region_collaboration_share",
                "partner_institution_count",
                "partner_country_count",
                "effective_partner_count",
                "repeat_partner_ratio",
                "annual_graph_year",
                "annual_graph_boundary",
                "annual_network_support_status",
                "degree",
                "pagerank",
                "betweenness",
                "betweenness_method",
                "bridge_score",
                "citation_flow_year",
                "citation_flow_boundary",
                "citation_flow_support_status",
                "citation_flow_in_full",
                "citation_flow_in_fractional",
                "citation_flow_out_full",
                "citation_flow_out_fractional",
                "coverage_ratio",
                "date_coverage_ratio",
                "date_coverage_status",
                "identity_status",
                "identity_resolution_confidence",
                "identity_quality_flags",
                "quality_flags",
            ],
        )
        st.caption(
            "Compare Schools and School Profile query the same exact source rows by stable school "
            "ID, corpus, school hierarchy, and rolling-window length. No per-school normalization "
            "or missing-value imputation is applied. Every chart shares its scale across schools; "
            "different-unit panels remain separate. This is not a university ranking, an "
            "admissions recommendation, or a universal-best-school comparison."
        )

elif view == "Overview":
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

elif view == "School Ego Map":
    school_index = require_table(
        "school_index",
        columns={
            "school_id",
            "display_name",
            "country_name",
            "macro_region",
            "recent_24m_work_count",
            "date_coverage_ratio",
            "in_prior_visualization_core",
            "has_retained_ego_partners",
        },
    )
    school_policy = metadata.get("school_ego_map")
    if not isinstance(school_policy, dict):
        st.error("The dashboard metadata lacks the School Ego Map contract. Rebuild the bundle.")
        st.stop()
    period_rows = school_policy.get("periods")
    if not isinstance(period_rows, list) or not period_rows:
        st.error("The dashboard metadata has no supported School Ego Map periods.")
        st.stop()
    periods = {
        str(row["period_key"]): str(row["period_label"])
        for row in period_rows
        if isinstance(row, dict) and "period_key" in row and "period_label" in row
    }
    if not periods:
        st.error("The dashboard metadata has no valid School Ego Map period labels.")
        st.stop()

    school_rows = school_index.sort_values(
        ["display_name", "school_id"], kind="stable"
    ).reset_index(drop=True)
    school_options = [str(value) for value in school_rows["school_id"]]
    school_labels = {
        str(row.school_id): (
            f"{row.display_name} · {row.country_name or 'Unknown country'} · {row.school_id}"
        )
        for row in school_rows.itertuples(index=False)
    }
    defaults = school_rows.loc[school_rows["has_retained_ego_partners"].astype(bool)].sort_values(
        ["recent_24m_work_count", "display_name", "school_id"],
        ascending=[False, True, True],
        kind="stable",
    )
    default_school = str(defaults.iloc[0]["school_id"]) if not defaults.empty else school_options[0]
    selected_school_id = st.selectbox(
        "School (type a name, country, or stable ID)",
        school_options,
        index=school_options.index(default_school),
        format_func=school_labels.get,
        help=(
            "Search covers the complete eligible school index. Names are labels; the selected "
            "stable ID is the query key."
        ),
    )
    selected_school = school_rows.loc[
        school_rows["school_id"].astype(str) == selected_school_id
    ].iloc[0]
    identity_columns = st.columns(4)
    identity_columns[0].metric("Stable school ID", selected_school_id)
    identity_columns[1].metric("Country", str(selected_school["country_name"]))
    identity_columns[2].metric("Macro-region", str(selected_school["macro_region"]))
    identity_columns[3].metric(
        "Exact-date coverage", f"{float(selected_school['date_coverage_ratio']):.1%}"
    )
    if not bool(selected_school["in_prior_visualization_core"]):
        st.success(
            "This school is outside the prior thresholded visualization core. Its partners remain "
            "available because this page queries the per-school index directly."
        )

    control_columns = st.columns(4)
    selected_period = control_columns[0].selectbox(
        "Collaboration period",
        tuple(periods),
        format_func=periods.get,
    )
    level_labels = {
        "Partner institutions": "institution",
        "Partner countries": "country",
        "Partner macro-regions": "macro_region",
    }
    selected_level_label = control_columns[1].selectbox("Partner level", tuple(level_labels))
    metric_by_label = {label: metric for metric, label in EGO_METRIC_LABELS.items()}
    selected_metric_label = control_columns[2].selectbox("Ego-map metric", tuple(metric_by_label))
    top_n = int(
        control_columns[3].number_input(
            "Top partners",
            min_value=1,
            max_value=int(school_policy.get("retained_partner_limit_per_school_period", 50)),
            value=12,
            step=1,
        )
    )
    selection = SchoolEgoSelection(
        school_id=selected_school_id,
        corpus_view=corpus,
        period_key=selected_period,
        level=cast(SchoolEgoLevel, level_labels[selected_level_label]),
        metric=cast(SchoolEgoMetric, metric_by_label[selected_metric_label]),
        top_n=top_n,
    )
    partners = query_school_ego_partners(
        DATA / "school_ego_partners.parquet",
        school_id=selection.school_id,
        corpus_view=selection.corpus_view,
        period_key=selection.period_key,
    )
    if partners.empty:
        show_empty(
            "This exact school, corpus, and period has no retained collaboration partner. "
            "Choose another corpus or period; no missing value is imputed."
        )
    else:
        geography_anchors = require_table(
            "geography_anchors",
            columns={
                "geographic_level",
                "geography",
                "latitude",
                "longitude",
                "coordinate_source",
            },
        )
        ego_view = build_school_ego_view(partners, geography_anchors, selection)
        mapped_view = ego_view.loc[ego_view["is_mappable"].astype(bool)].copy()
        unmapped_view = ego_view.loc[~ego_view["is_mappable"].astype(bool)].copy()
        display_metrics = st.columns(4)
        display_metrics[0].metric("Retained partner rows", len(ego_view))
        display_metrics[1].metric("Mapped partner rows", len(mapped_view))
        display_metrics[2].metric("Unmapped exact rows", len(unmapped_view))
        display_metrics[3].metric("Period", periods[selected_period])

        map_column, table_column = st.columns((1.65, 1.0))
        with map_column:
            st.subheader("School-centered partner map")
            if mapped_view.empty:
                st.info(
                    "No selected row has both source and target sourced coordinates. Exact "
                    "unmapped partner values remain available below."
                )
            else:
                show_chart(
                    build_school_ego_map_figure(mapped_view, selection),
                    height=610,
                    cartesian=False,
                )
        exact_columns = [
            "display_rank",
            "target_name",
            "target_id",
            "target_country_name",
            "target_macro_region",
            "selected_value",
            "fractional_count",
            "normalized_intensity",
            "persistence",
            "institution_partner_count",
        ]
        with table_column:
            st.subheader("Exact mapped partners")
            if mapped_view.empty:
                st.caption("No rows can be mapped with sourced endpoint coordinates.")
            else:
                show_data(mapped_view, columns=exact_columns)
        st.caption(
            "Every arc and marker uses the same exact selected value shown in the adjacent mapped "
            "table. Stable source and partner IDs remain visible; Top partners reranks only the "
            "retained per-school rows and never consults a global edge threshold."
        )
        if not unmapped_view.empty:
            with st.expander("Exact partners without complete sourced coordinates"):
                show_data(unmapped_view, columns=exact_columns)
                st.caption(
                    "These rows are retained in the exact result but cannot be drawn; "
                    "no coordinate is guessed or imputed."
                )
        with st.expander("Metric, period, and source details"):
            detail_columns = [
                column
                for column in (
                    "period_label",
                    "time_basis",
                    "source_id",
                    "source_name",
                    "target_id",
                    "target_name",
                    "full_count",
                    "fractional_count",
                    "source_work_count",
                    "target_work_count",
                    "normalized_intensity",
                    "persistence",
                    "persistence_definition",
                    "source_partner_index",
                    "metric_definition",
                    "line_width_definition",
                )
                if column in ego_view.columns
            ]
            show_data(ego_view, columns=detail_columns)
            st.caption(
                "Country and macro-region rows aggregate only retained institution partners. "
                "Fractional volume is summed; normalized intensity and persistence are "
                "fractional-volume-weighted means."
            )

elif view == "Region trends":
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

elif view == "Geographic flows":
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
            "macro_region",
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
        arc_controls = st.columns([1.0, 1.25, 1.25])
        with arc_controls[0]:
            top_n = int(
                st.number_input(
                    "Top cross-geography flows",
                    min_value=1,
                    max_value=50,
                    value=12,
                    step=1,
                )
            )
        with arc_controls[1]:
            minimum_weight = float(
                st.number_input(
                    "Minimum collaboration weight",
                    min_value=0.0,
                    value=0.0,
                    step=1.0,
                    format="%.2f",
                )
            )
        with arc_controls[2]:
            minimum_partner_share = (
                float(
                    st.slider(
                        "Minimum partner share",
                        min_value=0,
                        max_value=100,
                        value=0,
                        step=1,
                        format="%d%%",
                    )
                )
                / 100.0
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
        complete_flow_view = build_flow_view(
            matrix, geography_outputs, geography_anchors, selection
        )
        display_policy = FlowDisplayPolicy(
            top_n=top_n,
            minimum_weight=minimum_weight,
            minimum_partner_share=minimum_partner_share,
        )
        flow_view = filter_readable_flows(complete_flow_view, display_policy)
        st.caption(METRIC_DEFINITIONS[flow_metric])
        st.caption(
            f"Stable width calibration (never rescaled to the filtered subset): "
            f"{FLOW_LINE_WIDTH_DEFINITIONS[flow_metric]}."
        )
        if flow_metric == "normalized_intensity":
            st.info(
                "Normalized intensity always uses fractional flow by definition; the counting "
                "control remains visible for the exact companion volumes and partner share."
            )
        if complete_flow_view.empty:
            show_empty("The selected source has no observed collaboration flow in this window.")
        elif flow_view.empty:
            show_empty(
                "No flow passes the display thresholds. Reduce the minimum weight or partner "
                "share; the underlying processed flow data are unchanged."
            )
        else:
            displayed_arc_count = int((~flow_view["is_internal"].astype(bool)).sum())
            available_arc_count = int((~complete_flow_view["is_internal"].astype(bool)).sum())
            st.caption(
                f"Displaying {displayed_arc_count:,} of {available_arc_count:,} observed "
                "cross-geography flows after thresholds and deterministic Top N ranking. "
                "Internal flow, when qualifying, is shown at the selected-source marker and "
                "does not consume an arc slot."
            )
            map_tab, matrix_tab = st.tabs(["Flow map", "Origin-destination matrix"])
            with map_tab:
                show_chart(
                    build_flow_map_figure(flow_view, selection),
                    height=580,
                    cartesian=False,
                )
                st.caption(
                    "Great-circle arcs use sourced display anchors. Arc width follows the fixed "
                    "formula above, target macro-region controls arc and partner color, and "
                    "macro-region labels expose values without requiring hover."
                )
            with matrix_tab:
                matrix_height = min(900, max(360, 34 * len(flow_view) + 180))
                show_chart(
                    build_flow_matrix_figure(flow_view, selection),
                    height=matrix_height,
                )
                st.caption(
                    "This filtered selected-origin row contains exactly the same destinations and "
                    "values as the map. A hidden thresholded flow is not zero, and an absent "
                    "sparse row is not an imputed zero."
                )
            st.subheader("Exact displayed flows")
            show_data(
                flow_view,
                columns=[
                    "display_rank",
                    "source_geography",
                    "source_display_name",
                    "target_geography",
                    "target_display_name",
                    "target_macro_region",
                    "selected_value",
                    "selected_weight",
                    "full_count",
                    "fractional_count",
                    "partner_share",
                    "normalized_intensity",
                    "calibrated_width_px",
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

elif view == "Institutional network":
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

elif view == "Institution explorer":
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

elif view == "Topic-family comparison":
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

elif view == "Methods and limitations":
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

elif view == "Data quality":
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
