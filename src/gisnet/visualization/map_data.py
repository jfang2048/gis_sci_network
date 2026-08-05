"""Coordinate-grounded, thresholded geographic map datasets."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "geographic-map-data-2026-08-05-v1"


def build_map_data(
    nodes_path: str | Path,
    edges_metrics_path: str | Path,
    *,
    map_nodes_path: str | Path,
    map_edges_path: str | Path,
    coverage_path: str | Path,
    edge_limit_per_view: int = 500,
    node_limit_per_view: int = 1000,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build map-ready tables without inventing or modifying coordinates."""
    nodes = Path(nodes_path)
    edges = Path(edges_metrics_path)
    for path in (nodes, edges):
        if not path.is_file():
            raise ValueError(f"map input does not exist: {path}")
    outputs = [Path(map_nodes_path), Path(map_edges_path), Path(coverage_path)]
    for path in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary = {path: path.with_suffix(".parquet.tmp") for path in outputs}
    for path in temporary.values():
        path.unlink(missing_ok=True)
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        connection.execute("SET preserve_insertion_order = false")
        connection.execute(
            """
            CREATE TEMP TABLE coordinate_nodes AS
            SELECT year, corpus_view, hierarchy_view, institution_id,
                   latitude, longitude, institution_category
            FROM read_parquet(?)
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
            """,
            [str(nodes)],
        )
        connection.execute(
            f"""
            COPY (
                WITH joined AS (
                    SELECT
                        e.*,
                        source.latitude AS source_latitude,
                        source.longitude AS source_longitude,
                        target.latitude AS target_latitude,
                        target.longitude AS target_longitude,
                        source.institution_category AS source_institution_type,
                        target.institution_category AS target_institution_type,
                        least(e.source_region, e.target_region) || ' — ' ||
                            greatest(e.source_region, e.target_region) AS macro_region_pair,
                        row_number() OVER (
                            PARTITION BY e.year, e.corpus_view, e.hierarchy_view
                            ORDER BY e.visualization_score DESC, e.source_id, e.target_id
                        )::INTEGER AS default_edge_rank
                    FROM read_parquet(?) e
                    INNER JOIN coordinate_nodes source
                        ON source.year = e.year
                       AND source.corpus_view = e.corpus_view
                       AND source.hierarchy_view = e.hierarchy_view
                       AND source.institution_id = e.source_id
                    INNER JOIN coordinate_nodes target
                        ON target.year = e.year
                       AND target.corpus_view = e.corpus_view
                       AND target.hierarchy_view = e.hierarchy_view
                       AND target.institution_id = e.target_id
                    WHERE source.latitude IS NOT NULL AND source.longitude IS NOT NULL
                      AND target.latitude IS NOT NULL AND target.longitude IS NOT NULL
                )
                SELECT *, ?::INTEGER AS default_edge_limit,
                    'visualization_score (non-primary ranking only)' AS default_threshold_method
                FROM joined WHERE default_edge_rank <= ?
                ORDER BY year, corpus_view, hierarchy_view, default_edge_rank
            ) TO '{_literal(temporary[outputs[1]])}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [
                str(edges),
                edge_limit_per_view,
                edge_limit_per_view,
            ],
        )
        connection.execute(
            f"""
            COPY (
                WITH ranked AS (
                    SELECT *, row_number() OVER (
                        PARTITION BY year, corpus_view, hierarchy_view
                        ORDER BY work_count DESC, institution_id
                    )::INTEGER AS default_node_rank
                    FROM read_parquet(?)
                    WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                ), endpoints AS (
                    SELECT year, corpus_view, hierarchy_view, source_id AS institution_id
                    FROM read_parquet(?)
                    UNION
                    SELECT year, corpus_view, hierarchy_view, target_id AS institution_id
                    FROM read_parquet(?)
                )
                SELECT ranked.*, ?::INTEGER AS default_node_limit,
                    'sourced coordinates only' AS coordinate_policy
                FROM ranked
                LEFT JOIN endpoints USING (year, corpus_view, hierarchy_view, institution_id)
                WHERE default_node_rank <= ? OR endpoints.institution_id IS NOT NULL
                ORDER BY year, corpus_view, hierarchy_view, default_node_rank, institution_id
            ) TO '{_literal(temporary[outputs[0]])}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [
                str(nodes),
                str(temporary[outputs[1]]),
                str(temporary[outputs[1]]),
                node_limit_per_view,
                node_limit_per_view,
            ],
        )
        connection.execute(
            f"""
            COPY (
                WITH node_coverage AS (
                    SELECT year, corpus_view, hierarchy_view,
                        count(*)::BIGINT AS total_node_count,
                        count(*) FILTER (
                            WHERE latitude IS NOT NULL AND longitude IS NOT NULL
                        )::BIGINT AS coordinate_node_count
                    FROM read_parquet(?) GROUP BY ALL
                ), edge_totals AS (
                    SELECT year, corpus_view, hierarchy_view,
                           count(*)::BIGINT AS total_edge_count
                    FROM read_parquet(?)
                    GROUP BY year, corpus_view, hierarchy_view
                ), coordinate_edges AS (
                    SELECT e.year, e.corpus_view, e.hierarchy_view,
                        count(*)::BIGINT AS coordinate_edge_count
                    FROM read_parquet(?) e
                    INNER JOIN coordinate_nodes source
                        ON source.year=e.year AND source.corpus_view=e.corpus_view
                       AND source.hierarchy_view=e.hierarchy_view
                       AND source.institution_id=e.source_id
                    INNER JOIN coordinate_nodes target
                        ON target.year=e.year AND target.corpus_view=e.corpus_view
                       AND target.hierarchy_view=e.hierarchy_view
                       AND target.institution_id=e.target_id
                    GROUP BY e.year, e.corpus_view, e.hierarchy_view
                ), edge_coverage AS (
                    SELECT edge_totals.*,
                           coalesce(coordinate_edges.coordinate_edge_count, 0)::BIGINT
                               AS coordinate_edge_count
                    FROM edge_totals
                    LEFT JOIN coordinate_edges USING (year, corpus_view, hierarchy_view)
                ), selected AS (
                    SELECT year, corpus_view, hierarchy_view,
                           count(*)::BIGINT AS selected_edge_count
                    FROM read_parquet(?) GROUP BY ALL
                )
                SELECT
                    node_coverage.*,
                    edge_coverage.total_edge_count,
                    edge_coverage.coordinate_edge_count,
                    coalesce(selected.selected_edge_count, 0)::BIGINT AS selected_edge_count,
                    node_coverage.total_node_count - node_coverage.coordinate_node_count
                        AS missing_coordinate_node_count,
                    edge_coverage.total_edge_count - edge_coverage.coordinate_edge_count
                        AS missing_coordinate_edge_count,
                    node_coverage.coordinate_node_count / node_coverage.total_node_count::DOUBLE
                        AS node_coordinate_coverage_share,
                    ?::INTEGER AS default_edge_limit,
                    ?::INTEGER AS default_node_limit
                FROM node_coverage
                INNER JOIN edge_coverage USING (year, corpus_view, hierarchy_view)
                LEFT JOIN selected USING (year, corpus_view, hierarchy_view)
                ORDER BY year, corpus_view, hierarchy_view
            ) TO '{_literal(temporary[outputs[2]])}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [
                str(nodes),
                str(edges),
                str(edges),
                str(temporary[outputs[1]]),
                edge_limit_per_view,
                node_limit_per_view,
            ],
        )
    except BaseException:
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    definitions = {
        outputs[0]: (
            ["year", "corpus_view", "hierarchy_view", "institution_id"],
            {"year", "institution_id", "latitude", "longitude", "coordinate_policy"},
        ),
        outputs[1]: (
            ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {"year", "source_latitude", "target_latitude", "default_edge_rank"},
        ),
        outputs[2]: (
            ["year", "corpus_view", "hierarchy_view"],
            {"year", "total_node_count", "coordinate_node_count", "selected_edge_count"},
        ),
    }
    metrics: dict[Path, dict[str, Any]] = {}
    for destination, path in temporary.items():
        primary_key, required = definitions[destination]
        metrics[destination] = parquet_metrics(
            path, primary_key=primary_key, required_columns=required, year_column="year"
        )
    validation = duckdb.connect()
    try:
        checks = validation.execute(
            """
            SELECT
                count(*) FILTER (
                    WHERE source_latitude IS NULL OR source_longitude IS NULL
                       OR target_latitude IS NULL OR target_longitude IS NULL
                ),
                coalesce(max(default_edge_rank), 0),
                count(*)
            FROM read_parquet(?)
            """,
            [str(temporary[outputs[1]])],
        ).fetchone()
        coverage = validation.execute(
            """
            SELECT sum(missing_coordinate_node_count), sum(missing_coordinate_edge_count),
                   min(node_coordinate_coverage_share), max(node_coordinate_coverage_share)
            FROM read_parquet(?)
            """,
            [str(temporary[outputs[2]])],
        ).fetchone()
    finally:
        validation.close()
    if checks is None or coverage is None or int(checks[0]) or int(checks[1]) > edge_limit_per_view:
        raise ValueError("map coordinate or default-threshold invariant failed")
    for destination, path in temporary.items():
        os.replace(path, destination)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "nodes_sha256": file_sha256(nodes),
                "edges_sha256": file_sha256(edges),
                "edge_limit_per_view": edge_limit_per_view,
                "node_limit_per_view": node_limit_per_view,
            }
        ),
        "map_node_row_count": int(metrics[outputs[0]]["row_count"]),
        "map_edge_row_count": int(metrics[outputs[1]]["row_count"]),
        "coverage_row_count": int(metrics[outputs[2]]["row_count"]),
        "missing_coordinate_node_observations": int(coverage[0]),
        "missing_coordinate_edge_observations": int(coverage[1]),
        "minimum_node_coordinate_coverage_share": float(coverage[2]),
        "maximum_node_coordinate_coverage_share": float(coverage[3]),
        "default_edge_limit_per_view": edge_limit_per_view,
        "default_node_limit_per_view": node_limit_per_view,
        "coordinates_invented": False,
        "filters_mutate_source_data": False,
        "outputs": {
            "map_nodes_year": str(outputs[0]),
            "map_edges_year": str(outputs[1]),
            "map_coverage_year": str(outputs[2]),
        },
        "generated_at_utc": _timestamp(),
    }


def write_map_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [
        ".agent/manifests/nodes_year.json",
        ".agent/manifests/edges_metrics_year.json",
    ]
    source_versions = {"map_data_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="geographic_map_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions=source_versions,
        source_manifests=source_manifests,
        command=command,
    )
    definitions = (
        (
            "map_nodes_year",
            ["year", "corpus_view", "hierarchy_view", "institution_id"],
            {"year", "institution_id", "latitude", "longitude"},
        ),
        (
            "map_edges_year",
            ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {"year", "source_latitude", "target_latitude", "default_edge_rank"},
        ),
        (
            "map_coverage_year",
            ["year", "corpus_view", "hierarchy_view"],
            {"year", "total_node_count", "coordinate_node_count", "selected_edge_count"},
        ),
    )
    for dataset_name, primary_key, required in definitions:
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


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
