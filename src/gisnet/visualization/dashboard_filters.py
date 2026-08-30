"""Pure filtering helpers for the public dashboard."""

from __future__ import annotations

from collections.abc import Iterable

import pandas as pd  # type: ignore[import-untyped]

CONTROL_APPLICABILITY: dict[str, frozenset[str]] = {
    "School Finder": frozenset(),
    "School Profile": frozenset({"Corpus view"}),
    "Compare Schools": frozenset({"Corpus view"}),
    "Geographic Flows": frozenset(
        {
            "Corpus view",
            "Hierarchy view",
            "Counting method",
            "Macro-region pair",
            "Country",
            "Subregion",
            "Institution type",
            "Topic family",
            "Consortium policy",
        }
    ),
    "Institutional Network": frozenset(
        {
            "Year",
            "Corpus view",
            "Hierarchy view",
            "Counting method",
            "Macro-region pair",
            "Country",
            "Subregion",
            "Institution type",
            "Topic family",
            "Consortium policy",
        }
    ),
    "Global Trends": frozenset(
        {
            "Year",
            "Corpus view",
            "Hierarchy view",
            "Counting method",
            "Macro-region pair",
            "Topic family",
        }
    ),
    "Methods and Data Quality": frozenset({"Year", "Corpus view", "Hierarchy view"}),
}


def control_is_enabled(page: str, label: str) -> bool:
    """Return whether a global control has an effect on the selected page."""
    return label in CONTROL_APPLICABILITY.get(page, frozenset())


def dimension_options(
    frame: pd.DataFrame,
    column: str,
    *,
    year: int | None = None,
    corpus: str | None = None,
    hierarchy: str | None = None,
) -> list[str]:
    """Return sorted filter values from a complete filter-dimension table."""
    if frame.empty:
        return []
    selected = frame
    dimensions: tuple[tuple[str, object | None], ...] = (
        ("year", year),
        ("corpus_view", corpus),
        ("hierarchy_view", hierarchy),
    )
    for dimension, value in dimensions:
        if value is not None and dimension in selected.columns:
            selected = selected.loc[selected[dimension] == value]
    if {"dimension", "value"}.issubset(selected.columns):
        dimension_name = {
            "country_name": "country",
            "subregion": "subregion",
            "institution_category": "institution_type",
        }.get(column, column)
        selected = selected.loc[selected["dimension"] == dimension_name]
        return sorted(str(value) for value in selected["value"].dropna().unique())
    if column not in selected.columns:
        return []
    return sorted(str(value) for value in selected[column].dropna().unique())


def filtered_view(
    frame: pd.DataFrame,
    year: int,
    corpus: str,
    hierarchy: str,
) -> pd.DataFrame:
    """Select one complete annual corpus/hierarchy view."""
    if frame.empty:
        return frame
    mask = (
        (frame["year"] == year)
        & (frame["corpus_view"] == corpus)
        & (frame["hierarchy_view"] == hierarchy)
    )
    return frame.loc[mask].copy()


