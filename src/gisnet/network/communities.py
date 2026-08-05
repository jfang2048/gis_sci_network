"""Deterministic annual weighted Leiden community detection."""

from __future__ import annotations

import os
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import igraph as ig  # type: ignore[import-untyped]
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "annual-leiden-communities-2026-08-05-v1"
_RESOLUTIONS = (0.5, 1.0, 1.5)
_PRIMARY_RESOLUTION = 1.0


def build_annual_communities(
    edges_path: str | Path,
    nodes_path: str | Path,
    *,
    communities_path: str | Path,
    sensitivity_path: str | Path,
    random_seed: int,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Detect annual communities at the primary and sensitivity resolutions."""
    edges_source = Path(edges_path)
    nodes_source = Path(nodes_path)
    for path in (edges_source, nodes_source):
        if not path.is_file():
            raise ValueError(f"community input does not exist: {path}")
    community_output = Path(communities_path)
    sensitivity_output = Path(sensitivity_path)
    for path in (community_output, sensitivity_output):
        path.parent.mkdir(parents=True, exist_ok=True)
    community_temporary = community_output.with_suffix(".parquet.tmp")
    sensitivity_temporary = sensitivity_output.with_suffix(".parquet.tmp")
    community_temporary.unlink(missing_ok=True)
    sensitivity_temporary.unlink(missing_ok=True)

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
    writer: pq.ParquetWriter | None = None
    sensitivity_rows: list[dict[str, Any]] = []
    try:
        for year_value, corpus_value, hierarchy_value in partitions:
            year = int(year_value)
            corpus = str(corpus_value)
            hierarchy = str(hierarchy_value)
            node_rows = (
                connection.execute(
                    """
                SELECT institution_id, display_name, degree
                FROM read_parquet(?)
                WHERE year = ? AND corpus_view = ? AND hierarchy_view = ?
                ORDER BY institution_id
                """,
                    [str(nodes_source), year, corpus, hierarchy],
                )
                .to_arrow_table()
                .to_pylist()
            )
            edge_rows = (
                connection.execute(
                    """
                SELECT source_id, target_id, fractional_count
                FROM read_parquet(?)
                WHERE year = ? AND corpus_view = ? AND hierarchy_view = ?
                ORDER BY source_id, target_id
                """,
                    [str(edges_source), year, corpus, hierarchy],
                )
                .to_arrow_table()
                .to_pylist()
            )
            ids = [str(row["institution_id"]) for row in node_rows]
            index = {institution_id: position for position, institution_id in enumerate(ids)}
            graph = ig.Graph(
                n=len(ids),
                edges=[
                    (index[str(row["source_id"])], index[str(row["target_id"])])
                    for row in edge_rows
                ],
                directed=False,
            )
            weights = [float(row["fractional_count"]) for row in edge_rows]
            graph.es["weight"] = weights
            active = [position for position, row in enumerate(node_rows) if int(row["degree"]) > 0]
            too_small = len(active) < 3 or len(edge_rows) < 2
            memberships: dict[float, list[int] | None] = {}
            community_sizes: dict[int, int] = {}
            for resolution in _RESOLUTIONS:
                derived_seed = _derived_seed(random_seed, year, corpus, hierarchy, resolution)
                if too_small:
                    memberships[resolution] = None
                    sensitivity_rows.append(
                        {
                            "year": year,
                            "corpus_view": corpus,
                            "hierarchy_view": hierarchy,
                            "resolution": resolution,
                            "community_count": None,
                            "modularity": None,
                            "active_node_count": len(active),
                            "status": "too_small",
                            "random_seed": derived_seed,
                            "algorithm": "Leiden weighted modularity",
                        }
                    )
                    continue
                active_graph = graph.induced_subgraph(active)
                active_weights = [float(value) for value in active_graph.es["weight"]]
                ig.set_random_number_generator(random.Random(derived_seed))
                clustering = active_graph.community_leiden(
                    objective_function="modularity",
                    weights=active_weights,
                    resolution=resolution,
                    n_iterations=-1,
                )
                stable_membership = _stable_membership(
                    clustering.membership, [ids[position] for position in active]
                )
                memberships[resolution] = stable_membership
                if resolution == _PRIMARY_RESOLUTION:
                    community_sizes = {
                        community: stable_membership.count(community)
                        for community in set(stable_membership)
                    }
                sensitivity_rows.append(
                    {
                        "year": year,
                        "corpus_view": corpus,
                        "hierarchy_view": hierarchy,
                        "resolution": resolution,
                        "community_count": len(set(stable_membership)),
                        "modularity": float(clustering.modularity),
                        "active_node_count": len(active),
                        "status": "complete",
                        "random_seed": derived_seed,
                        "algorithm": "Leiden weighted modularity",
                    }
                )
            primary = memberships[_PRIMARY_RESOLUTION]
            active_membership = (
                {
                    node_position: primary[index_position]
                    for index_position, node_position in enumerate(active)
                }
                if primary is not None
                else {}
            )
            output_rows = []
            for position, row in enumerate(node_rows):
                community_number = active_membership.get(position)
                output_rows.append(
                    {
                        "year": year,
                        "corpus_view": corpus,
                        "hierarchy_view": hierarchy,
                        "institution_id": row["institution_id"],
                        "display_name": row["display_name"],
                        "degree": int(row["degree"]),
                        "community_id": (
                            f"C{community_number + 1:04d}" if community_number is not None else None
                        ),
                        "community_size": (
                            community_sizes[community_number]
                            if community_number is not None
                            else None
                        ),
                        "resolution": _PRIMARY_RESOLUTION,
                        "algorithm": "Leiden weighted modularity",
                        "status": (
                            "too_small"
                            if too_small
                            else "isolated"
                            if int(row["degree"]) == 0
                            else "complete"
                        ),
                        "random_seed": _derived_seed(
                            random_seed, year, corpus, hierarchy, _PRIMARY_RESOLUTION
                        ),
                    }
                )
            table = pa.Table.from_pylist(output_rows)
            if writer is None:
                writer = pq.ParquetWriter(community_temporary, table.schema, compression="zstd")
            writer.write_table(table, row_group_size=100_000)
        if writer is None:
            raise ValueError("community input contains no annual graph partitions")
        writer.close()
        writer = None
        pq.write_table(
            pa.Table.from_pylist(sensitivity_rows), sensitivity_temporary, compression="zstd"
        )
    except BaseException:
        if writer is not None:
            writer.close()
        community_temporary.unlink(missing_ok=True)
        sensitivity_temporary.unlink(missing_ok=True)
        raise
    finally:
        connection.close()

    community_metrics = parquet_metrics(
        community_temporary,
        primary_key=["year", "corpus_view", "hierarchy_view", "institution_id"],
        required_columns={"year", "institution_id", "community_id", "resolution", "status"},
        year_column="year",
    )
    sensitivity_metrics = parquet_metrics(
        sensitivity_temporary,
        primary_key=["year", "corpus_view", "hierarchy_view", "resolution"],
        required_columns={"year", "resolution", "community_count", "status", "algorithm"},
        year_column="year",
    )
    validation = duckdb.connect()
    try:
        invariants = validation.execute(
            """
            SELECT
                count(*) FILTER (
                    WHERE degree > 0 AND community_id IS NULL AND status != 'too_small'
                ),
                count(*) FILTER (WHERE degree = 0 AND community_id IS NOT NULL),
                count(*) FILTER (WHERE resolution != 1.0),
                count(*) FILTER (WHERE status = 'too_small'),
                count(DISTINCT community_id) FILTER (WHERE community_id IS NOT NULL)
            FROM read_parquet(?)
            """,
            [str(community_temporary)],
        ).fetchone()
        sensitivity = validation.execute(
            """
            SELECT count(DISTINCT resolution), count(*) FILTER (
                WHERE status = 'complete' AND (modularity < -1 OR modularity > 1)
            )
            FROM read_parquet(?)
            """,
            [str(sensitivity_temporary)],
        ).fetchone()
    finally:
        validation.close()
    if invariants is None or sensitivity is None:
        raise ValueError("community validation query failed")
    if (
        any(int(invariants[index]) for index in range(3))
        or int(sensitivity[0]) < 2
        or int(sensitivity[1])
    ):
        raise ValueError(
            "community coverage, isolate, primary-resolution, or sensitivity invariant failed"
        )
    os.replace(community_temporary, community_output)
    os.replace(sensitivity_temporary, sensitivity_output)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "edges_sha256": file_sha256(edges_source),
                "nodes_sha256": file_sha256(nodes_source),
                "resolutions": list(_RESOLUTIONS),
                "random_seed": random_seed,
            }
        ),
        "community_node_row_count": int(community_metrics["row_count"]),
        "sensitivity_row_count": int(sensitivity_metrics["row_count"]),
        "distinct_annual_community_labels": int(invariants[4]),
        "too_small_node_row_count": int(invariants[3]),
        "resolutions": list(_RESOLUTIONS),
        "primary_resolution": _PRIMARY_RESOLUTION,
        "random_seed": random_seed,
        "outputs": {
            "communities_year": str(community_output),
            "community_sensitivity_year": str(sensitivity_output),
        },
        "generated_at_utc": _timestamp(),
    }


def write_community_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    """Write community summary and Parquet manifests."""
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [
        ".agent/manifests/edges_year.json",
        ".agent/manifests/nodes_year.json",
    ]
    source_versions = {"community_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="community_detection_summary",
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
            "communities_year",
            ["year", "corpus_view", "hierarchy_view", "institution_id"],
            {"year", "institution_id", "community_id", "resolution", "status"},
        ),
        (
            "community_sensitivity_year",
            ["year", "corpus_view", "hierarchy_view", "resolution"],
            {"year", "resolution", "community_count", "modularity", "status"},
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


def _stable_membership(membership: list[int], institution_ids: list[str]) -> list[int]:
    members: dict[int, list[str]] = {}
    for community, institution_id in zip(membership, institution_ids, strict=True):
        members.setdefault(int(community), []).append(institution_id)
    ordered = sorted(members, key=lambda community: min(members[community]))
    remap = {community: stable for stable, community in enumerate(ordered)}
    return [remap[int(community)] for community in membership]


def _derived_seed(base: int, year: int, corpus: str, hierarchy: str, resolution: float) -> int:
    return (
        base
        + year * 100
        + int(resolution * 10)
        + (0 if corpus == "strict" else 2)
        + (0 if hierarchy == "organization" else 1)
    )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
