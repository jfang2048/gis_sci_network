"""Stable-ID helpers for the public institution-pair explorer."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd  # type: ignore[import-untyped]


def institution_labels(edges: pd.DataFrame) -> dict[str, str]:
    """Return unambiguous display labels keyed by stable institution ID."""
    labels: dict[str, str] = {}
    columns = ("source_id", "source_name", "target_id", "target_name")
    for source_id, source_name, target_id, target_name in edges.loc[:, columns].itertuples(
        index=False, name=None
    ):
        labels[str(source_id)] = f"{source_name} [{source_id}]"
        labels[str(target_id)] = f"{target_name} [{target_id}]"
    return labels


def build_pair_timeline(
    edges: pd.DataFrame,
    institution_a: str,
    institution_b: str,
    *,
    years: Iterable[int],
) -> pd.DataFrame:
    """Build a complete-year series for one stable-ID pair.

    Missing edge-years mean zero observed counts in the thresholded public snapshot.
    Normalized intensity and persistence remain missing because they are undefined when
    no stored edge row exists.
    """
    if institution_a == institution_b:
        raise ValueError("choose two different institution IDs")
    source_id, target_id = sorted((str(institution_a), str(institution_b)))
    pair = edges.loc[(edges["source_id"] == source_id) & (edges["target_id"] == target_id)].copy()
    if pair["year"].duplicated().any():
        raise ValueError("institution pair has more than one row for a year")
    complete = pd.DataFrame({"year": sorted({int(year) for year in years})})
    timeline = complete.merge(pair, on="year", how="left", validate="one_to_one")
    timeline["source_id"] = timeline["source_id"].fillna(source_id)
    timeline["target_id"] = timeline["target_id"].fillna(target_id)
    timeline["full_count"] = timeline["full_count"].fillna(0).astype("int64")
    timeline["fractional_count"] = timeline["fractional_count"].fillna(0.0)
    for column in ("topic_families", "work_ids_sample"):
        timeline[column] = timeline[column].apply(
            lambda value: [] if pd.api.types.is_scalar(value) and pd.isna(value) else value
        )
    return timeline


def identity_rows(
    identities: pd.DataFrame, institution_id: str, *, hierarchy_view: str
) -> pd.DataFrame:
    """Return organization and umbrella identities associated with a selected node ID."""
    columns = [
        "organization_id",
        "organization_name",
        "umbrella_id",
        "umbrella_name",
        "is_collapsed",
    ]
    if identities.empty:
        return pd.DataFrame(columns=columns)
    if hierarchy_view == "organization":
        selected = identities.loc[identities["organization_id"] == institution_id]
    elif hierarchy_view == "umbrella":
        selected = identities.loc[identities["umbrella_id"] == institution_id]
    else:
        raise ValueError(f"unsupported hierarchy view: {hierarchy_view}")
    return selected.loc[:, columns].sort_values(
        ["organization_name", "organization_id"], kind="stable"
    )
