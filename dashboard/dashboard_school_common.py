"""Shared stable-ID school selection helpers for institution-first pages."""

from __future__ import annotations

import pandas as pd

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
