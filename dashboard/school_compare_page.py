"""Aligned stable-ID School Comparison page renderer."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dashboard_components import human_label, show_chart, show_data
from dashboard_data_access import require_table
from dashboard_school_common import SCHOOL_INDEX_COLUMNS, school_selector_records

from gisnet.visualization.school_compare import (
    align_school_profiles,
    comparison_activity_horizons,
    comparison_topic_view,
)
from gisnet.visualization.school_profile import (
    query_school_profiles,
    query_school_topics_for_schools,
)


def render_school_comparison(
    *,
    data_dir: Path,
    metadata: dict[str, object],
    corpus: str,
) -> None:
    school_index = require_table(data_dir, "school_index", columns=SCHOOL_INDEX_COLUMNS)
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
            data_dir / "school_profiles.parquet",
            school_ids=selected_school_ids,
            corpus_view=corpus,
            window_months=selected_window,
        )
        topic_rows = query_school_topics_for_schools(
            data_dir / "school_topic_profiles.parquet",
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
