"""Stable-ID School Profile page renderer."""

from __future__ import annotations

from pathlib import Path
from typing import cast

import pandas as pd
import plotly.express as px
import streamlit as st
from dashboard_components import metric_text, show_chart, show_data
from dashboard_data_access import require_table
from dashboard_school_common import SCHOOL_INDEX_COLUMNS, school_selector_records

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
    query_school_topics,
    research_neighbor_view,
)


def render_school_profile(
    *,
    data_dir: Path,
    metadata: dict[str, object],
    corpus: str,
) -> None:
    school_index = require_table(data_dir, "school_index", columns=SCHOOL_INDEX_COLUMNS)
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
        data_dir / "school_profiles.parquet",
        school_id=selected_school_id,
        corpus_view=corpus,
        window_months=selected_window,
    )
    profile = profile_rows.iloc[0] if not profile_rows.empty else None
    topic_view = query_school_topics(
        data_dir / "school_topic_profiles.parquet",
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
        data_dir / "school_ego_partners.parquet",
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
            data_dir,
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
