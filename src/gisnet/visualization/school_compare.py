"""Pure alignment helpers for exact, non-imputed school comparison views."""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd  # type: ignore[import-untyped]

_IDENTITY_COLUMNS = [
    "school_id",
    "display_name",
    "country_name",
    "macro_region",
    "subregion",
    "institution_category",
]
_ROLLING_WINDOWS = (12, 24, 36)


def align_school_profiles(
    profiles: pd.DataFrame,
    school_index: pd.DataFrame,
    *,
    school_ids: Sequence[str],
) -> pd.DataFrame:
    """Align selected IDs in user order while retaining an explicit missing-profile row."""
    selected_ids = _validate_school_ids(school_ids)
    missing_index = sorted(set(_IDENTITY_COLUMNS).difference(school_index.columns))
    if missing_index:
        raise ValueError(f"school index lacks required columns: {missing_index}")
    if "school_id" not in profiles.columns:
        raise ValueError("school profiles lack required column: school_id")
    duplicate_profile_ids = sorted(
        str(value)
        for value in profiles.loc[
            profiles.duplicated("school_id", keep=False), "school_id"
        ].unique()
    )
    if duplicate_profile_ids:
        raise ValueError(f"school profiles contain duplicate stable IDs: {duplicate_profile_ids}")

    selection_order = pd.DataFrame(
        {"school_id": selected_ids, "selection_order": range(len(selected_ids))}
    )
    identities = school_index.loc[
        school_index["school_id"].astype(str).isin(selected_ids), _IDENTITY_COLUMNS
    ].copy()
    identities["school_id"] = identities["school_id"].astype(str)
    identities = identities.drop_duplicates("school_id", keep="first")
    missing_ids = [
        school_id for school_id in selected_ids if school_id not in set(identities["school_id"])
    ]
    if missing_ids:
        raise ValueError(
            f"selected stable IDs are absent from the complete school index: {missing_ids}"
        )

    profile_values = profiles.copy()
    profile_values["school_id"] = profile_values["school_id"].astype(str)
    profile_identity_columns = set(_IDENTITY_COLUMNS).difference({"school_id"})
    profile_values = profile_values.drop(
        columns=[column for column in profile_identity_columns if column in profile_values.columns]
    )
    profile_values["profile_row_status"] = "available"
    comparison = selection_order.merge(
        identities, on="school_id", how="left", validate="one_to_one"
    )
    comparison = comparison.merge(
        profile_values,
        on="school_id",
        how="left",
        validate="one_to_one",
    )
    comparison["profile_row_status"] = comparison["profile_row_status"].fillna(
        "missing_source_profile"
    )
    comparison["school_label"] = (
        comparison["display_name"].astype(str)
        + " · "
        + comparison["country_name"].fillna("Unknown country").astype(str)
    )
    return comparison.sort_values("selection_order", kind="stable").reset_index(drop=True)


def comparison_activity_horizons(comparison: pd.DataFrame) -> pd.DataFrame:
    """Return source-stored rolling counts for common-scale comparison without imputation."""
    required = {"school_id", "school_label", "selection_order"}
    missing = sorted(required.difference(comparison.columns))
    if missing:
        raise ValueError(f"comparison rows lack required columns: {missing}")
    rows: list[dict[str, object]] = []
    for profile in comparison.to_dict("records"):
        for window_months in _ROLLING_WINDOWS:
            rows.append(
                {
                    "school_id": profile["school_id"],
                    "school_label": profile["school_label"],
                    "selection_order": profile["selection_order"],
                    "window_months": window_months,
                    "window_label": f"Rolling {window_months} months",
                    "work_count": profile.get(f"recent_{window_months}m_work_count"),
                }
            )
    return pd.DataFrame(rows)


def comparison_topic_view(
    topics: pd.DataFrame,
    comparison: pd.DataFrame,
    *,
    top_n: int = 6,
) -> pd.DataFrame:
    """Keep exact observed shares for the leading shared Topic families; never add zero rows."""
    if top_n < 1:
        raise ValueError("top_n must be positive")
    required_topics = {"school_id", "topic_family", "topic_family_share"}
    missing_topics = sorted(required_topics.difference(topics.columns))
    if missing_topics:
        raise ValueError(f"school Topic profiles lack required columns: {missing_topics}")
    required_comparison = {"school_id", "school_label", "selection_order"}
    missing_comparison = sorted(required_comparison.difference(comparison.columns))
    if missing_comparison:
        raise ValueError(f"comparison rows lack required columns: {missing_comparison}")
    if topics.empty:
        return pd.DataFrame(
            columns=[*topics.columns, "school_label", "selection_order", "topic_order"]
        )

    selected_ids = set(comparison["school_id"].astype(str))
    observed = topics.loc[topics["school_id"].astype(str).isin(selected_ids)].copy()
    if observed.empty:
        return pd.DataFrame(
            columns=[*topics.columns, "school_label", "selection_order", "topic_order"]
        )
    observed["school_id"] = observed["school_id"].astype(str)
    topic_order = (
        observed.groupby("topic_family", as_index=False, dropna=False)["topic_family_share"]
        .sum(min_count=1)
        .sort_values(
            ["topic_family_share", "topic_family"],
            ascending=[False, True],
            kind="stable",
        )["topic_family"]
        .head(top_n)
        .astype(str)
        .tolist()
    )
    observed = observed.loc[observed["topic_family"].astype(str).isin(topic_order)].copy()
    labels = comparison[["school_id", "school_label", "selection_order"]].copy()
    labels["school_id"] = labels["school_id"].astype(str)
    observed = observed.merge(labels, on="school_id", how="left", validate="many_to_one")
    observed["topic_order"] = (
        observed["topic_family"]
        .astype(str)
        .map({topic: index for index, topic in enumerate(topic_order)})
    )
    return observed.sort_values(["topic_order", "selection_order"], kind="stable").reset_index(
        drop=True
    )


def _validate_school_ids(school_ids: Sequence[str]) -> list[str]:
    if isinstance(school_ids, str):
        raise ValueError("school_ids must be a sequence of stable IDs, not one string")
    selected_ids = [str(value) for value in school_ids]
    if not selected_ids or any(not value for value in selected_ids):
        raise ValueError("school_ids cannot be empty")
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("school_ids cannot contain duplicates")
    return selected_ids
