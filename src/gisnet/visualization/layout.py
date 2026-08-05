"""Fixed aggregate network coordinates with deterministic fallback positions."""

from __future__ import annotations

import hashlib
import math
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

_STAGE_VERSION = "fixed-aggregate-layout-2026-08-05-v1"
_LAYOUT_ALGORITHM = "seeded_fruchterman_reingold_core_plus_sha256_annulus_fallback"


def build_fixed_layout(
    edges_path: str | Path,
    nodes_path: str | Path,
    *,
    output_path: str | Path,
    random_seed: int,
    core_size: int = 500,
    corpus_view: str = "broad",
    hierarchy_view: str = "organization",
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build one full-period layout reused by every annual network view."""
    edges_source = Path(edges_path)
    nodes_source = Path(nodes_path)
    for path in (edges_source, nodes_source):
        if not path.is_file():
            raise ValueError(f"layout input does not exist: {path}")
    if core_size < 2:
        raise ValueError("layout core_size must be at least two")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".parquet.tmp")
    temporary.unlink(missing_ok=True)

    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        node_rows = (
            connection.execute(
                """
            SELECT
                institution_id,
                max_by(display_name, year) AS display_name,
                max_by(macro_region, year) AS macro_region,
                max_by(country_code, year) AS country_code,
                sum(fractional_strength) AS aggregate_fractional_strength,
                count(DISTINCT year)::INTEGER AS active_year_count
            FROM read_parquet(?)
            WHERE corpus_view = ? AND hierarchy_view = ?
            GROUP BY institution_id
            ORDER BY aggregate_fractional_strength DESC, institution_id
            """,
                [str(nodes_source), corpus_view, hierarchy_view],
            )
            .to_arrow_table()
            .to_pylist()
        )
        core_rows = node_rows[: min(core_size, len(node_rows))]
        core_ids_ranked = [str(row["institution_id"]) for row in core_rows]
        core_ids = sorted(core_ids_ranked)
        core_index = {identifier: position for position, identifier in enumerate(core_ids)}
        edge_rows = connection.execute(
            """
            SELECT source_id, target_id, sum(fractional_count) AS weight
            FROM read_parquet(?)
            WHERE corpus_view = ? AND hierarchy_view = ?
              AND source_id IN (SELECT unnest(?::VARCHAR[]))
              AND target_id IN (SELECT unnest(?::VARCHAR[]))
            GROUP BY source_id, target_id
            ORDER BY source_id, target_id
            """,
            [str(edges_source), corpus_view, hierarchy_view, core_ids, core_ids],
        ).fetchall()
    finally:
        connection.close()
    graph = ig.Graph(
        n=len(core_ids),
        edges=[
            (core_index[str(source)], core_index[str(target)]) for source, target, _ in edge_rows
        ],
        directed=False,
    )
    weights = [float(weight) for _, _, weight in edge_rows]
    initial = [
        [
            math.cos(2 * math.pi * position / len(core_ids)),
            math.sin(2 * math.pi * position / len(core_ids)),
        ]
        for position in range(len(core_ids))
    ]
    ig.set_random_number_generator(random.Random(random_seed))
    coordinates = graph.layout_fruchterman_reingold(
        weights=weights,
        niter=500,
        seed=initial,
        grid="auto",
    ).coords
    normalized = _normalize(coordinates)
    core_coordinates = {
        identifier: (float(normalized[position][0]), float(normalized[position][1]))
        for position, identifier in enumerate(core_ids)
    }
    rank = {identifier: position + 1 for position, identifier in enumerate(core_ids_ranked)}
    output_rows = []
    for row in sorted(node_rows, key=lambda item: str(item["institution_id"])):
        identifier = str(row["institution_id"])
        is_core = identifier in core_coordinates
        x, y = core_coordinates[identifier] if is_core else _fallback(identifier)
        output_rows.append(
            {
                "institution_id": identifier,
                "display_name": row["display_name"],
                "macro_region": row["macro_region"],
                "country_code": row["country_code"],
                "x": x,
                "y": y,
                "is_core": is_core,
                "core_rank": rank.get(identifier),
                "aggregate_fractional_strength": float(row["aggregate_fractional_strength"]),
                "active_year_count": int(row["active_year_count"]),
                "layout_method": _LAYOUT_ALGORITHM,
                "layout_version": _STAGE_VERSION,
                "random_seed": random_seed,
                "core_size_parameter": core_size,
                "core_corpus_view": corpus_view,
                "core_hierarchy_view": hierarchy_view,
            }
        )
    pq.write_table(pa.Table.from_pylist(output_rows), temporary, compression="zstd")
    metrics = parquet_metrics(
        temporary,
        primary_key=["institution_id"],
        required_columns={"institution_id", "x", "y", "is_core", "layout_method"},
    )
    validation = duckdb.connect()
    try:
        invariants = validation.execute(
            """
            SELECT
                count(*) FILTER (WHERE NOT isfinite(x) OR NOT isfinite(y)),
                count(*) FILTER (WHERE is_core),
                count(*) FILTER (WHERE NOT is_core),
                min(x), max(x), min(y), max(y)
            FROM read_parquet(?)
            """,
            [str(temporary)],
        ).fetchone()
    finally:
        validation.close()
    if invariants is None or int(invariants[0]) or int(invariants[1]) != len(core_ids):
        temporary.unlink(missing_ok=True)
        raise ValueError("fixed-layout finiteness or core-coverage invariant failed")
    os.replace(temporary, output)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "edges_sha256": file_sha256(edges_source),
                "nodes_sha256": file_sha256(nodes_source),
                "random_seed": random_seed,
                "core_size": core_size,
                "corpus_view": corpus_view,
                "hierarchy_view": hierarchy_view,
            }
        ),
        "institution_count": int(metrics["row_count"]),
        "core_institution_count": int(invariants[1]),
        "fallback_institution_count": int(invariants[2]),
        "aggregate_core_edge_count": len(edge_rows),
        "coordinate_bounds": {
            "minimum_x": float(invariants[3]),
            "maximum_x": float(invariants[4]),
            "minimum_y": float(invariants[5]),
            "maximum_y": float(invariants[6]),
        },
        "layout_method": _LAYOUT_ALGORITHM,
        "random_seed": random_seed,
        "core_size": core_size,
        "outputs": {"network_layout": str(output)},
        "generated_at_utc": _timestamp(),
    }


def write_layout_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    """Write fixed-layout summary and dataset manifest."""
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [
        ".agent/manifests/edges_year.json",
        ".agent/manifests/nodes_year.json",
    ]
    source_versions = {"layout_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="network_layout_summary",
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
        path=summary["outputs"]["network_layout"],
        dataset_name="network_layout",
        primary_key=["institution_id"],
        required_columns={"institution_id", "x", "y", "is_core", "layout_method"},
        year_column=None,
        run_id=run_id,
        config_hashes=config_hashes,
        source_manifests=source_manifests,
        source_versions=source_versions,
        command=command,
    )


def _normalize(coordinates: list[list[float]]) -> list[list[float]]:
    if not coordinates:
        return []
    mean_x = sum(value[0] for value in coordinates) / len(coordinates)
    mean_y = sum(value[1] for value in coordinates) / len(coordinates)
    centered = [[value[0] - mean_x, value[1] - mean_y] for value in coordinates]
    scale = max(max(abs(value[0]), abs(value[1])) for value in centered) or 1.0
    return [[value[0] / scale, value[1] / scale] for value in centered]


def _fallback(institution_id: str) -> tuple[float, float]:
    digest = hashlib.sha256(f"{_STAGE_VERSION}:{institution_id}".encode()).digest()
    angle = int.from_bytes(digest[:8], "big") / (2**64) * 2 * math.pi
    radius = 1.10 + int.from_bytes(digest[8:16], "big") / (2**64) * 0.35
    return radius * math.cos(angle), radius * math.sin(angle)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
