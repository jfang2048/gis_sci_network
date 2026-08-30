"""Pure filters for separately interpreted annual scientific edge layers."""

from __future__ import annotations

import pandas as pd  # type: ignore[import-untyped]

_BASE_COLUMNS = {
    "year",
    "corpus_view",
    "hierarchy_view",
    "source_id",
    "target_id",
    "source_name",
    "target_name",
    "source_region",
    "target_region",
    "source_country",
    "target_country",
    "source_category",
    "target_category",
}


def scientific_layer_edge_view(
    edges: pd.DataFrame,
    *,
    year: int,
    corpus_view: str,
    hierarchy_view: str,
    value_column: str,
    directed: bool,
    limit: int,
    region_pair: str = "All",
    country_code: str | None = None,
    subregion: str | None = None,
    institution_category: str | None = None,
    minimum_value: float | None = None,
) -> pd.DataFrame:
    """Filter one layer without merging units, then rank exact rows deterministically."""
    missing = sorted((_BASE_COLUMNS | {value_column}).difference(edges.columns))
    if missing:
        raise ValueError(f"scientific layer edges lack required columns: {missing}")
    if limit <= 0:
        raise ValueError("scientific layer display limit must be positive")
    if minimum_value is not None and minimum_value < 0:
        raise ValueError("scientific layer minimum value cannot be negative")

    view = edges.loc[
        (edges["year"] == year)
        & (edges["corpus_view"] == corpus_view)
        & (edges["hierarchy_view"] == hierarchy_view)
    ].copy()
    if region_pair != "All":
        source_region, target_region = region_pair.split(" — ", maxsplit=1)
        view = view.loc[
            ((view["source_region"] == source_region) & (view["target_region"] == target_region))
            | ((view["source_region"] == target_region) & (view["target_region"] == source_region))
        ]
    if country_code is not None:
        view = view.loc[
            (view["source_country"] == country_code) | (view["target_country"] == country_code)
        ]
    if subregion is not None and {"source_subregion", "target_subregion"}.issubset(view.columns):
        view = view.loc[
            (view["source_subregion"] == subregion) | (view["target_subregion"] == subregion)
        ]
    if institution_category is not None:
        view = view.loc[
            (view["source_category"] == institution_category)
            | (view["target_category"] == institution_category)
        ]
    if minimum_value is not None:
        view = view.loc[view[value_column] >= minimum_value]

    arrow = " → " if directed else " ↔ "
    view["edge_label"] = view["source_name"].astype(str) + arrow + view["target_name"].astype(str)
    view = view.sort_values(
        [value_column, "source_name", "target_name", "source_id", "target_id"],
        ascending=[False, True, True, True, True],
        na_position="last",
        kind="stable",
    ).head(limit)
    return view.reset_index(drop=True)