def filter_geographic_view(
    nodes: pd.DataFrame,
    edges: pd.DataFrame,
    *,
    country: str = "All",
    subregion: str = "All",
    institution_type: str = "All",
    region_pair: str = "All",
    topic_family: str = "All",
    exclude_warning_size_consortia: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Filter map edges through one final node-ID set and retain outside partners.

    Country, subregion, and institution-type predicates are combined on nodes before
    edges are selected. An edge remains visible when at least one endpoint belongs to
    that final node set. Its other endpoint is returned separately for secondary styling.
    """
    selected_nodes = nodes.copy()
    node_filters = (
        ("country_name", country),
        ("subregion", subregion),
        ("institution_category", institution_type),
    )
    for column, value in node_filters:
        if value != "All":
            selected_nodes = selected_nodes.loc[selected_nodes[column] == value]

    selected_edges = edges.copy()
    if region_pair != "All":
        selected_edges = selected_edges.loc[selected_edges["macro_region_pair"] == region_pair]
    if topic_family != "All":
        selected_edges = selected_edges.loc[
            selected_edges["topic_families"].apply(
                lambda values: _contains_value(values, topic_family)
            )
        ]
    if exclude_warning_size_consortia:
        selected_edges = selected_edges.loc[selected_edges["large_consortium_work_count"] == 0]

    selected_ids = set(str(value) for value in selected_nodes["institution_id"])
    selected_edges = selected_edges.loc[
        selected_edges["source_id"].isin(selected_ids)
        | selected_edges["target_id"].isin(selected_ids)
    ].copy()
    endpoint_ids = set(str(value) for value in selected_edges["source_id"])
    endpoint_ids.update(str(value) for value in selected_edges["target_id"])
    partner_ids = endpoint_ids.difference(selected_ids)
    partner_nodes = nodes.loc[nodes["institution_id"].isin(partner_ids)].copy()
    return selected_nodes, partner_nodes, selected_edges


def partner_share_view(
    flows: pd.DataFrame,
    *,
    weight_column: str,
    geographic_level: str | None = None,
) -> pd.DataFrame:
    """Expand undirected flows into row-normalized collaboration-endpoint shares.

    An internal edge contributes two endpoints to its geography; a cross-geography edge
    contributes one endpoint to each side. A row therefore answers: for a collaboration
    endpoint in this geography, what share of weighted partners are in each destination?
    """
    required = {"source_geography", "target_geography", weight_column}
    if flows.empty or not required.issubset(flows.columns):
        return pd.DataFrame(
            columns=[
                *flows.columns,
                "endpoint_weight",
                "total_endpoint_weight",
                "partner_share",
                "is_local",
            ]
        )
    selected = flows.copy()
    if geographic_level is not None and "geographic_level" in selected.columns:
        selected = selected.loc[selected["geographic_level"] == geographic_level].copy()
    if selected.empty:
        return partner_share_view(
            selected.drop(columns=[weight_column], errors="ignore"),
            weight_column=weight_column,
        )
    if (selected[weight_column] < 0).any():
        raise ValueError(f"{weight_column} cannot contain negative collaboration weights")

    cross_flows = selected.loc[selected["source_geography"] != selected["target_geography"]].copy()
    reverse = cross_flows.rename(
        columns={
            "source_geography": "target_geography",
            "target_geography": "source_geography",
        }
    )
    directed = pd.concat([selected, reverse], ignore_index=True)
    directed["is_local"] = directed["source_geography"] == directed["target_geography"]
    directed["endpoint_weight"] = directed[weight_column].astype(float)
    directed.loc[directed["is_local"], "endpoint_weight"] *= 2.0
    group_columns = [
        column
        for column in ("year", "corpus_view", "hierarchy_view", "geographic_level")
        if column in directed.columns
    ]
    source_groups = [*group_columns, "source_geography"]
    directed["total_endpoint_weight"] = directed.groupby(source_groups, dropna=False)[
        "endpoint_weight"
    ].transform("sum")
    directed["partner_share"] = directed["endpoint_weight"].div(
        directed["total_endpoint_weight"].where(directed["total_endpoint_weight"] > 0)
    )
    return directed.sort_values([*source_groups, "target_geography"], kind="stable").reset_index(
        drop=True
    )


def local_collaboration_profile(
    flows: pd.DataFrame,
    *,
    weight_column: str,
    geographic_level: str | None = None,
) -> pd.DataFrame:
    """Return within-geography endpoint shares while retaining zero-local geographies."""
    directed = partner_share_view(
        flows,
        weight_column=weight_column,
        geographic_level=geographic_level,
    )
    output_columns = [
        "geography",
        "local_collaboration_weight",
        "local_endpoint_weight",
        "external_endpoint_weight",
        "total_endpoint_weight",
        "local_collaboration_share",
    ]
    if directed.empty:
        return pd.DataFrame(columns=output_columns)
    group_columns = [
        column
        for column in ("year", "corpus_view", "hierarchy_view", "geographic_level")
        if column in directed.columns
    ]
    source_groups = [*group_columns, "source_geography"]
    totals = (
        directed.groupby(source_groups, as_index=False, dropna=False)["endpoint_weight"]
        .sum()
        .rename(columns={"endpoint_weight": "total_endpoint_weight"})
    )
    local = (
        directed.loc[directed["is_local"]]
        .groupby(source_groups, as_index=False, dropna=False)
        .agg(
            local_collaboration_weight=(weight_column, "sum"),
            local_endpoint_weight=("endpoint_weight", "sum"),
        )
    )
    profile = totals.merge(local, on=source_groups, how="left")
    profile[["local_collaboration_weight", "local_endpoint_weight"]] = profile[
        ["local_collaboration_weight", "local_endpoint_weight"]
    ].fillna(0.0)
    profile["external_endpoint_weight"] = (
        profile["total_endpoint_weight"] - profile["local_endpoint_weight"]
    )
    profile["local_collaboration_share"] = profile["local_endpoint_weight"].div(
        profile["total_endpoint_weight"].where(profile["total_endpoint_weight"] > 0)
    )
    profile = profile.rename(columns={"source_geography": "geography"})
    return profile.sort_values([*group_columns, "geography"], kind="stable").reset_index(drop=True)


def region_comparison_rows(
    frame: pd.DataFrame,
    *,
    weight_column: str,
    region_pair: str,
) -> pd.DataFrame:
    """Return comparable directional shares for one macro-region view."""
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


def _contains_value(values: object, expected: str) -> bool:
    if isinstance(values, str) or not isinstance(values, Iterable):
        return values == expected
    return expected in values
