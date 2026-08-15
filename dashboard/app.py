"""Processed-data-only Streamlit dashboard for the GIS collaboration network."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from gisnet.visualization.dashboard_filters import (
    control_is_enabled,
    dimension_options,
    filter_geographic_view,
    local_collaboration_profile,
    partner_share_view,
)
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
    "Geographic map",
    "Institutional network",
    "Institution explorer",
    "Topic-family comparison",
    "Methods and limitations",
    "Data quality",
)

st.set_page_config(
    page_title="GIS Scientific Collaboration Network",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="expanded",
)


@st.cache_data(show_spinner=False)
def load_metadata() -> dict[str, object]:
    path = DATA / "metadata.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


@st.cache_resource(show_spinner=False)
def load_table(name: str) -> pd.DataFrame:
    path = DATA / f"{name}.parquet"
    if not path.is_file():
        return pd.DataFrame()
    return pd.read_parquet(path)


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


def metric_value(row: pd.Series | None, column: str, default: float = 0.0) -> float:
    if row is None or column not in row or pd.isna(row[column]):
        return default
    return float(row[column])


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

map_nodes = load_table("map_nodes")
map_edges = load_table("map_edges")
graph_metrics = load_table("graph_metrics")
topics = load_table("topics")
trends = load_table("trends")
filter_dimensions = load_table("filter_dimensions")
if filter_dimensions.empty:
    # Older public snapshots predate the complete filter-dimension table. The fixed-layout
    # core is still substantially less coordinate-biased than the map-node fallback.
    filter_dimensions = load_table("network_nodes")
institution_identities = load_table("institution_identities")
geography_dimensions = load_table("geography_dimensions")
if geography_dimensions.empty and not map_nodes.empty:
    geography_dimensions = map_nodes[
        ["country_code", "country_name", "macro_region", "subregion"]
    ].drop_duplicates()

if graph_metrics.empty:
    st.error("The dashboard snapshot is incomplete: graph metrics are unavailable.")
    st.stop()

years = sorted(int(value) for value in graph_metrics["year"].dropna().unique())
corpora = sorted(str(value) for value in graph_metrics["corpus_view"].dropna().unique())
hierarchies = sorted(str(value) for value in graph_metrics["hierarchy_view"].dropna().unique())

st.sidebar.title("GIS Network")
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
country = st.sidebar.selectbox(
    "Country",
    ("All", *countries),
    disabled=not control_is_enabled(page, "Country"),
    help="This control is disabled when it does not affect the selected page.",
)
subregion = st.sidebar.selectbox(
    "Subregion",
    ("All", *subregions),
    disabled=not control_is_enabled(page, "Subregion"),
    help="This control is disabled when it does not affect the selected page.",
)
institution_type = st.sidebar.selectbox(
    "Institution type",
    ("All", *institution_types),
    disabled=not control_is_enabled(page, "Institution type"),
    help="This control is disabled when it does not affect the selected page.",
)
topic_family = st.sidebar.selectbox(
    "Topic family",
    ("All", *topic_families),
    disabled=not control_is_enabled(page, "Topic family"),
    help="This control is disabled when it does not affect the selected page.",
)
consortium_policy = st.sidebar.selectbox(
    "Consortium policy",
    ("Primary configured policy", "Exclude warning-size consortium edges"),
    disabled=not control_is_enabled(page, "Consortium policy"),
    help="This control is disabled when it does not affect the selected page.",
)
st.sidebar.divider()
st.sidebar.caption(f"Data: {metadata.get('data_version', 'unknown')}")
st.sidebar.caption(f"Methods: {metadata.get('methods_version', 'unknown')}")
st.sidebar.caption("Local processed snapshot; ordinary viewing makes no OpenAlex requests.")

st.title("Dynamic GIS Scientific Collaboration Network")
st.caption(
    "Institutional co-authorship across Europe, Asia, and the Americas · 2010-2025 complete years"
)
st.warning(
    "Scientific review warning: the Topic registry and corpus boundary remain provisional until "
    "the required human review is completed. No AI-generated judgment is treated as human review."
)
metadata_collapse_count = metadata.get("active_umbrella_collapse_count")
active_collapse_count = (
    int(metadata_collapse_count)
    if isinstance(metadata_collapse_count, int | float)
    else (
        int(institution_identities["is_collapsed"].fillna(False).astype(bool).sum())
        if "is_collapsed" in institution_identities.columns
        else 0
    )
)
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
    st.header("Overview")
    current = filtered_view(graph_metrics, year, corpus, hierarchy)
    row = current.iloc[0] if not current.empty else None
    columns = st.columns(5)
    columns[0].metric("Institutions", f"{int(metric_value(row, 'node_count')):,}")
    columns[1].metric("Edges", f"{int(metric_value(row, 'edge_count')):,}")
    columns[2].metric("Density", f"{metric_value(row, 'density'):.4f}")
    columns[3].metric("Modularity", f"{metric_value(row, 'modularity'):.3f}")
    columns[4].metric(
        "Largest component",
        f"{metric_value(row, 'largest_connected_component_share'):.1%}",
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
            markers=True,
            title=f"Regional partner share over time — {counting.lower()} counting",
            labels={
                "partner_share": "Share of collaboration endpoints",
                "year": "Publication year",
                "comparison": "Region / direction",
            },
        )
        figure.add_vline(x=year, line_dash="dot", line_color="#0f172a")
        figure.update_yaxes(tickformat=".0%", range=[0, 1])
        st.plotly_chart(figure, width="stretch")
        st.caption(
            "Shares use collaboration endpoints, so an internal link contributes two local "
            "endpoints while a cross-region link contributes one endpoint to each region."
        )
    st.subheader("What this snapshot contains")
    table_rows = metadata.get("tables", {})
    if isinstance(table_rows, dict):
        st.dataframe(
            pd.DataFrame(
                [
                    {"table": name, "rows": info.get("row_count"), "sha256": info.get("sha256")}
                    for name, info in table_rows.items()
                    if isinstance(info, dict)
                ]
            ),
            width="stretch",
            hide_index=True,
        )

elif page == "Region trends":
    matrix = load_table("matrix")
    st.header("Region trends and collaboration matrix")
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
            markers=True,
            title=f"Regional collaboration composition — {counting.lower()} counting",
            labels={
                "partner_share": "Share of collaboration endpoints",
                "comparison": "Region / direction",
            },
        )
        figure.add_vline(x=year, line_dash="dot")
        figure.update_yaxes(tickformat=".0%", range=[0, 1])
        st.plotly_chart(figure, width="stretch")
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
        labels = sorted(
            set(partner_cells["source_geography"]) | set(partner_cells["target_geography"])
        )
        grid = pd.DataFrame(index=labels, columns=labels, dtype=float)
        for _, cell in partner_cells.iterrows():
            value = float(cell["partner_share"])
            grid.loc[cell["source_geography"], cell["target_geography"]] = value
        figure = px.imshow(
            grid,
            text_auto=".1%",
            color_continuous_scale="Blues",
            zmin=0,
            zmax=1,
            title=f"{year} partner-share matrix — {counting.lower()} counting",
            labels={
                "x": "Partner region",
                "y": "Source region",
                "color": "Endpoint share",
            },
        )
        st.plotly_chart(figure, width="stretch")
        st.caption(
            "Each source-region row sums to 100%. Diagonal cells are within-region shares; "
            "blank cells are missing/no observed flow, not imputed zeros."
        )
        st.dataframe(
            partner_cells[
                [
                    "source_geography",
                    "target_geography",
                    "full_count",
                    "fractional_count",
                    "endpoint_weight",
                    "total_endpoint_weight",
                    "partner_share",
                ]
            ],
            width="stretch",
            hide_index=True,
        )

elif page == "Geographic map":
    map_coverage = load_table("map_coverage")
    matrix = load_table("matrix")
    current_matrix = filtered_view(matrix, year, corpus, hierarchy)
    st.header("Geographic collaboration patterns")
    st.caption(
        "The primary views below use the complete country and region flow tables, not the sparse "
        "institution-coordinate subset. Color and bar length represent proportions rather than "
        "absolute collaboration volume."
    )

    macro_profile = local_collaboration_profile(
        current_matrix,
        weight_column=weight_column,
        geographic_level="macro_region",
    )
    macro_profile = macro_profile.loc[
        macro_profile["geography"].isin(("Europe", "Asia", "Americas"))
    ].copy()
    st.subheader("Within-region collaboration share")
    if macro_profile.empty:
        show_empty("No macro-region collaboration profile is available for this selection.")
    else:
        macro_figure = px.bar(
            macro_profile,
            x="geography",
            y="local_collaboration_share",
            color="geography",
            text="local_collaboration_share",
            title=f"{year} local partner share — {counting.lower()} counting",
            labels={
                "geography": "Macro-region",
                "local_collaboration_share": "Within-region endpoint share",
            },
        )
        macro_figure.update_traces(texttemplate="%{text:.1%}", textposition="outside")
        macro_figure.update_yaxes(tickformat=".0%", range=[0, 1])
        macro_figure.update_layout(showlegend=False, height=420)
        st.plotly_chart(macro_figure, width="stretch")
        st.dataframe(
            macro_profile[
                [
                    "geography",
                    "local_collaboration_share",
                    "local_collaboration_weight",
                    "external_endpoint_weight",
                    "total_endpoint_weight",
                ]
            ],
            width="stretch",
            hide_index=True,
        )

    country_profile = local_collaboration_profile(
        current_matrix,
        weight_column=weight_column,
        geographic_level="country",
    ).rename(columns={"geography": "country_code"})
    if not country_profile.empty and not geography_dimensions.empty:
        country_profile = country_profile.merge(
            geography_dimensions,
            on="country_code",
            how="left",
            validate="one_to_one",
        )
        country_profile["country_name"] = country_profile["country_name"].fillna(
            country_profile["country_code"]
        )
    st.subheader("Domestic collaboration share by country")
    st.caption(
        "For each country, the numerator is the weighted domestic collaboration endpoints and "
        "the denominator is all weighted endpoints attached to that country; internal links count "
        "twice because both institutions are local. Hover shows the denominator, so high shares "
        "based on little activity are identifiable."
    )
    if country_profile.empty or "country_name" not in country_profile.columns:
        show_empty("No country-level collaboration profile is available for this selection.")
    else:
        country_figure = px.choropleth(
            country_profile,
            locations="country_name",
            locationmode="country names",
            color="local_collaboration_share",
            range_color=(0, 1),
            color_continuous_scale="Viridis",
            hover_name="country_name",
            hover_data={
                "country_name": False,
                "country_code": True,
                "macro_region": True,
                "local_collaboration_share": ":.1%",
                "local_collaboration_weight": ":.3g",
                "total_endpoint_weight": ":.3g",
            },
            labels={
                "local_collaboration_share": "Domestic endpoint share",
                "local_collaboration_weight": "Domestic collaboration weight",
                "total_endpoint_weight": "All endpoint weight",
                "macro_region": "Macro-region",
            },
            title=f"{year} domestic partner orientation — {counting.lower()} counting",
        )
        country_figure.update_geos(
            projection_type="natural earth",
            showframe=False,
            showcoastlines=True,
            coastlinecolor="#94a3b8",
            showland=True,
            landcolor="#f8fafc",
        )
        country_figure.update_coloraxes(colorbar_tickformat=".0%")
        country_figure.update_layout(height=600, margin={"l": 0, "r": 0, "t": 55, "b": 0})
        st.plotly_chart(country_figure, width="stretch")

    if country != "All" and not geography_dimensions.empty:
        country_matches = geography_dimensions.loc[
            geography_dimensions["country_name"] == country, "country_code"
        ]
        country_flows = partner_share_view(
            current_matrix,
            weight_column=weight_column,
            geographic_level="country",
        )
        if not country_matches.empty and not country_flows.empty:
            country_code = str(country_matches.iloc[0])
            partner_rows = country_flows.loc[
                country_flows["source_geography"] == country_code
            ].copy()
            partner_labels = geography_dimensions[["country_code", "country_name"]].rename(
                columns={
                    "country_code": "target_geography",
                    "country_name": "partner_country",
                }
            )
            partner_rows = partner_rows.merge(
                partner_labels,
                on="target_geography",
                how="left",
                validate="many_to_one",
            )
            partner_rows["partner_country"] = partner_rows["partner_country"].fillna(
                partner_rows["target_geography"]
            )
            top_partners = partner_rows.nlargest(15, "partner_share").sort_values("partner_share")
            st.subheader(f"Partner composition for {country}")
            partner_figure = px.bar(
                top_partners,
                x="partner_share",
                y="partner_country",
                orientation="h",
                text="partner_share",
                labels={
                    "partner_share": "Share of country collaboration endpoints",
                    "partner_country": "Partner country",
                },
            )
            partner_figure.update_traces(texttemplate="%{text:.1%}", textposition="outside")
            partner_figure.update_xaxes(tickformat=".0%", range=[0, 1])
            partner_figure.update_layout(height=500)
            st.plotly_chart(partner_figure, width="stretch")
            if len(partner_rows) > len(top_partners):
                st.caption(
                    "The chart shows the 15 largest shares; the full partner row sums to 100%."
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
                            "size": 6,
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
                        "size": 8,
                        "color": "#dc2626",
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
            institution_figure.update_layout(height=650, margin={"l": 0, "r": 0, "t": 20, "b": 0})
            st.plotly_chart(institution_figure, width="stretch")
            st.caption(
                f"Showing {len(visible_edges)} links ranked by {counting.lower()} weight; "
                "line width "
                "is relative within this displayed subset and is not comparable across filters."
            )

elif page == "Institutional network":
    network_nodes = load_table("network_nodes")
    network_edges = load_table("network_edges")
    network_accessibility = load_table("network_accessibility")
    community_continuity = load_table("community_continuity")
    st.header("Fixed-layout institutional network")
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
        canonical = region_pair.replace(" — ", " — ")
        pairs = edges.apply(
            lambda row: " — ".join(sorted((row["source_region"], row["target_region"]))), axis=1
        )
        edges = edges.loc[pairs == canonical]
    if consortium_policy.startswith("Exclude"):
        edges = edges.loc[edges["large_consortium_work_count"] == 0]
    available_metrics = ("work_count", "degree", "fractional_strength", "pagerank")
    size_metric = st.selectbox("Node-size metric", available_metrics, index=2)
    color_metric = st.radio(
        "Node color", ("macro_region", "community_id", "continuity_id"), horizontal=True
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
            height=700,
            xaxis={"visible": False},
            yaxis={"visible": False, "scaleanchor": "x", "scaleratio": 1},
            margin={"l": 0, "r": 0, "t": 15, "b": 0},
            legend_title=color_metric,
        )
        st.plotly_chart(figure, width="stretch")
        st.caption(
            f"Node size = {size_metric}; node color = {color_metric}; edge width is constant. "
            f"The {counting.lower()} collaboration weight controls edge inclusion, with visible "
            f"minimum {minimum_weight:.4g}."
        )
    summary = filtered_view(network_accessibility, year, corpus, hierarchy)
    if not summary.empty:
        st.info(summary.iloc[0]["summary_text"])

elif page == "Institution explorer":
    network_edges = load_table("network_edges")
    st.header("Institution-pair explorer")
    pair_data = network_edges.loc[
        (network_edges["corpus_view"] == corpus) & (network_edges["hierarchy_view"] == hierarchy)
    ].copy()
    labels = institution_labels(pair_data)
    ordered_ids = sorted(labels, key=lambda identifier: (labels[identifier].casefold(), identifier))
    if len(ordered_ids) < 2:
        show_empty("No institution pairs are available in this view.")
    else:
        left, right = st.columns(2)
        institution_a = left.selectbox("Institution A", ordered_ids, format_func=labels.get)
        institution_b = right.selectbox(
            "Institution B", ordered_ids, index=min(1, len(ordered_ids) - 1), format_func=labels.get
        )
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
                    x=pair["year"], y=pair["full_count"], name="Full count", mode="lines+markers"
                )
            )
            figure.add_trace(
                go.Scatter(
                    x=pair["year"],
                    y=pair["fractional_count"],
                    name="Fractional count",
                    mode="lines+markers",
                )
            )
            figure.update_layout(
                title=f"{labels[institution_a]} ↔ {labels[institution_b]}",
                yaxis_title="Collaboration weight",
            )
            st.plotly_chart(figure, width="stretch")
            st.dataframe(
                pair[
                    [
                        "year",
                        "full_count",
                        "fractional_count",
                        "normalized_intensity",
                        "persistence_3y",
                        "persistence_5y",
                        "topic_families",
                        "work_ids_sample",
                    ]
                ],
                width="stretch",
                hide_index=True,
            )
            st.caption(
                "Stable institution IDs are shown in brackets; missing years use zero counts "
                "and missing intensity/persistence."
            )
        st.subheader("Organization and umbrella identities")
        for selected_id, selected_label in (
            (institution_a, labels[institution_a]),
            (institution_b, labels[institution_b]),
        ):
            st.markdown(f"**{selected_label}**")
            identities = identity_rows(
                institution_identities, selected_id, hierarchy_view=hierarchy
            )
            if identities.empty:
                st.caption("No released hierarchy mapping is available for this ID.")
            else:
                st.dataframe(identities, width="stretch", hide_index=True)

elif page == "Topic-family comparison":
    st.header("Topic-family comparison")
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
            markers=True,
            labels={weight_column: f"{counting} visible-core edge weight"},
        )
        figure.add_vline(x=year, line_dash="dot")
        st.plotly_chart(figure, width="stretch")
        st.caption(
            "Topic-family comparison covers the thresholded fixed-layout core, "
            "not all stored edges."
        )

elif page == "Methods and limitations":
    st.header("Methods and limitations")
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
- Topic classifications are provisional and have not received human review.
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
    sensitivity = load_table("sensitivity")
    map_coverage = load_table("map_coverage")
    community_continuity = load_table("community_continuity")
    community_transitions = load_table("community_transitions")
    st.header("Data quality and sensitivity")
    st.subheader("Required sensitivity matrix")
    st.dataframe(
        sensitivity[
            [
                "comparison_id",
                "comparison",
                "baseline_label",
                "alternative_label",
                "absolute_relative_change",
                "major_change",
                "status",
            ]
        ],
        width="stretch",
        hide_index=True,
    )
    st.subheader("Coordinate coverage")
    coverage = filtered_view(map_coverage, year, corpus, hierarchy)
    if coverage.empty:
        show_empty("No coverage row exists for this view.")
    else:
        st.dataframe(coverage, width="stretch", hide_index=True)
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
            st.dataframe(
                transition_view["event_type"]
                .value_counts()
                .rename_axis("event_type")
                .reset_index(),
                width="stretch",
                hide_index=True,
            )
        st.caption(
            "Continuity uses deterministic adjacent-year Jaccard assignment; "
            "selected matches below 0.25 are explicitly uncertain."
        )
    st.subheader("Version and integrity metadata")
    st.json(metadata)
