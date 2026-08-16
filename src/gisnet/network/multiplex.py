"""Compare co-authorship, citation, and Topic-proximity layers without merging them."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "multiplex-separate-layer-comparison-2026-08-17-v1"


def build_multiplex_comparison(
    collaboration_edges_path: str | Path,
    citation_edges_path: str | Path,
    topic_similarity_edges_path: str | Path,
    *,
    layer_summary_path: str | Path,
    overlap_path: str | Path,
    memory_limit: str = "8GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Summarize layers separately and compare only unweighted node/dyad presence."""
    collaboration = Path(collaboration_edges_path)
    citation = Path(citation_edges_path)
    proximity = Path(topic_similarity_edges_path)
    for source in (collaboration, citation, proximity):
        if not source.is_file():
            raise ValueError(f"multiplex input does not exist: {source}")
    layer_summary = Path(layer_summary_path)
    overlap = Path(overlap_path)
    outputs = (layer_summary, overlap)
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
    temporary = {output: output.with_suffix(".parquet.tmp") for output in outputs}
    for path in temporary.values():
        path.unlink(missing_ok=True)

    connection = duckdb.connect()
    try:
        _configure(connection, memory_limit, threads)
        _create_layer_view(connection, collaboration, citation, proximity)
        _write_layer_summary(connection, temporary[layer_summary])
        _write_overlap(connection, temporary[overlap])
    except BaseException:
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()

    layer_metrics = parquet_metrics(
        temporary[layer_summary],
        primary_key=["year", "corpus_view", "hierarchy_view", "layer"],
        required_columns={
            "year",
            "layer",
            "directionality",
            "node_count",
            "edge_count",
            "undirected_dyad_count",
            "total_layer_weight",
            "weight_semantics",
        },
        year_column="year",
        memory_limit=memory_limit,
    )
    overlap_metrics = parquet_metrics(
        temporary[overlap],
        primary_key=["year", "corpus_view", "hierarchy_view", "layer_a", "layer_b"],
        required_columns={
            "year",
            "layer_a",
            "layer_b",
            "shared_dyad_count",
            "dyad_jaccard",
            "shared_node_count",
            "node_jaccard",
            "comparison_weighting",
        },
        year_column="year",
        memory_limit=memory_limit,
    )
    validation = duckdb.connect()
    try:
        checks = validation.execute(
            """
            SELECT
                (SELECT count(*) FROM read_parquet(?)
                    WHERE layer NOT IN ('coauthorship', 'citation_flow', 'topic_proximity')
                       OR node_count < 1 OR edge_count < 1 OR undirected_dyad_count < 0
                       OR density < 0 OR density > 1),
                (SELECT count(*) FROM read_parquet(?)
                    WHERE layer_a >= layer_b
                       OR shared_dyad_count < 0
                       OR union_dyad_count < shared_dyad_count
                       OR dyad_jaccard < 0 OR dyad_jaccard > 1
                       OR node_jaccard < 0 OR node_jaccard > 1
                       OR comparison_weighting
                          <> 'unweighted presence only; no layer weights applied'
                       OR NOT layers_remain_separate),
                (SELECT count(DISTINCT layer) FROM read_parquet(?)),
                (SELECT min(year) FROM read_parquet(?)),
                (SELECT max(year) FROM read_parquet(?))
            """,
            [
                str(temporary[layer_summary]),
                str(temporary[overlap]),
                str(temporary[layer_summary]),
                str(temporary[layer_summary]),
                str(temporary[layer_summary]),
            ],
        ).fetchone()
    finally:
        validation.close()
    if checks is None or int(checks[0]) or int(checks[1]) or int(checks[2]) != 3:
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise ValueError("multiplex separate-layer invariants failed")
    for output, path in temporary.items():
        os.replace(path, output)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "collaboration_edges_sha256": file_sha256(collaboration),
                "citation_edges_sha256": file_sha256(citation),
                "topic_similarity_edges_sha256": file_sha256(proximity),
            }
        ),
        "layers": ["coauthorship", "citation_flow", "topic_proximity"],
        "layers_merged": False,
        "composite_weight_defined": False,
        "comparison_weighting": "unweighted presence only; no layer weights applied",
        "citation_overlap_projection": "direction ignored for dyad presence only",
        "citation_direction_preserved_in_layer_summary": True,
        "layer_summary_row_count": int(layer_metrics["row_count"]),
        "pairwise_overlap_row_count": int(overlap_metrics["row_count"]),
        "year_minimum": int(checks[3]),
        "year_maximum": int(checks[4]),
        "outputs": {
            "multiplex_layer_summary_year": str(layer_summary),
            "multiplex_pairwise_overlap_year": str(overlap),
        },
        "generated_at_utc": _timestamp(),
    }


