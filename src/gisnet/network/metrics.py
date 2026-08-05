"""Deterministic node centrality and annual graph metrics."""

from __future__ import annotations

import os
import random
from datetime import UTC, datetime
from math import isfinite
from pathlib import Path
from typing import Any

import duckdb
import igraph as ig  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "network-metrics-2026-08-05-v1"
_APPROXIMATION_CUTOFF = 3


def build_network_metrics(
    edges_path: str | Path,
    institution_outputs_path: str | Path,
    *,
    nodes_metrics_path: str | Path,
    graph_metrics_path: str | Path,
    approximate_betweenness_threshold: int,
    random_seed: int,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Compute required weighted node and graph measures for every annual view."""
    edges_source = Path(edges_path)
    nodes_source = Path(institution_outputs_path)
    for path in (edges_source, nodes_source):
        if not path.is_file():
            raise ValueError(f"network metric input does not exist: {path}")
    node_output = Path(nodes_metrics_path)
    graph_output = Path(graph_metrics_path)
    for path in (node_output, graph_output):
        path.parent.mkdir(parents=True, exist_ok=True)
    node_temporary = node_output.with_suffix(".parquet.tmp")
    graph_temporary = graph_output.with_suffix(".parquet.tmp")
    node_temporary.unlink(missing_ok=True)
    graph_temporary.unlink(missing_ok=True)

    connection = duckdb.connect()
    connection.execute("SET memory_limit = ?", [memory_limit])
    connection.execute("SET threads = ?", [threads])
    partitions = connection.execute(
        """
        SELECT DISTINCT year, corpus_view, hierarchy_view
        FROM read_parquet(?)
        ORDER BY year, corpus_view, hierarchy_view
        """,
        [str(nodes_source)],
    ).fetchall()
    node_writer: pq.ParquetWriter | None = None
    graph_rows: list[dict[str, Any]] = []
    previous_edges: dict[tuple[str, str], set[tuple[str, str]]] = {}
    try:
        for year_value, corpus_value, hierarchy_value in partitions:
            year = int(year_value)
            corpus = str(corpus_value)
            hierarchy = str(hierarchy_value)
            node_table = connection.execute(
                """
                SELECT * FROM read_parquet(?)
                WHERE year = ? AND corpus_view = ? AND hierarchy_view = ?
                ORDER BY institution_id
                """,
                [str(nodes_source), year, corpus, hierarchy],
            ).to_arrow_table()
            edge_table = connection.execute(
                """
                SELECT source_id, target_id, full_count, fractional_count,
                       source_region, target_region, source_country, target_country
                FROM read_parquet(?)
                WHERE year = ? AND corpus_view = ? AND hierarchy_view = ?
                ORDER BY source_id, target_id
                """,
                [str(edges_source), year, corpus, hierarchy],
            ).to_arrow_table()
            node_rows = node_table.to_pylist()
            edge_rows = edge_table.to_pylist()
            institution_ids = [str(row["institution_id"]) for row in node_rows]
            index = {
                institution_id: position for position, institution_id in enumerate(institution_ids)
            }
            graph_edges: list[tuple[int, int]] = []
            full_weights: list[float] = []
            fractional_weights: list[float] = []
            partner_countries: list[set[str]] = [set() for _ in institution_ids]
            partner_regions: list[set[str]] = [set() for _ in institution_ids]
            cross_region_strength = [0.0 for _ in institution_ids]
            current_edge_keys: set[tuple[str, str]] = set()
            cross_region_edges = 0
            cross_region_weight = 0.0
            for edge in edge_rows:
                source_id = str(edge["source_id"])
                target_id = str(edge["target_id"])
                try:
                    source_index = index[source_id]
                    target_index = index[target_id]
                except KeyError as exc:
                    raise ValueError(f"edge endpoint missing from annual nodes: {exc}") from exc
                graph_edges.append((source_index, target_index))
                full_weight = float(edge["full_count"])
                fractional_weight = float(edge["fractional_count"])
                full_weights.append(full_weight)
                fractional_weights.append(fractional_weight)
                current_edge_keys.add((source_id, target_id))
                source_country = str(edge["source_country"])
                target_country = str(edge["target_country"])
                source_region = str(edge["source_region"])
                target_region = str(edge["target_region"])
                partner_countries[source_index].add(target_country)
                partner_countries[target_index].add(source_country)
                partner_regions[source_index].add(target_region)
                partner_regions[target_index].add(source_region)
                if source_region != target_region:
                    cross_region_edges += 1
                    cross_region_weight += fractional_weight
                    cross_region_strength[source_index] += fractional_weight
                    cross_region_strength[target_index] += fractional_weight
            graph = ig.Graph(n=len(institution_ids), edges=graph_edges, directed=False)
            degree = [int(value) for value in graph.degree()]
            full_strength = [float(value) for value in graph.strength(weights=full_weights)]
            fractional_strength = [
                float(value) for value in graph.strength(weights=fractional_weights)
            ]
            pagerank = [
                float(value) for value in graph.pagerank(weights=fractional_weights, directed=False)
            ]
            distances = [1.0 / value for value in fractional_weights]
            is_approximate = len(institution_ids) > approximate_betweenness_threshold
            cutoff = _APPROXIMATION_CUTOFF if is_approximate else None
            cutoff_metadata = cutoff if cutoff is not None else -1
            raw_betweenness = graph.betweenness(directed=False, weights=distances, cutoff=cutoff)
            denominator = (len(institution_ids) - 1) * (len(institution_ids) - 2) / 2.0
            betweenness = [
                float(value) / denominator if denominator > 0 else 0.0 for value in raw_betweenness
            ]
            method = "igraph_weighted_cutoff_3" if is_approximate else "igraph_weighted_exact"
            derived_seed = (
                random_seed
                + year * 10
                + (0 if corpus == "strict" else 2)
                + (0 if hierarchy == "organization" else 1)
            )
            ig.set_random_number_generator(random.Random(derived_seed))
            clustering = graph.community_leiden(
                objective_function="modularity",
                weights=fractional_weights,
                resolution=1.0,
                n_iterations=-1,
            )
            components = graph.connected_components()
            component_sizes = components.sizes()
            region_codes = _category_codes([str(row["macro_region"]) for row in node_rows])
            country_codes = _category_codes([str(row["country_code"]) for row in node_rows])
            region_assortativity = _finite_or_none(
                graph.assortativity_nominal(region_codes, directed=False)
            )
            country_assortativity = _finite_or_none(
                graph.assortativity_nominal(country_codes, directed=False)
            )
            previous = previous_edges.get((corpus, hierarchy), set())
            new_edge_count = len(current_edge_keys - previous)
            continuing_edge_count = len(current_edge_keys & previous)
            disappearing_edge_count = len(previous - current_edge_keys)
            previous_edges[(corpus, hierarchy)] = current_edge_keys
            total_fractional_weight = sum(fractional_weights)
            for position, row in enumerate(node_rows):
                strength = fractional_strength[position]
                row.update(
                    {
                        "degree": degree[position],
                        "full_strength": full_strength[position],
                        "fractional_strength": strength,
                        "betweenness": betweenness[position],
                        "betweenness_method": method,
                        "betweenness_sample_size": len(institution_ids),
                        "betweenness_cutoff": cutoff_metadata,
                        "betweenness_seed": derived_seed,
                        "pagerank": pagerank[position],
                        "bridge_score": (
                            cross_region_strength[position] / strength if strength > 0 else 0.0
                        ),
                        "partner_country_count": len(partner_countries[position]),
                        "partner_region_count": len(partner_regions[position]),
                    }
                )
            output_table = pa.Table.from_pylist(node_rows)
            if node_writer is None:
                node_writer = pq.ParquetWriter(
                    node_temporary, output_table.schema, compression="zstd"
                )
            node_writer.write_table(output_table, row_group_size=100_000)
            graph_rows.append(
                {
                    "year": year,
                    "corpus_view": corpus,
                    "hierarchy_view": hierarchy,
                    "node_count": len(institution_ids),
                    "edge_count": len(graph_edges),
                    "density": float(graph.density(loops=False)),
                    "mean_degree": sum(degree) / len(degree) if degree else 0.0,
                    "mean_full_strength": (
                        sum(full_strength) / len(full_strength) if full_strength else 0.0
                    ),
                    "mean_fractional_strength": (
                        sum(fractional_strength) / len(fractional_strength)
                        if fractional_strength
                        else 0.0
                    ),
                    "connected_component_count": len(components),
                    "largest_connected_component_share": (
                        max(component_sizes) / len(institution_ids) if institution_ids else 0.0
                    ),
                    "modularity": float(clustering.modularity),
                    "modularity_resolution": 1.0,
                    "macro_region_assortativity": region_assortativity,
                    "country_assortativity": country_assortativity,
                    "cross_region_edge_share": (
                        cross_region_edges / len(graph_edges) if graph_edges else 0.0
                    ),
                    "cross_region_fractional_weight_share": (
                        cross_region_weight / total_fractional_weight
                        if total_fractional_weight > 0
                        else 0.0
                    ),
                    "new_edge_count": new_edge_count,
                    "continuing_edge_count": continuing_edge_count,
                    "disappearing_edge_count": disappearing_edge_count,
                    "betweenness_method": method,
                    "betweenness_sample_size": len(institution_ids),
                    "betweenness_cutoff": cutoff_metadata,
                    "random_seed": derived_seed,
                }
            )
        if node_writer is None:
            raise ValueError("network metric input contains no annual nodes")
        node_writer.close()
        node_writer = None
        pq.write_table(pa.Table.from_pylist(graph_rows), graph_temporary, compression="zstd")
    except BaseException:
        if node_writer is not None:
            node_writer.close()
        node_temporary.unlink(missing_ok=True)
        graph_temporary.unlink(missing_ok=True)
        raise
    finally:
        connection.close()

    node_metrics = parquet_metrics(
        node_temporary,
        primary_key=["year", "corpus_view", "hierarchy_view", "institution_id"],
        required_columns={
            "year",
            "institution_id",
            "degree",
            "full_strength",
            "fractional_strength",
            "betweenness",
            "pagerank",
            "bridge_score",
        },
        year_column="year",
    )
    graph_metrics = parquet_metrics(
        graph_temporary,
        primary_key=["year", "corpus_view", "hierarchy_view"],
        required_columns={
            "year",
            "node_count",
            "edge_count",
            "density",
            "connected_component_count",
            "modularity",
        },
        year_column="year",
    )
    validation = duckdb.connect()
    try:
        node_invariants = validation.execute(
            """
            SELECT
                count(*) FILTER (WHERE degree < 0 OR full_strength < 0 OR fractional_strength < 0),
                count(*) FILTER (WHERE betweenness < 0 OR betweenness > 1),
                count(*) FILTER (WHERE pagerank < 0 OR pagerank > 1),
                count(*) FILTER (WHERE bridge_score < 0 OR bridge_score > 1),
                max(abs(pagerank_sum - 1.0))
            FROM (
                SELECT *, sum(pagerank) OVER (
                    PARTITION BY year, corpus_view, hierarchy_view
                ) AS pagerank_sum
                FROM read_parquet(?)
            )
            """,
            [str(node_temporary)],
        ).fetchone()
        graph_invariants = validation.execute(
            """
            SELECT
                count(*) FILTER (WHERE density < 0 OR density > 1),
                count(*) FILTER (
                    WHERE largest_connected_component_share < 0
                       OR largest_connected_component_share > 1
                ),
                count(*) FILTER (WHERE modularity < -1 OR modularity > 1),
                count(*) FILTER (WHERE new_edge_count + continuing_edge_count != edge_count),
                count(*) FILTER (WHERE connected_component_count < 1),
                count(*) FILTER (WHERE betweenness_method LIKE '%cutoff%')
            FROM read_parquet(?)
            """,
            [str(graph_temporary)],
        ).fetchone()
    finally:
        validation.close()
    if node_invariants is None or graph_invariants is None:
        raise ValueError("network metric validation query failed")
    if any(int(node_invariants[index]) for index in range(4)) or float(node_invariants[4]) > 1e-10:
        raise ValueError("node metric ranges or PageRank reconciliation failed")
    if any(int(graph_invariants[index]) for index in range(5)):
        raise ValueError("annual graph metric range or edge-status reconciliation failed")
    os.replace(node_temporary, node_output)
    os.replace(graph_temporary, graph_output)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "edges_sha256": file_sha256(edges_source),
                "institution_outputs_sha256": file_sha256(nodes_source),
                "approximate_betweenness_threshold": approximate_betweenness_threshold,
                "random_seed": random_seed,
            }
        ),
        "node_metric_row_count": int(node_metrics["row_count"]),
        "graph_metric_row_count": int(graph_metrics["row_count"]),
        "approximate_betweenness_graph_count": int(graph_invariants[5]),
        "maximum_pagerank_sum_error": float(node_invariants[4]),
        "large_graph_betweenness_method": "igraph weighted shortest paths with cutoff=3",
        "large_graph_betweenness_sample_size": "all vertices; path length limited to 3",
        "random_seed": random_seed,
        "outputs": {
            "nodes_year": str(node_output),
            "graph_metrics_year": str(graph_output),
        },
        "generated_at_utc": _timestamp(),
    }


def write_metric_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    """Write metric summary and Parquet manifests."""
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [
        ".agent/manifests/edges_year.json",
        ".agent/manifests/institution_outputs_year.json",
        ".agent/manifests/graph_summary_year.json",
    ]
    source_versions = {"network_metrics_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="network_metrics_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions=source_versions,
        source_manifests=source_manifests,
        command=command,
    )
    for dataset_name, primary_key, required in (
        (
            "nodes_year",
            ["year", "corpus_view", "hierarchy_view", "institution_id"],
            {"year", "institution_id", "degree", "betweenness", "pagerank"},
        ),
        (
            "graph_metrics_year",
            ["year", "corpus_view", "hierarchy_view"],
            {"year", "node_count", "edge_count", "density", "modularity"},
        ),
    ):
        write_parquet_manifest(
            path=summary["outputs"][dataset_name],
            dataset_name=dataset_name,
            primary_key=primary_key,
            required_columns=required,
            year_column="year",
            run_id=run_id,
            config_hashes=config_hashes,
            source_manifests=source_manifests,
            source_versions=source_versions,
            command=command,
        )


def _category_codes(values: list[str]) -> list[int]:
    mapping = {value: index for index, value in enumerate(sorted(set(values)))}
    return [mapping[value] for value in values]


def _finite_or_none(value: float) -> float | None:
    return float(value) if isfinite(value) else None


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
