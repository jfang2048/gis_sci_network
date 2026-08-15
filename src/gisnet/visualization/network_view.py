"""Filter-ready fixed-layout institutional network visualization tables."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "fixed-layout-network-view-2026-08-06-v2"
EDGE_WIDTH_ENCODING = "constant; selected weight controls inclusion only"


def build_network_view(
    nodes_path: str | Path,
    edges_path: str | Path,
    communities_path: str | Path,
    layout_path: str | Path,
    *,
    nodes_output_path: str | Path,
    edges_output_path: str | Path,
    accessibility_output_path: str | Path,
    edge_limit_per_view: int = 1000,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build a legible core network using one layout for all annual views."""
    sources = [Path(nodes_path), Path(edges_path), Path(communities_path), Path(layout_path)]
    for path in sources:
        if not path.is_file():
            raise ValueError(f"network-view input does not exist: {path}")
    outputs = [Path(nodes_output_path), Path(edges_output_path), Path(accessibility_output_path)]
    for path in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary = {path: path.with_suffix(".parquet.tmp") for path in outputs}
    for path in temporary.values():
        path.unlink(missing_ok=True)
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        connection.execute(
            f"""
            COPY (
                SELECT
                    nodes.*,
                    layout.x,
                    layout.y,
                    layout.core_rank,
                    communities.community_id,
                    communities.community_size,
                    'fractional_strength (selectable)' AS node_size_encoding,
                    'macro_region or community_id (selectable)' AS node_color_encoding,
                    'fixed full-period aggregate coordinates' AS coordinate_encoding
                FROM read_parquet(?) nodes
                INNER JOIN read_parquet(?) layout USING (institution_id)
                LEFT JOIN read_parquet(?) communities
                    ON communities.year = nodes.year
                   AND communities.corpus_view = nodes.corpus_view
                   AND communities.hierarchy_view = nodes.hierarchy_view
                   AND communities.institution_id = nodes.institution_id
                WHERE layout.is_core
                ORDER BY nodes.year, nodes.corpus_view, nodes.hierarchy_view, layout.core_rank
            ) TO '{_literal(temporary[outputs[0]])}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [str(sources[0]), str(sources[3]), str(sources[2])],
        )
        connection.execute(
            f"""
            COPY (
                WITH core_edges AS (
                    SELECT
                        edges.*,
                        source.x AS source_x,
                        source.y AS source_y,
                        target.x AS target_x,
                        target.y AS target_y,
                        row_number() OVER (
                            PARTITION BY edges.year, edges.corpus_view, edges.hierarchy_view
                            ORDER BY edges.fractional_count DESC,
                                     edges.source_id, edges.target_id
                        )::INTEGER AS default_edge_rank
                    FROM read_parquet(?) edges
                    INNER JOIN read_parquet(?) source
                        ON source.institution_id = edges.source_id AND source.is_core
                    INNER JOIN read_parquet(?) target
                        ON target.institution_id = edges.target_id AND target.is_core
                )
                SELECT *,
                    ?::INTEGER AS default_edge_limit,
                    '{EDGE_WIDTH_ENCODING}' AS edge_width_encoding,
                    'macro-region pair' AS edge_color_encoding
                FROM core_edges
                WHERE default_edge_rank <= ?
                ORDER BY year, corpus_view, hierarchy_view, default_edge_rank
            ) TO '{_literal(temporary[outputs[1]])}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [
                str(sources[1]),
                str(sources[3]),
                str(sources[3]),
                edge_limit_per_view,
                edge_limit_per_view,
            ],
        )
        summaries = (
            connection.execute(
                """
            WITH node_summary AS (
                SELECT year, corpus_view, hierarchy_view,
                    count(*)::BIGINT AS node_count,
                    max_by(display_name, fractional_strength) AS top_institution,
                    max(fractional_strength) AS top_fractional_strength,
                    count(*) FILTER (WHERE degree = 0)::BIGINT AS isolated_node_count
                FROM read_parquet(?) GROUP BY ALL
            ), edge_summary AS (
                SELECT year, corpus_view, hierarchy_view,
                    count(*)::BIGINT AS edge_count,
                    count(*) FILTER (WHERE source_region != target_region)::BIGINT
                        AS cross_region_edge_count,
                    min(fractional_count) AS visible_minimum_fractional_weight
                FROM read_parquet(?) GROUP BY ALL
            )
            SELECT node_summary.*,
                   coalesce(edge_summary.edge_count, 0)::BIGINT AS edge_count,
                   coalesce(edge_summary.cross_region_edge_count, 0)::BIGINT
                       AS cross_region_edge_count,
                   edge_summary.visible_minimum_fractional_weight
            FROM node_summary
            LEFT JOIN edge_summary USING (year, corpus_view, hierarchy_view)
            ORDER BY year, corpus_view, hierarchy_view
            """,
                [str(temporary[outputs[0]]), str(temporary[outputs[1]])],
            )
            .to_arrow_table()
            .to_pylist()
        )
        for row in summaries:
            row["summary_text"] = _accessibility_sentence(row)
            row["coordinate_policy"] = "fixed across all years"
            row["default_edge_limit"] = edge_limit_per_view
        pq.write_table(pa.Table.from_pylist(summaries), temporary[outputs[2]], compression="zstd")
    except BaseException:
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    definitions = {
        outputs[0]: (
            ["year", "corpus_view", "hierarchy_view", "institution_id"],
            {"year", "institution_id", "x", "y", "community_id", "node_size_encoding"},
        ),
        outputs[1]: (
            ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {"year", "source_x", "target_x", "default_edge_rank", "edge_width_encoding"},
        ),
        outputs[2]: (
            ["year", "corpus_view", "hierarchy_view"],
            {"year", "node_count", "edge_count", "summary_text", "coordinate_policy"},
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
                count(*) FILTER (WHERE coordinate_count != 1),
                count(*)
            FROM (
                SELECT institution_id, count(DISTINCT (x, y)) AS coordinate_count
                FROM read_parquet(?) GROUP BY institution_id
            )
            """,
            [str(temporary[outputs[0]])],
        ).fetchone()
        edges_check = validation.execute(
            "SELECT coalesce(max(default_edge_rank), 0) FROM read_parquet(?)",
            [str(temporary[outputs[1]])],
        ).fetchone()
    finally:
        validation.close()
    if (
        checks is None
        or edges_check is None
        or int(checks[0])
        or int(edges_check[0]) > edge_limit_per_view
    ):
        raise ValueError("fixed-coordinate or network-view threshold invariant failed")
    for destination, path in temporary.items():
        os.replace(path, destination)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "source_sha256": [file_sha256(path) for path in sources],
                "edge_limit_per_view": edge_limit_per_view,
            }
        ),
        "network_node_row_count": int(metrics[outputs[0]]["row_count"]),
        "network_edge_row_count": int(metrics[outputs[1]]["row_count"]),
        "accessibility_summary_row_count": int(metrics[outputs[2]]["row_count"]),
        "distinct_layout_node_count": int(checks[1]),
        "institutions_with_unstable_coordinates": int(checks[0]),
        "default_edge_limit_per_view": edge_limit_per_view,
        "encodings": {
            "node_size": ["work_count", "degree", "fractional_strength", "pagerank"],
            "node_color": ["macro_region", "community_id"],
            "edge_width": EDGE_WIDTH_ENCODING,
        },
        "outputs": {
            "network_view_nodes_year": str(outputs[0]),
            "network_view_edges_year": str(outputs[1]),
            "network_accessibility_year": str(outputs[2]),
        },
        "generated_at_utc": _timestamp(),
    }


def _accessibility_sentence(row: dict[str, Any]) -> str:
    weight = row.get("visible_minimum_fractional_weight")
    weight_text = f"{float(weight):.4g}" if weight is not None else "not applicable"
    return (
        f"In {int(row['year'])}, the {row['corpus_view']} corpus at the "
        f"{row['hierarchy_view']} hierarchy shows {int(row['node_count']):,} core institutions "
        f"and {int(row['edge_count']):,} visible edges. {int(row['cross_region_edge_count']):,} "
        f"edges cross macro-regions. The leading institution by fractional strength is "
        f"{row['top_institution']}. Edges use constant display width; fractional weight controls "
        f"inclusion, and the visible minimum fractional edge weight is {weight_text}."
    )


def write_network_view_artifacts(
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
        ".agent/manifests/communities_year.json",
        ".agent/manifests/network_layout.json",
    ]
    source_versions = {"network_view_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="network_view_summary",
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
            "network_view_nodes_year",
            ["year", "corpus_view", "hierarchy_view", "institution_id"],
            {"year", "institution_id", "x", "y", "node_size_encoding"},
        ),
        (
            "network_view_edges_year",
            ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {"year", "source_x", "target_x", "default_edge_rank"},
        ),
        (
            "network_accessibility_year",
            ["year", "corpus_view", "hierarchy_view"],
            {"year", "summary_text", "coordinate_policy"},
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
