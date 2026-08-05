"""Compact public dashboard bundle derived only from processed datasets."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics

_STAGE_VERSION = "public-dashboard-bundle-2026-08-05-v1"


def build_dashboard_bundle(
    *,
    sources: dict[str, str | Path],
    output_directory: str | Path,
    metadata_path: str | Path,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build a compact, validated snapshot with no API calls or private paths."""
    required = {
        "trends",
        "matrix",
        "map_nodes",
        "map_edges",
        "map_coverage",
        "network_nodes",
        "network_edges",
        "network_accessibility",
        "graph_metrics",
        "sensitivity",
    }
    missing = required.difference(sources)
    if missing:
        raise ValueError(f"dashboard bundle lacks sources: {sorted(missing)}")
    paths = {name: Path(value) for name, value in sources.items()}
    for path in paths.values():
        if not path.is_file():
            raise ValueError(f"dashboard source does not exist: {path}")
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    destinations = {name: output / f"{name}.parquet" for name in required}
    destinations["topics"] = output / "topics.parquet"
    temporary = {name: path.with_suffix(".parquet.tmp") for name, path in destinations.items()}
    metadata = Path(metadata_path)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata_temporary = metadata.with_suffix(".json.tmp")
    for path in [*temporary.values(), metadata_temporary]:
        path.unlink(missing_ok=True)
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        for name in sorted(required):
            connection.execute(
                f"""
                COPY (SELECT * FROM read_parquet(?))
                TO '{_literal(temporary[name])}'
                (FORMAT PARQUET, COMPRESSION ZSTD)
                """,
                [str(paths[name])],
            )
        connection.execute(
            f"""
            COPY (
                SELECT
                    year,
                    corpus_view,
                    hierarchy_view,
                    topic_family,
                    count(*)::BIGINT AS visible_edge_count,
                    sum(full_count)::BIGINT AS full_count,
                    sum(fractional_count) AS fractional_count,
                    sum(distinct_work_count)::BIGINT AS edge_work_count_sum,
                    'top fixed-layout core edges only' AS coverage_note
                FROM (
                    SELECT *, unnest(topic_families) AS topic_family
                    FROM read_parquet(?)
                )
                GROUP BY year, corpus_view, hierarchy_view, topic_family
                ORDER BY year, corpus_view, hierarchy_view, topic_family
            ) TO '{_literal(temporary["topics"])}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [str(paths["network_edges"])],
        )
    except BaseException:
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()

    definitions: dict[str, tuple[list[str], set[str], str | None]] = {
        "trends": (
            ["year", "corpus_view", "hierarchy_view", "source_region", "target_region"],
            {"year", "region_pair", "full_count", "fractional_count"},
            "year",
        ),
        "matrix": (
            [
                "year",
                "corpus_view",
                "hierarchy_view",
                "geographic_level",
                "source_geography",
                "target_geography",
            ],
            {"year", "geographic_level", "normalized_share", "cell_status"},
            "year",
        ),
        "map_nodes": (
            ["year", "corpus_view", "hierarchy_view", "institution_id"],
            {"year", "institution_id", "latitude", "longitude"},
            "year",
        ),
        "map_edges": (
            ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {"year", "source_latitude", "target_latitude", "default_edge_rank"},
            "year",
        ),
        "map_coverage": (
            ["year", "corpus_view", "hierarchy_view"],
            {"year", "coordinate_node_count", "missing_coordinate_node_count"},
            "year",
        ),
        "network_nodes": (
            ["year", "corpus_view", "hierarchy_view", "institution_id"],
            {"year", "institution_id", "x", "y", "community_id"},
            "year",
        ),
        "network_edges": (
            ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {"year", "source_x", "target_x", "fractional_count"},
            "year",
        ),
        "network_accessibility": (
            ["year", "corpus_view", "hierarchy_view"],
            {"year", "summary_text", "coordinate_policy"},
            "year",
        ),
        "graph_metrics": (
            ["year", "corpus_view", "hierarchy_view"],
            {"year", "node_count", "edge_count", "density", "modularity"},
            "year",
        ),
        "sensitivity": (
            ["comparison_id"],
            {"comparison_id", "comparison", "status", "major_change"},
            None,
        ),
        "topics": (
            ["year", "corpus_view", "hierarchy_view", "topic_family"],
            {"year", "topic_family", "full_count", "fractional_count"},
            "year",
        ),
    }
    metrics: dict[str, dict[str, Any]] = {}
    for name, path in temporary.items():
        primary_key, required_columns, year_column = definitions[name]
        metrics[name] = parquet_metrics(
            path,
            primary_key=primary_key,
            required_columns=required_columns,
            year_column=year_column,
        )
    hashes = {name: metrics[name]["checksum_sha256"] for name in sorted(metrics)}
    payload = {
        "schema_version": 1,
        "data_version": "gisnet-0.1.0-2026-08-05",
        "methods_version": _STAGE_VERSION,
        "generated_at_utc": _timestamp(),
        "source_policy": "processed aggregate datasets only; no API requests during viewing",
        "public_snapshot": True,
        "tables": {
            name: {
                "path": f"dashboard/data/{destinations[name].name}",
                "row_count": int(metrics[name]["row_count"]),
                "sha256": hashes[name],
            }
            for name in sorted(destinations)
        },
        "known_limitations": [
            "Institution coordinates are sparse and never imputed.",
            "Network and Topic pages use a thresholded 500-node core and top 1,000 edges per view.",
            "The Topic registry is provisional and has not received human review.",
            "2025 is the last complete calendar year; no partial 2026 data are included.",
            "Visualization score is non-primary and used only to rank visible edges.",
        ],
    }
    _validate_public_metadata(payload)
    metadata_temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for name, path in temporary.items():
        os.replace(path, destinations[name])
    os.replace(metadata_temporary, metadata)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "source_sha256": {name: file_sha256(path) for name, path in sorted(paths.items())},
            }
        ),
        "table_count": len(destinations),
        "row_count": sum(int(value["row_count"]) for value in metrics.values()),
        "table_hashes": hashes,
        "public_snapshot": True,
        "api_requests_during_viewing": False,
        "data_version": payload["data_version"],
        "methods_version": payload["methods_version"],
        "outputs": {
            "dashboard_metadata": str(metadata),
            "dashboard_data_directory": str(output),
        },
        "generated_at_utc": _timestamp(),
    }


def _validate_public_metadata(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload).lower()
    forbidden = ("openalex_api_key", "api_key=", "/home/", ".env")
    found = [value for value in forbidden if value in serialized]
    if found:
        raise ValueError(f"dashboard metadata contains forbidden private values: {found}")


def write_dashboard_artifact(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    write_json_artifact(
        path=summary_path,
        dataset_name="dashboard_bundle_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes={"project": config_file_hash(project_config_path)},
        source_versions={"dashboard_bundle_policy": _STAGE_VERSION},
        source_manifests=[
            ".agent/manifests/trend_series_year.json",
            ".agent/manifests/collaboration_matrix_year.json",
            ".agent/manifests/map_nodes_year.json",
            ".agent/manifests/network_view_nodes_year.json",
        ],
        command=command,
    )


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
