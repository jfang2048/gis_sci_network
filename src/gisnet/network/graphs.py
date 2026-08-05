"""Annual weighted undirected graph catalogues backed by processed Parquet tables."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "annual-graph-catalogue-2026-08-05-v1"


def build_annual_graph_catalogue(
    edges_path: str | Path,
    institution_outputs_path: str | Path,
    *,
    summary_path: str | Path,
    minimum_fractional_weight: float,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build one catalogue row per annual corpus/hierarchy graph."""
    edges = Path(edges_path)
    nodes = Path(institution_outputs_path)
    for path in (edges, nodes):
        if not path.is_file():
            raise ValueError(f"annual graph input does not exist: {path}")
    output = Path(summary_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".parquet.tmp")
    temporary.unlink(missing_ok=True)
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        connection.execute("SET preserve_insertion_order = false")
        connection.execute(
            f"""
            COPY (
                WITH node_counts AS (
                    SELECT
                        year,
                        corpus_view,
                        hierarchy_view,
                        count(*)::BIGINT AS node_count,
                        count(*) FILTER (WHERE analytical_scope = 'primary')::BIGINT
                            AS primary_node_count,
                        sum(work_count)::BIGINT AS summed_node_work_count
                    FROM read_parquet(?)
                    GROUP BY ALL
                ), edge_counts AS (
                    SELECT
                        year,
                        corpus_view,
                        hierarchy_view,
                        count(*)::BIGINT AS edge_count,
                        count(*) FILTER (WHERE fractional_count >= ?)::BIGINT
                            AS configured_filtered_edge_count,
                        sum(full_count)::HUGEINT AS total_full_weight,
                        sum(fractional_count) AS total_fractional_weight
                    FROM read_parquet(?)
                    GROUP BY ALL
                ), active_nodes AS (
                    SELECT
                        year,
                        corpus_view,
                        hierarchy_view,
                        count(*)::BIGINT AS active_node_count
                    FROM (
                        SELECT year, corpus_view, hierarchy_view, source_id AS institution_id
                        FROM read_parquet(?)
                        UNION
                        SELECT year, corpus_view, hierarchy_view, target_id AS institution_id
                        FROM read_parquet(?)
                    )
                    GROUP BY year, corpus_view, hierarchy_view
                )
                SELECT
                    n.year,
                    n.corpus_view,
                    n.hierarchy_view,
                    'undirected' AS graph_type,
                    'fractional_count' AS default_weight_column,
                    n.node_count,
                    coalesce(a.active_node_count, 0)::BIGINT AS active_node_count,
                    (n.node_count - coalesce(a.active_node_count, 0))::BIGINT
                        AS isolated_output_node_count,
                    n.primary_node_count,
                    coalesce(e.edge_count, 0)::BIGINT AS edge_count,
                    coalesce(e.configured_filtered_edge_count, 0)::BIGINT
                        AS configured_filtered_edge_count,
                    coalesce(e.total_full_weight, 0)::HUGEINT AS total_full_weight,
                    coalesce(e.total_fractional_weight, 0.0) AS total_fractional_weight,
                    n.summed_node_work_count,
                    ?::DOUBLE AS configured_minimum_fractional_weight,
                    'data/processed/institution_outputs_year.parquet' AS node_source,
                    'data/processed/edges_year.parquet' AS edge_source,
                    'year,corpus_view,hierarchy_view,institution_id' AS node_key,
                    'year,corpus_view,hierarchy_view,source_id,target_id' AS edge_key
                FROM node_counts n
                LEFT JOIN edge_counts e USING (year, corpus_view, hierarchy_view)
                LEFT JOIN active_nodes a USING (year, corpus_view, hierarchy_view)
                ORDER BY n.year, n.corpus_view, n.hierarchy_view
            ) TO '{_literal(temporary)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [
                str(nodes),
                minimum_fractional_weight,
                str(edges),
                str(edges),
                str(edges),
                minimum_fractional_weight,
            ],
        )
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    metrics = parquet_metrics(
        temporary,
        primary_key=["year", "corpus_view", "hierarchy_view"],
        required_columns={
            "year",
            "node_count",
            "edge_count",
            "isolated_output_node_count",
            "graph_type",
        },
        year_column="year",
    )
    validation = duckdb.connect()
    try:
        invariants = validation.execute(
            """
            SELECT
                count(*) FILTER (WHERE graph_type != 'undirected'),
                count(*) FILTER (
                    WHERE active_node_count + isolated_output_node_count != node_count
                ),
                count(*) FILTER (WHERE edge_count < configured_filtered_edge_count),
                min(isolated_output_node_count),
                sum(isolated_output_node_count),
                sum(node_count),
                sum(edge_count)
            FROM read_parquet(?)
            """,
            [str(temporary)],
        ).fetchone()
    finally:
        validation.close()
    if invariants is None or any(int(invariants[index]) for index in range(3)):
        raise ValueError("annual graph count or filter invariants failed")
    os.replace(temporary, output)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "edges_sha256": file_sha256(edges),
                "institution_outputs_sha256": file_sha256(nodes),
                "minimum_fractional_weight": minimum_fractional_weight,
            }
        ),
        "graph_count": int(metrics["row_count"]),
        "node_observation_count": int(invariants[5]),
        "edge_observation_count": int(invariants[6]),
        "isolated_output_node_count": int(invariants[4]),
        "minimum_isolated_output_nodes_per_graph": int(invariants[3]),
        "graph_representation": {
            "type": "weighted undirected",
            "node_source": "data/processed/institution_outputs_year.parquet",
            "edge_source": "data/processed/edges_year.parquet",
            "default_weight": "fractional_count",
            "available_weights": ["full_count", "fractional_count"],
            "filters": {
                "minimum_weight": "fractional_count >= configured threshold",
                "primary_institutions": "analytical_scope = 'primary'",
            },
            "stored_data_mutated_by_filters": False,
        },
        "outputs": {"graph_summary_year": str(output)},
        "generated_at_utc": _timestamp(),
    }


def write_graph_artifacts(
    summary: dict[str, Any],
    *,
    catalogue_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    """Write the lightweight graph catalogue and dataset manifests."""
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [
        ".agent/manifests/edges_year.json",
        ".agent/manifests/institution_outputs_year.json",
    ]
    source_versions = {"annual_graph_policy": _STAGE_VERSION}
    write_json_artifact(
        path=catalogue_path,
        dataset_name="annual_graph_catalogue",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions=source_versions,
        source_manifests=source_manifests,
        command=command,
    )
    write_parquet_manifest(
        path=summary["outputs"]["graph_summary_year"],
        dataset_name="graph_summary_year",
        primary_key=["year", "corpus_view", "hierarchy_view"],
        required_columns={"year", "node_count", "edge_count", "isolated_output_node_count"},
        year_column="year",
        run_id=run_id,
        config_hashes=config_hashes,
        source_manifests=source_manifests,
        source_versions=source_versions,
        command=command,
    )


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