def write_multiplex_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    """Write the comparison summary and manifests without creating a merged edge table."""
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [
        ".agent/manifests/edges_year.json",
        ".agent/manifests/citation_edges_year.json",
        ".agent/manifests/topic_similarity_edges_year.json",
    ]
    source_versions = {"multiplex_comparison_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="multiplex_comparison_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions=source_versions,
        source_manifests=source_manifests,
        command=command,
    )
    definitions = {
        "multiplex_layer_summary_year": (
            ["year", "corpus_view", "hierarchy_view", "layer"],
            {"year", "layer", "edge_count", "weight_semantics"},
        ),
        "multiplex_pairwise_overlap_year": (
            ["year", "corpus_view", "hierarchy_view", "layer_a", "layer_b"],
            {"year", "layer_a", "layer_b", "dyad_jaccard", "node_jaccard"},
        ),
    }
    for dataset_name, path in summary["outputs"].items():
        primary_key, required = definitions[dataset_name]
        write_parquet_manifest(
            path=path,
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


def _create_layer_view(
    connection: duckdb.DuckDBPyConnection,
    collaboration: Path,
    citation: Path,
    proximity: Path,
) -> None:
    connection.execute(
        f"""
        CREATE TEMP VIEW layer_edges AS
        SELECT year, corpus_view, hierarchy_view, 'coauthorship' AS layer,
               source_id, target_id, fractional_count::DOUBLE AS layer_weight,
               'undirected' AS directionality,
               'fractional co-authorship weight' AS weight_semantics,
               'complete annual co-authorship edges' AS coverage_scope
        FROM read_parquet('{_literal(collaboration)}')
        UNION ALL
        SELECT year, corpus_view, hierarchy_view, 'citation_flow' AS layer,
               source_id, target_id, fractional_count::DOUBLE AS layer_weight,
               'directed' AS directionality,
               'fractional citation-flow weight' AS weight_semantics,
               'corpus-internal institution-resolved citation edges' AS coverage_scope
        FROM read_parquet('{_literal(citation)}')
        UNION ALL
        SELECT year, corpus_view, hierarchy_view, 'topic_proximity' AS layer,
               source_id, target_id, cosine_similarity::DOUBLE AS layer_weight,
               'undirected' AS directionality,
               'cosine Topic-profile proximity' AS weight_semantics,
               'union-top-k edges in the deterministic annual similarity core'
                   AS coverage_scope
        FROM read_parquet('{_literal(proximity)}')
        """,
    )


def _write_layer_summary(connection: duckdb.DuckDBPyConnection, destination: Path) -> None:
    connection.execute(
        f"""
        COPY (
            WITH nodes AS (
                SELECT year, corpus_view, hierarchy_view, layer, source_id AS institution_id
                FROM layer_edges
                UNION
                SELECT year, corpus_view, hierarchy_view, layer, target_id AS institution_id
                FROM layer_edges
            ), node_counts AS (
                SELECT year, corpus_view, hierarchy_view, layer,
                       count(*)::BIGINT AS node_count
                FROM nodes GROUP BY 1, 2, 3, 4
            ), edge_counts AS (
                SELECT
                    year, corpus_view, hierarchy_view, layer,
                    any_value(directionality) AS directionality,
                    count(*)::BIGINT AS edge_count,
                    count(*) FILTER (WHERE source_id = target_id)::BIGINT AS self_edge_count,
                    sum(layer_weight) AS total_layer_weight,
                    any_value(weight_semantics) AS weight_semantics,
                    any_value(coverage_scope) AS coverage_scope
                FROM layer_edges GROUP BY 1, 2, 3, 4
            ), dyads AS (
                SELECT DISTINCT year, corpus_view, hierarchy_view, layer,
                       least(source_id, target_id) AS source_id,
                       greatest(source_id, target_id) AS target_id
                FROM layer_edges WHERE source_id <> target_id
            ), dyad_counts AS (
                SELECT year, corpus_view, hierarchy_view, layer,
                       count(*)::BIGINT AS undirected_dyad_count
                FROM dyads GROUP BY 1, 2, 3, 4
            )
            SELECT
                edge.year,
                edge.corpus_view,
                edge.hierarchy_view,
                edge.layer,
                edge.directionality,
                node.node_count,
                edge.edge_count,
                edge.self_edge_count,
                coalesce(dyad.undirected_dyad_count, 0)::BIGINT AS undirected_dyad_count,
                CASE WHEN edge.directionality = 'directed'
                     THEN node.node_count * node.node_count
                     ELSE node.node_count * (node.node_count - 1) / 2
                END::BIGINT AS possible_edge_count,
                edge.edge_count::DOUBLE / nullif(CASE WHEN edge.directionality = 'directed'
                    THEN node.node_count * node.node_count
                    ELSE node.node_count * (node.node_count - 1) / 2
                END, 0) AS density,
                edge.total_layer_weight,
                edge.weight_semantics,
                edge.coverage_scope,
                false AS composite_weight_defined,
                'layers remain separate; totals have incomparable units' AS comparison_boundary
            FROM edge_counts edge
            INNER JOIN node_counts node USING (year, corpus_view, hierarchy_view, layer)
            LEFT JOIN dyad_counts dyad USING (year, corpus_view, hierarchy_view, layer)
            ORDER BY edge.year, edge.corpus_view, edge.hierarchy_view, edge.layer
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )


def _write_overlap(connection: duckdb.DuckDBPyConnection, destination: Path) -> None:
    connection.execute(
        f"""
        COPY (
            WITH dyads AS (
                SELECT DISTINCT year, corpus_view, hierarchy_view, layer,
                       least(source_id, target_id) AS source_id,
                       greatest(source_id, target_id) AS target_id
                FROM layer_edges WHERE source_id <> target_id
            ), dyad_counts AS (
                SELECT year, corpus_view, hierarchy_view, layer,
                       count(*)::BIGINT AS dyad_count
                FROM dyads GROUP BY 1, 2, 3, 4
            ), dyad_intersections AS (
                SELECT
                    a.year, a.corpus_view, a.hierarchy_view,
                    a.layer AS layer_a, b.layer AS layer_b,
                    count(*)::BIGINT AS shared_dyad_count
                FROM dyads a
                INNER JOIN dyads b
                    ON b.year = a.year
                   AND b.corpus_view = a.corpus_view
                   AND b.hierarchy_view = a.hierarchy_view
                   AND b.source_id = a.source_id
                   AND b.target_id = a.target_id
                   AND a.layer < b.layer
                GROUP BY 1, 2, 3, 4, 5
            ), nodes AS (
                SELECT year, corpus_view, hierarchy_view, layer, source_id AS institution_id
                FROM layer_edges
                UNION
                SELECT year, corpus_view, hierarchy_view, layer, target_id AS institution_id
                FROM layer_edges
            ), node_counts AS (
                SELECT year, corpus_view, hierarchy_view, layer,
                       count(*)::BIGINT AS node_count
                FROM nodes GROUP BY 1, 2, 3, 4
            ), node_intersections AS (
                SELECT
                    a.year, a.corpus_view, a.hierarchy_view,
                    a.layer AS layer_a, b.layer AS layer_b,
                    count(*)::BIGINT AS shared_node_count
                FROM nodes a
                INNER JOIN nodes b
                    ON b.year = a.year
                   AND b.corpus_view = a.corpus_view
                   AND b.hierarchy_view = a.hierarchy_view
                   AND b.institution_id = a.institution_id
                   AND a.layer < b.layer
                GROUP BY 1, 2, 3, 4, 5
            ), layer_pairs AS (
                SELECT DISTINCT
                    a.year, a.corpus_view, a.hierarchy_view,
                    a.layer AS layer_a, b.layer AS layer_b
                FROM dyad_counts a
                INNER JOIN dyad_counts b
                    ON b.year = a.year
                   AND b.corpus_view = a.corpus_view
                   AND b.hierarchy_view = a.hierarchy_view
                   AND a.layer < b.layer
            )
            SELECT
                pair.year,
                pair.corpus_view,
                pair.hierarchy_view,
                pair.layer_a,
                pair.layer_b,
                count_a.dyad_count AS layer_a_dyad_count,
                count_b.dyad_count AS layer_b_dyad_count,
                coalesce(dyad.shared_dyad_count, 0)::BIGINT AS shared_dyad_count,
                count_a.dyad_count + count_b.dyad_count
                    - coalesce(dyad.shared_dyad_count, 0) AS union_dyad_count,
                coalesce(dyad.shared_dyad_count, 0)::DOUBLE / (
                    count_a.dyad_count + count_b.dyad_count
                    - coalesce(dyad.shared_dyad_count, 0)
                ) AS dyad_jaccard,
                coalesce(dyad.shared_dyad_count, 0)::DOUBLE
                    / least(count_a.dyad_count, count_b.dyad_count)
                    AS dyad_overlap_coefficient,
                node_a.node_count AS layer_a_node_count,
                node_b.node_count AS layer_b_node_count,
                coalesce(node.shared_node_count, 0)::BIGINT AS shared_node_count,
                node_a.node_count + node_b.node_count
                    - coalesce(node.shared_node_count, 0) AS union_node_count,
                coalesce(node.shared_node_count, 0)::DOUBLE / (
                    node_a.node_count + node_b.node_count
                    - coalesce(node.shared_node_count, 0)
                ) AS node_jaccard,
                CASE WHEN pair.layer_a = 'citation_flow' OR pair.layer_b = 'citation_flow'
                     THEN 'direction ignored for dyad presence only'
                     ELSE 'not applicable; both layers are undirected'
                END AS citation_overlap_projection,
                'unweighted presence only; no layer weights applied' AS comparison_weighting,
                true AS layers_remain_separate
            FROM layer_pairs pair
            INNER JOIN dyad_counts count_a
                ON count_a.year = pair.year
               AND count_a.corpus_view = pair.corpus_view
               AND count_a.hierarchy_view = pair.hierarchy_view
               AND count_a.layer = pair.layer_a
            INNER JOIN dyad_counts count_b
                ON count_b.year = pair.year
               AND count_b.corpus_view = pair.corpus_view
               AND count_b.hierarchy_view = pair.hierarchy_view
               AND count_b.layer = pair.layer_b
            INNER JOIN node_counts node_a
                ON node_a.year = pair.year
               AND node_a.corpus_view = pair.corpus_view
               AND node_a.hierarchy_view = pair.hierarchy_view
               AND node_a.layer = pair.layer_a
            INNER JOIN node_counts node_b
                ON node_b.year = pair.year
               AND node_b.corpus_view = pair.corpus_view
               AND node_b.hierarchy_view = pair.hierarchy_view
               AND node_b.layer = pair.layer_b
            LEFT JOIN dyad_intersections dyad
                ON dyad.year = pair.year
               AND dyad.corpus_view = pair.corpus_view
               AND dyad.hierarchy_view = pair.hierarchy_view
               AND dyad.layer_a = pair.layer_a
               AND dyad.layer_b = pair.layer_b
            LEFT JOIN node_intersections node
                ON node.year = pair.year
               AND node.corpus_view = pair.corpus_view
               AND node.hierarchy_view = pair.hierarchy_view
               AND node.layer_a = pair.layer_a
               AND node.layer_b = pair.layer_b
            ORDER BY pair.year, pair.corpus_view, pair.hierarchy_view,
                     pair.layer_a, pair.layer_b
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )


def _configure(
    connection: duckdb.DuckDBPyConnection,
    memory_limit: str,
    threads: int,
) -> None:
    connection.execute("SET memory_limit = ?", [memory_limit])
    connection.execute("SET threads = ?", [threads])
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET temp_directory = 'data/interim/duckdb-multiplex'")


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
