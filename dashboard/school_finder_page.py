"""School Finder page renderer."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from dashboard_components import human_label, show_data, show_empty
from dashboard_data_access import require_table
from dashboard_school_common import SCHOOL_INDEX_COLUMNS, school_selector_records

CATALOG_COLUMNS = [
    "school_id",
    "display_name",
    "institution_category",
    "country_code",
    "country_name",
    "subregion",
    "macro_region",
    "openalex_id",
    "ror_id",
    "broad_work_count",
    "strict_work_count",
    "recent_24m_work_count",
    "identity_status",
]


def institution_catalog(frame: pd.DataFrame) -> pd.DataFrame:
    """List every supplied stable ID once, without a visualization-core threshold."""
    missing = sorted(set(CATALOG_COLUMNS).difference(frame.columns))
    if missing:
        raise ValueError(f"school index lacks catalogue columns: {', '.join(missing)}")
    if frame["school_id"].isna().any() or frame["school_id"].duplicated().any():
        raise ValueError("school index requires unique, non-null stable school IDs")
    return (
        frame.loc[:, CATALOG_COLUMNS]
        .sort_values(
            ["macro_region", "country_name", "display_name", "school_id"],
            kind="stable",
            na_position="last",
        )
        .reset_index(drop=True)
    )


def render_school_finder(*, data_dir: Path) -> None:
    school_index = require_table(
        data_dir, "school_index", columns=SCHOOL_INDEX_COLUMNS | set(CATALOG_COLUMNS)
    )
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
    st.subheader("Complete institution list")
    st.caption(
        "All eligible universities and research institutions matching the geography/category "
        "filters are listed below, not just the visualization core. The CSV always contains "
        "the entire checked-in Broad-primary 2010-2025 catalogue, including Africa, Oceania, "
        "and Unknown geography. Stable school IDs support later institution-by-institution "
        "analysis."
    )
    complete_catalog = institution_catalog(school_index)
    st.write(f"Showing {len(finder_view):,} of {len(complete_catalog):,} institutions.")
    if not finder_view.empty:
        show_data(institution_catalog(finder_view))
    st.download_button(
        "Download all institutions (CSV)",
        data=complete_catalog.to_csv(index=False, lineterminator="\n").encode("utf-8"),
        file_name="gis_research_institutions_2010_2025.csv",
        mime="text/csv; charset=utf-8",
    )
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
