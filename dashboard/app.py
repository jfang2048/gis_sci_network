"""Processed-data-only Streamlit dashboard for the GIS collaboration network."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

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


@st.cache_data(show_spinner=False)
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


metadata = load_metadata()
if not metadata:
    st.error(
        "Dashboard data are missing. Run "
        "`uv run python -m gisnet.cli build-dashboard-data --resume`."
    )
    st.stop()

trends = load_table("trends")
matrix = load_table("matrix")
map_nodes = load_table("map_nodes")
map_edges = load_table("map_edges")
map_coverage = load_table("map_coverage")
network_nodes = load_table("network_nodes")
network_edges = load_table("network_edges")
network_accessibility = load_table("network_accessibility")
graph_metrics = load_table("graph_metrics")
sensitivity = load_table("sensitivity")
topics = load_table("topics")
community_continuity = load_table("community_continuity")
community_transitions = load_table("community_transitions")

if graph_metrics.empty:
    st.error("The dashboard snapshot is incomplete: graph metrics are unavailable.")
    st.stop()

years = sorted(int(value) for value in graph_metrics["year"].dropna().unique())
corpora = sorted(str(value) for value in graph_metrics["corpus_view"].dropna().unique())
hierarchies = sorted(str(value) for value in graph_metrics["hierarchy_view"].dropna().unique())
region_pairs = sorted(
    str(value) for value in map_edges.get("macro_region_pair", []).dropna().unique()
)
countries = sorted(str(value) for value in map_nodes.get("country_name", []).dropna().unique())
subregions = sorted(str(value) for value in map_nodes.get("subregion", []).dropna().unique())
institution_types = sorted(
    str(value) for value in map_nodes.get("institution_category", []).dropna().unique()
)
topic_families = sorted(str(value) for value in topics.get("topic_family", []).dropna().unique())

st.sidebar.title("GIS Network")
page = st.sidebar.selectbox("Page", PAGES)
st.sidebar.subheader("Global filters")
year = st.sidebar.select_slider("Year", options=years, value=years[-1])
corpus = st.sidebar.selectbox("Corpus view", corpora, index=corpora.index("broad"))
hierarchy = st.sidebar.selectbox(
    "Hierarchy view", hierarchies, index=hierarchies.index("organization")
)
counting = st.sidebar.radio("Counting method", ("Fractional", "Full"), horizontal=True)
region_pair = st.sidebar.selectbox("Macro-region pair", ("All", *region_pairs))
country = st.sidebar.selectbox("Country", ("All", *countries))
subregion = st.sidebar.selectbox("Subregion", ("All", *subregions))
institution_type = st.sidebar.selectbox("Institution type", ("All", *institution_types))
topic_family = st.sidebar.selectbox("Topic family", ("All", *topic_families))
consortium_policy = st.sidebar.selectbox(
    "Consortium policy",
    ("Primary configured policy", "Exclude warning-size consortium edges"),
)
st.sidebar.divider()
st.sidebar.caption(f"Data: {metadata.get('data_version', 'unknown')}")
st.sidebar.caption(f"Methods: {metadata.get('methods_version', 'unknown')}")
st.sidebar.caption("Local processed snapshot; ordinary viewing makes no OpenAlex requests.")

st.title("Dynamic GIS Scientific Collaboration Network")
st.caption(
    "Institutional co-authorship across Europe, Asia, and the Americas · 2010-2025 complete years"
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
    if region_pair != "All":
        view_trends = view_trends.loc[view_trends["region_pair"] == region_pair]
    else:
        view_trends = view_trends.loc[view_trends["source_region"] != view_trends["target_region"]]
    if view_trends.empty:
        show_empty("Choose a different macro-region pair.")
    else:
        figure = px.line(
            view_trends.sort_values("year"),
            x="year",
            y=weight_column,
            color="region_pair",
            markers=True,
            title=f"Regional collaboration over time — {counting.lower()} counting",
            labels={weight_column: f"{counting} collaboration weight", "year": "Publication year"},
        )
        figure.add_vline(x=year, line_dash="dot", line_color="#0f172a")
        st.plotly_chart(figure, width="stretch")
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
    st.header("Region trends and collaboration matrix")
    view_trends = trends.loc[
        (trends["corpus_view"] == corpus) & (trends["hierarchy_view"] == hierarchy)
    ].copy()
    if region_pair != "All":
        view_trends = view_trends.loc[view_trends["region_pair"] == region_pair]
    if view_trends.empty:
        show_empty("Choose another region pair or view.")
    else:
        figure = px.line(
            view_trends.sort_values("year"),
            x="year",
            y=weight_column,
            color="region_pair",
            markers=True,
            labels={weight_column: f"{counting} collaboration weight"},
        )
        figure.add_vline(x=year, line_dash="dot")
        st.plotly_chart(figure, width="stretch")
    cells = filtered_view(matrix, year, corpus, hierarchy)
    cells = cells.loc[cells["geographic_level"] == "macro_region"].copy()
    if cells.empty:
        show_empty("No matrix is available for this year and view.")
    else:
        labels = sorted(set(cells["source_geography"]) | set(cells["target_geography"]))
        grid = pd.DataFrame(index=labels, columns=labels, dtype=float)
        for _, cell in cells.iterrows():
            value = float(cell[weight_column])
            grid.loc[cell["source_geography"], cell["target_geography"]] = value
            grid.loc[cell["target_geography"], cell["source_geography"]] = value
        figure = px.imshow(
            grid,
            text_auto=".3g",
            color_continuous_scale="Blues",
            title=f"{year} macro-region matrix — {counting.lower()} weight",
            labels={"color": counting},
        )
        st.plotly_chart(figure, width="stretch")
        st.caption("Blank cells are missing/no observed flow, not silently imputed zeros.")
        st.dataframe(
            cells[
                [
                    "source_geography",
                    "target_geography",
                    "full_count",
                    "fractional_count",
                    "normalized_share",
                    "cell_status",
                ]
            ],
            width="stretch",
            hide_index=True,
        )

elif page == "Geographic map":
    st.header("Geographic collaboration map")
    nodes = filtered_view(map_nodes, year, corpus, hierarchy)
    edges = filtered_view(map_edges, year, corpus, hierarchy)
    if region_pair != "All":
        edges = edges.loc[edges["macro_region_pair"] == region_pair]
    if country != "All":
        nodes = nodes.loc[nodes["country_name"] == country]
        edges = edges.loc[
            (edges["source_country"] == country) | (edges["target_country"] == country)
        ]
    if subregion != "All":
        nodes = nodes.loc[nodes["subregion"] == subregion]
        edges = edges.loc[
            (edges["source_subregion"] == subregion) | (edges["target_subregion"] == subregion)
        ]
    if institution_type != "All":
        nodes = nodes.loc[nodes["institution_category"] == institution_type]
        edges = edges.loc[
            (edges["source_institution_type"] == institution_type)
            | (edges["target_institution_type"] == institution_type)
        ]
    if topic_family != "All":
        edges = edges.loc[edges["topic_families"].apply(lambda values: topic_family in values)]
    if consortium_policy.startswith("Exclude"):
        edges = edges.loc[edges["large_consortium_work_count"] == 0]
    if nodes.empty:
        show_empty("Coordinate coverage is sparse; broaden the country/type filters.")
    else:
        edge_limit = st.slider("Visible edge limit", 0, 500, min(200, len(edges)), step=25)
        edges = edges.nsmallest(edge_limit, "default_edge_rank") if edge_limit else edges.iloc[0:0]
        figure = go.Figure()
        if not edges.empty:
            longitudes: list[float | None] = []
            latitudes: list[float | None] = []
            for _, edge in edges.iterrows():
                longitudes.extend([edge["source_longitude"], edge["target_longitude"], None])
                latitudes.extend([edge["source_latitude"], edge["target_latitude"], None])
            figure.add_trace(
                go.Scattergeo(
                    lon=longitudes,
                    lat=latitudes,
                    mode="lines",
                    line={"width": 0.8, "color": "rgba(37,99,235,0.35)"},
                    name=f"Top {len(edges)} edges",
                    hoverinfo="skip",
                )
            )
        figure.add_trace(
            go.Scattergeo(
                lon=nodes["longitude"],
                lat=nodes["latitude"],
                text=nodes["display_name"] + " · " + nodes["country_name"],
                customdata=nodes[["institution_id", "work_count"]],
                hovertemplate=(
                    "%{text}<br>ID %{customdata[0]}<br>Works %{customdata[1]}<extra></extra>"
                ),
                mode="markers",
                marker={"size": 8, "color": "#dc2626", "line": {"width": 0.5, "color": "white"}},
                name="Institutions with sourced coordinates",
            )
        )
        figure.update_geos(
            showland=True, landcolor="#f1f5f9", showcountries=True, projection_type="natural earth"
        )
        figure.update_layout(height=650, margin={"l": 0, "r": 0, "t": 20, "b": 0})
        st.plotly_chart(figure, width="stretch")
    coverage = filtered_view(map_coverage, year, corpus, hierarchy)
    if not coverage.empty:
        coverage_row = coverage.iloc[0]
        st.warning(
            f"Coordinate coverage is {coverage_row['node_coordinate_coverage_share']:.2%}: "
            f"{int(coverage_row['coordinate_node_count']):,} of "
            f"{int(coverage_row['total_node_count']):,} node observations. "
            "No coordinates are invented."
        )
    st.caption(
        "Default edge ranking uses the explicitly non-primary visualization score; "
        "the limit is visible above."
    )

elif page == "Institutional network":
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
            f"Node size = {size_metric}; node color = {color_metric}; edge width represents "
            f"{counting.lower()} collaboration weight; visible minimum = {minimum_weight:.4g}."
        )
    summary = filtered_view(network_accessibility, year, corpus, hierarchy)
    if not summary.empty:
        st.info(summary.iloc[0]["summary_text"])

elif page == "Institution explorer":
    st.header("Institution-pair explorer")
    pair_data = network_edges.loc[
        (network_edges["corpus_view"] == corpus) & (network_edges["hierarchy_view"] == hierarchy)
    ].copy()
    labels: dict[str, str] = {}
    for _, edge in (
        pair_data[["source_id", "source_name", "target_id", "target_name"]]
        .drop_duplicates()
        .iterrows()
    ):
        labels[str(edge["source_id"])] = f"{edge['source_name']} [{edge['source_id']}]"
        labels[str(edge["target_id"])] = f"{edge['target_name']} [{edge['target_id']}]"
    ordered_ids = sorted(labels, key=lambda identifier: (labels[identifier].casefold(), identifier))
    if len(ordered_ids) < 2:
        show_empty("No institution pairs are available in this view.")
    else:
        left, right = st.columns(2)
        institution_a = left.selectbox("Institution A", ordered_ids, format_func=labels.get)
        institution_b = right.selectbox(
            "Institution B", ordered_ids, index=min(1, len(ordered_ids) - 1), format_func=labels.get
        )
        source_id, target_id = sorted((institution_a, institution_b))
        pair = pair_data.loc[
            (pair_data["source_id"] == source_id) & (pair_data["target_id"] == target_id)
        ].copy()
        if pair.empty:
            show_empty("This thresholded public snapshot has no visible edge for the selected IDs.")
        else:
            all_years = pd.DataFrame({"year": years})
            pair = all_years.merge(pair, on="year", how="left")
            pair["full_count"] = pair["full_count"].fillna(0)
            pair["fractional_count"] = pair["fractional_count"].fillna(0.0)
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
