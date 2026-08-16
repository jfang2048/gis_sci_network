"""Build institutional Topic vectors and sparse cosine-proximity networks."""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "institution-topic-similarity-2026-08-17-v3"


def build_topic_similarity(
    work_topics_path: str | Path,
    work_institutions_path: str | Path,
    *,
    vectors_path: str | Path,
    edges_path: str | Path,
    coverage_path: str | Path,
    corpus_views: list[str] | None = None,
    hierarchy_views: list[str] | None = None,
    maximum_institutions_per_view: int = 500,
    top_k: int = 20,
    minimum_similarity: float = 0.0,
    memory_limit: str = "8GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build annual Topic-profile vectors and a union-top-k cosine network.

    Vector dimensions are frozen registry Topics eligible for the selected corpus. Topic scores
    are divided across the in-scope institutions on each Work before institutional aggregation.
    Cosine edges are research-proximity links, never collaboration observations.
    """
    topics = Path(work_topics_path)
    institutions = Path(work_institutions_path)
    for source in (topics, institutions):
        if not source.is_file():
            raise ValueError(f"Topic-similarity input does not exist: {source}")
    corpora = corpus_views or ["strict", "broad"]
    hierarchies = hierarchy_views or ["organization", "umbrella"]
    if not corpora or not set(corpora).issubset({"strict", "broad"}):
        raise ValueError("corpus views must contain only strict and broad")
    if not hierarchies or not set(hierarchies).issubset({"organization", "umbrella"}):
        raise ValueError("hierarchy views must contain only organization and umbrella")
    if maximum_institutions_per_view < 2:
        raise ValueError("maximum institutions per view must be at least two")
    if top_k < 1 or top_k >= maximum_institutions_per_view:
        raise ValueError("top-k must be positive and smaller than the core institution limit")
    if not 0.0 <= minimum_similarity < 1.0:
        raise ValueError("minimum similarity must be in [0, 1)")

    vectors = Path(vectors_path)
    edges = Path(edges_path)
    coverage = Path(coverage_path)
    outputs = (vectors, edges, coverage)
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
    temporary = {output: output.with_suffix(".parquet.tmp") for output in outputs}
    vector_shards = {
        (view, hierarchy): vectors.with_name(f".{vectors.stem}.{view}.{hierarchy}.parquet.tmp")
        for view in corpora
        for hierarchy in hierarchies
    }
    edge_shards = {
        (view, hierarchy): edges.with_name(f".{edges.stem}.{view}.{hierarchy}.parquet.tmp")
        for view in corpora
        for hierarchy in hierarchies
    }
    coverage_shards = {
        (view, hierarchy): coverage.with_name(f".{coverage.stem}.{view}.{hierarchy}.parquet.tmp")
        for view in corpora
        for hierarchy in hierarchies
    }
    scratch = [
        *temporary.values(),
        *vector_shards.values(),
        *edge_shards.values(),
        *coverage_shards.values(),
    ]
    for path in scratch:
        path.unlink(missing_ok=True)

    connection = duckdb.connect()
    try:
        _configure(connection, memory_limit, threads)
        for corpus_view in corpora:
            corpus_flag = f"{corpus_view}_primary"
            topic_condition = _topic_condition(corpus_view)
            for hierarchy_view in hierarchies:
                key = (corpus_view, hierarchy_view)
                _write_vector_shard(
                    connection,
                    topics=topics,
                    institutions=institutions,
                    destination=vector_shards[key],
                    corpus_view=corpus_view,
                    corpus_flag=corpus_flag,
                    topic_condition=topic_condition,
                    hierarchy_view=hierarchy_view,
                    maximum_institutions_per_view=maximum_institutions_per_view,
                )
                _write_edge_shard(
                    connection,
                    vectors=vector_shards[key],
                    destination=edge_shards[key],
                    corpus_view=corpus_view,
                    hierarchy_view=hierarchy_view,
                    maximum_institutions_per_view=maximum_institutions_per_view,
                    top_k=top_k,
                    minimum_similarity=minimum_similarity,
                )
                _write_coverage_shard(
                    connection,
                    topics=topics,
                    institutions=institutions,
                    vectors=vector_shards[key],
                    edges=edge_shards[key],
                    destination=coverage_shards[key],
                    corpus_view=corpus_view,
                    corpus_flag=corpus_flag,
                    topic_condition=topic_condition,
                    hierarchy_view=hierarchy_view,
                    maximum_institutions_per_view=maximum_institutions_per_view,
                    top_k=top_k,
                    minimum_similarity=minimum_similarity,
                )
        _combine_shards(
            connection,
            vector_shards.values(),
            temporary[vectors],
            order="1, 2, 3, 4, 5",
        )
        _combine_shards(
            connection,
            edge_shards.values(),
            temporary[edges],
            order="1, 2, 3, 4, 5",
        )
        _combine_shards(
            connection,
            coverage_shards.values(),
            temporary[coverage],
            order="1, 2, 3",
        )
    except BaseException:
        for path in scratch:
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()

    vector_metrics = parquet_metrics(
        temporary[vectors],
        primary_key=["year", "corpus_view", "hierarchy_view", "institution_id", "topic_id"],
        required_columns={
            "year",
            "institution_id",
            "topic_id",
            "topic_weight",
            "normalized_topic_weight",
            "core_rank",
        },
        year_column="year",
        memory_limit=memory_limit,
    )
    edge_metrics = parquet_metrics(
        temporary[edges],
        primary_key=["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
        required_columns={
            "year",
            "source_id",
            "target_id",
            "cosine_similarity",
            "source_neighbor_rank",
            "target_neighbor_rank",
        },
        year_column="year",
        memory_limit=memory_limit,
    )
    coverage_metrics = parquet_metrics(
        temporary[coverage],
        primary_key=["year", "corpus_view", "hierarchy_view"],
        required_columns={
            "year",
            "in_scope_institution_count",
            "vector_eligible_institution_count",
            "selected_core_institution_count",
            "source_topic_score_sum",
            "vector_topic_weight_sum",
            "selected_similarity_edge_count",
        },
        year_column="year",
        memory_limit=memory_limit,
    )
    validation = duckdb.connect()
    try:
        checks = validation.execute(
            """
            WITH norms AS (
                SELECT year, corpus_view, hierarchy_view, institution_id,
                       abs(sum(normalized_topic_weight * normalized_topic_weight) - 1.0)
                           AS norm_error
                FROM read_parquet(?) GROUP BY 1, 2, 3, 4
            )
            SELECT
                count(*) FILTER (WHERE norm_error > 1e-10),
                max(norm_error),
                (SELECT count(*) FROM read_parquet(?)
                    WHERE source_id >= target_id
                       OR cosine_similarity <= 0
                       OR cosine_similarity > 1.0 + 1e-12
                       OR least(source_neighbor_rank, target_neighbor_rank) > top_k),
                (SELECT count(*) FROM read_parquet(?)
                    WHERE abs(source_topic_score_sum - vector_topic_weight_sum)
                          > 1e-9 * greatest(source_topic_score_sum, 1)),
                (SELECT max(abs(source_topic_score_sum - vector_topic_weight_sum))
                    FROM read_parquet(?)),
                (SELECT min(cosine_similarity) FROM read_parquet(?)),
                (SELECT max(cosine_similarity) FROM read_parquet(?)),
                (SELECT sum(source_topic_score_sum) FROM read_parquet(?)),
                (SELECT sum(vector_topic_weight_sum) FROM read_parquet(?))
            FROM norms
            """,
            [
                str(temporary[vectors]),
                str(temporary[edges]),
                str(temporary[coverage]),
                str(temporary[coverage]),
                str(temporary[edges]),
                str(temporary[edges]),
                str(temporary[coverage]),
                str(temporary[coverage]),
            ],
        ).fetchone()
    finally:
        validation.close()
    if checks is None:
        for path in scratch:
            path.unlink(missing_ok=True)
        raise ValueError("Topic-similarity validation returned no result")
    if int(checks[0]) or int(checks[2]) or int(checks[3]):
        for path in scratch:
            path.unlink(missing_ok=True)
        raise ValueError(
            "Topic-similarity invariants failed: "
            f"vector norm failures={int(checks[0])}, edge failures={int(checks[2])}, "
            f"weight failures={int(checks[3])}"
        )

    for output, path in temporary.items():
        os.replace(path, output)
    for path in [*vector_shards.values(), *edge_shards.values(), *coverage_shards.values()]:
        path.unlink(missing_ok=True)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "work_topics_sha256": file_sha256(topics),
                "work_institutions_sha256": file_sha256(institutions),
                "corpus_views": corpora,
                "hierarchy_views": hierarchies,
                "maximum_institutions_per_view": maximum_institutions_per_view,
                "top_k": top_k,
                "minimum_similarity": minimum_similarity,
            }
        ),
        "layer_semantics": "Topic-profile research proximity, not collaboration",
        "topic_dimensions": (
            "frozen registry Topics eligible for each selected corpus; uncertain and excluded "
            "Topics are omitted"
        ),
        "topic_weight_policy": (
            "source Topic score divided across in-scope institutions on each Work"
        ),
        "similarity_metric": "cosine similarity of L2-normalized institutional Topic vectors",
        "edge_selection_policy": "union of top-k neighbors per institution",
        "maximum_institutions_per_view": maximum_institutions_per_view,
        "top_k": top_k,
        "minimum_similarity": minimum_similarity,
        "vector_component_count": int(vector_metrics["row_count"]),
        "annual_similarity_edge_count": int(edge_metrics["row_count"]),
        "coverage_row_count": int(coverage_metrics["row_count"]),
        "maximum_vector_norm_error": float(checks[1] or 0.0),
        "maximum_weight_reconciliation_error": float(checks[4] or 0.0),
        "minimum_selected_similarity": float(checks[5]) if checks[5] is not None else None,
        "maximum_selected_similarity": float(checks[6]) if checks[6] is not None else None,
        "view_source_topic_score_sum": float(checks[7] or 0.0),
        "view_vector_topic_weight_sum": float(checks[8] or 0.0),
        "corpus_views": corpora,
        "hierarchy_views": hierarchies,
        "outputs": {
            "institution_topic_vectors_year": str(vectors),
            "topic_similarity_edges_year": str(edges),
            "topic_similarity_coverage_year": str(coverage),
        },
        "generated_at_utc": _timestamp(),
    }


def write_topic_similarity_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    """Write the summary and provenance manifests for the Topic-proximity layer."""
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [
        ".agent/manifests/work_topics.json",
        ".agent/manifests/work_institutions.json",
    ]
    source_versions = {"topic_similarity_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="topic_similarity_summary",
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
        "institution_topic_vectors_year": (
            ["year", "corpus_view", "hierarchy_view", "institution_id", "topic_id"],
            {"year", "institution_id", "topic_id", "normalized_topic_weight"},
        ),
        "topic_similarity_edges_year": (
            ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {"year", "source_id", "target_id", "cosine_similarity"},
        ),
        "topic_similarity_coverage_year": (
            ["year", "corpus_view", "hierarchy_view"],
            {
                "year",
                "in_scope_institution_count",
                "vector_eligible_institution_count",
                "selected_similarity_edge_count",
            },
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


def _write_vector_shard(
    connection: duckdb.DuckDBPyConnection,
    *,
    topics: Path,
    institutions: Path,
    destination: Path,
    corpus_view: str,
    corpus_flag: str,
    topic_condition: str,
    hierarchy_view: str,
    maximum_institutions_per_view: int,
) -> None:
    connection.execute(
        f"""
        COPY (
            WITH nodes AS (
                SELECT DISTINCT
                    publication_year AS analysis_year,
                    work_id,
                    institution_id,
                    display_name,
                    macro_region,
                    subregion,
                    country_code,
                    normalized_category
                FROM read_parquet(?)
                WHERE hierarchy_view = '{hierarchy_view}'
                  AND {corpus_flag}
                  AND is_primary_network_scope
            ), counted_nodes AS (
                SELECT *, count(*) OVER (PARTITION BY analysis_year, work_id)
                    AS institution_count
                FROM nodes
            ), institution_counts AS (
                SELECT
                    analysis_year,
                    institution_id,
                    any_value(display_name) AS display_name,
                    any_value(macro_region) AS macro_region,
                    any_value(subregion) AS subregion,
                    any_value(country_code) AS country_code,
                    any_value(normalized_category) AS institution_category,
                    count(DISTINCT work_id)::BIGINT AS institution_work_count
                FROM counted_nodes
                GROUP BY analysis_year, institution_id
            ), ranked_institutions AS (
                SELECT *, row_number() OVER (
                    PARTITION BY analysis_year
                    ORDER BY institution_work_count DESC, institution_id
                )::INTEGER AS core_rank
                FROM institution_counts
            ), components AS (
                SELECT
                    node.analysis_year,
                    node.institution_id,
                    topic.topic_id,
                    any_value(topic.topic_name) AS topic_name,
                    any_value(topic.subfield_name) AS subfield_name,
                    any_value(topic.field_name) AS field_name,
                    any_value(topic.domain_name) AS domain_name,
                    sum(topic.topic_score / node.institution_count) AS topic_weight,
                    count(DISTINCT node.work_id)::BIGINT AS contributing_work_count
                FROM counted_nodes node
                INNER JOIN read_parquet(?) topic USING (work_id)
                WHERE topic.topic_score > 0 AND {topic_condition}
                GROUP BY node.analysis_year, node.institution_id, topic.topic_id
            ), weighted AS (
                SELECT
                    component.*,
                    institution.display_name,
                    institution.macro_region,
                    institution.subregion,
                    institution.country_code,
                    institution.institution_category,
                    institution.institution_work_count,
                    institution.core_rank,
                    sqrt(sum(component.topic_weight * component.topic_weight) OVER (
                        PARTITION BY component.analysis_year, component.institution_id
                    )) AS l2_norm
                FROM components component
                INNER JOIN ranked_institutions institution
                    USING (analysis_year, institution_id)
            )
            SELECT
                analysis_year AS year,
                '{corpus_view}' AS corpus_view,
                '{hierarchy_view}' AS hierarchy_view,
                institution_id,
                display_name,
                macro_region,
                subregion,
                country_code,
                institution_category,
                institution_work_count,
                core_rank,
                core_rank <= {maximum_institutions_per_view} AS is_similarity_core,
                topic_id,
                topic_name,
                subfield_name,
                field_name,
                domain_name,
                contributing_work_count,
                topic_weight,
                l2_norm,
                topic_weight / l2_norm AS normalized_topic_weight,
                'source Topic score divided across in-scope institutions on each Work'
                    AS topic_weight_policy,
                'Topic-profile research proximity; not collaboration' AS layer_semantics
            FROM weighted
            WHERE l2_norm > 0
            ORDER BY analysis_year, institution_id, topic_id
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """,
        [str(institutions), str(topics)],
    )


def _write_edge_shard(
    connection: duckdb.DuckDBPyConnection,
    *,
    vectors: Path,
    destination: Path,
    corpus_view: str,
    hierarchy_view: str,
    maximum_institutions_per_view: int,
    top_k: int,
    minimum_similarity: float,
) -> None:
    connection.execute(
        f"""
        COPY (
            WITH core_vectors AS (
                SELECT * FROM read_parquet(?) WHERE is_similarity_core
            ), raw_pairs AS (
                SELECT
                    source.year,
                    source.institution_id AS source_id,
                    target.institution_id AS target_id,
                    any_value(source.display_name) AS source_name,
                    any_value(target.display_name) AS target_name,
                    any_value(source.macro_region) AS source_region,
                    any_value(target.macro_region) AS target_region,
                    any_value(source.country_code) AS source_country,
                    any_value(target.country_code) AS target_country,
                    any_value(source.institution_category) AS source_category,
                    any_value(target.institution_category) AS target_category,
                    any_value(source.institution_work_count)::BIGINT AS source_work_count,
                    any_value(target.institution_work_count)::BIGINT AS target_work_count,
                    count(*)::INTEGER AS shared_topic_count,
                    sum(
                        source.normalized_topic_weight * target.normalized_topic_weight
                    ) AS raw_cosine_similarity
                FROM core_vectors source
                INNER JOIN core_vectors target
                    ON target.year = source.year
                   AND target.topic_id = source.topic_id
                   AND source.institution_id < target.institution_id
                GROUP BY source.year, source.institution_id, target.institution_id
            ), pairs AS (
                SELECT
                    *,
                    least(1.0, greatest(0.0, raw_cosine_similarity)) AS cosine_similarity,
                    count(*) OVER (PARTITION BY year)::BIGINT
                        AS threshold_eligible_pair_count
                FROM raw_pairs
                WHERE raw_cosine_similarity > {minimum_similarity}
            ), directed AS (
                SELECT year, source_id AS institution_id, target_id AS neighbor_id,
                       cosine_similarity
                FROM pairs
                UNION ALL
                SELECT year, target_id AS institution_id, source_id AS neighbor_id,
                       cosine_similarity
                FROM pairs
            ), ranked AS (
                SELECT *, row_number() OVER (
                    PARTITION BY year, institution_id
                    ORDER BY cosine_similarity DESC, neighbor_id
                )::INTEGER AS neighbor_rank
                FROM directed
            ), pair_ranks AS (
                SELECT
                    year,
                    least(institution_id, neighbor_id) AS source_id,
                    greatest(institution_id, neighbor_id) AS target_id,
                    max(neighbor_rank) FILTER (WHERE institution_id < neighbor_id)::INTEGER
                        AS source_neighbor_rank,
                    max(neighbor_rank) FILTER (WHERE institution_id > neighbor_id)::INTEGER
                        AS target_neighbor_rank
                FROM ranked
                GROUP BY year, least(institution_id, neighbor_id),
                         greatest(institution_id, neighbor_id)
            )
            SELECT
                pair.year,
                '{corpus_view}' AS corpus_view,
                '{hierarchy_view}' AS hierarchy_view,
                pair.source_id,
                pair.target_id,
                pair.source_name,
                pair.target_name,
                pair.source_region,
                pair.target_region,
                pair.source_country,
                pair.target_country,
                pair.source_category,
                pair.target_category,
                pair.source_work_count,
                pair.target_work_count,
                pair.shared_topic_count,
                pair.cosine_similarity,
                rank.source_neighbor_rank,
                rank.target_neighbor_rank,
                pair.threshold_eligible_pair_count,
                {maximum_institutions_per_view}::INTEGER AS maximum_institutions_per_view,
                {top_k}::INTEGER AS top_k,
                {minimum_similarity}::DOUBLE AS minimum_similarity,
                'union of top-k neighbors per institution' AS edge_selection_policy,
                'undirected Topic-profile research proximity; not collaboration'
                    AS layer_semantics
            FROM pairs pair
            INNER JOIN pair_ranks rank USING (year, source_id, target_id)
            WHERE least(rank.source_neighbor_rank, rank.target_neighbor_rank) <= {top_k}
            ORDER BY pair.year, pair.source_id, pair.target_id
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """,
        [str(vectors)],
    )


def _write_coverage_shard(
    connection: duckdb.DuckDBPyConnection,
    *,
    topics: Path,
    institutions: Path,
    vectors: Path,
    edges: Path,
    destination: Path,
    corpus_view: str,
    corpus_flag: str,
    topic_condition: str,
    hierarchy_view: str,
    maximum_institutions_per_view: int,
    top_k: int,
    minimum_similarity: float,
) -> None:
    connection.execute(
        f"""
        COPY (
            WITH nodes AS (
                SELECT DISTINCT publication_year AS analysis_year, work_id, institution_id
                FROM read_parquet(?)
                WHERE hierarchy_view = '{hierarchy_view}'
                  AND {corpus_flag}
                  AND is_primary_network_scope
            ), node_stats AS (
                SELECT analysis_year,
                       count(DISTINCT institution_id)::BIGINT AS in_scope_institution_count
                FROM nodes GROUP BY analysis_year
            ), eligible_works AS (
                SELECT DISTINCT analysis_year, work_id FROM nodes
            ), expected AS (
                SELECT
                    work.analysis_year,
                    sum(topic.topic_score) AS source_topic_score_sum
                FROM eligible_works work
                INNER JOIN read_parquet(?) topic USING (work_id)
                WHERE topic.topic_score > 0 AND {topic_condition}
                GROUP BY work.analysis_year
            ), vector_stats AS (
                SELECT
                    year,
                    count(DISTINCT institution_id)::BIGINT
                        AS vector_eligible_institution_count,
                    count(DISTINCT institution_id) FILTER (WHERE is_similarity_core)::BIGINT
                        AS selected_core_institution_count,
                    count(*)::BIGINT AS vector_component_count,
                    count(DISTINCT topic_id)::INTEGER AS topic_dimension_count,
                    sum(topic_weight) AS vector_topic_weight_sum
                FROM read_parquet(?)
                GROUP BY year
            ), edge_stats AS (
                SELECT
                    year,
                    count(*)::BIGINT AS selected_similarity_edge_count,
                    max(threshold_eligible_pair_count)::BIGINT
                        AS threshold_eligible_pair_count,
                    min(cosine_similarity) AS minimum_selected_similarity,
                    max(cosine_similarity) AS maximum_selected_similarity
                FROM read_parquet(?)
                GROUP BY year
            )
            SELECT
                vector.year,
                '{corpus_view}' AS corpus_view,
                '{hierarchy_view}' AS hierarchy_view,
                node.in_scope_institution_count,
                vector.vector_eligible_institution_count,
                node.in_scope_institution_count - vector.vector_eligible_institution_count
                    AS zero_vector_institution_count,
                vector.selected_core_institution_count,
                vector.vector_eligible_institution_count - vector.selected_core_institution_count
                    AS excluded_from_core_institution_count,
                vector.vector_eligible_institution_count::DOUBLE
                    / node.in_scope_institution_count AS vector_coverage_share,
                vector.selected_core_institution_count::DOUBLE
                    / node.in_scope_institution_count AS core_coverage_share,
                vector.vector_component_count,
                vector.topic_dimension_count,
                (
                    vector.selected_core_institution_count
                    * (vector.selected_core_institution_count - 1) / 2
                )::BIGINT AS candidate_core_pair_count,
                coalesce(edge.threshold_eligible_pair_count, 0)::BIGINT
                    AS threshold_eligible_pair_count,
                coalesce(edge.selected_similarity_edge_count, 0)::BIGINT
                    AS selected_similarity_edge_count,
                edge.minimum_selected_similarity,
                edge.maximum_selected_similarity,
                expected.source_topic_score_sum,
                vector.vector_topic_weight_sum,
                abs(expected.source_topic_score_sum - vector.vector_topic_weight_sum)
                    AS weight_reconciliation_error,
                {maximum_institutions_per_view}::INTEGER AS maximum_institutions_per_view,
                {top_k}::INTEGER AS top_k,
                {minimum_similarity}::DOUBLE AS minimum_similarity,
                'union of top-k neighbors per institution' AS edge_selection_policy,
                'Topic-profile research proximity; not collaboration' AS layer_semantics
            FROM vector_stats vector
            INNER JOIN node_stats node ON node.analysis_year = vector.year
            INNER JOIN expected ON expected.analysis_year = vector.year
            LEFT JOIN edge_stats edge USING (year)
            ORDER BY vector.year
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """,
        [str(institutions), str(topics), str(vectors), str(edges)],
    )


def _topic_condition(corpus_view: str) -> str:
    if corpus_view == "strict":
        return "topic.corpus_membership = 'strict'"
    return "topic.corpus_membership IN ('strict', 'broad_only')"


def _combine_shards(
    connection: duckdb.DuckDBPyConnection,
    shards: Iterable[Path],
    destination: Path,
    *,
    order: str,
) -> None:
    sources = ", ".join(f"'{_literal(path)}'" for path in shards)
    connection.execute(
        f"""
        COPY (
            SELECT * FROM read_parquet([{sources}]) ORDER BY {order}
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
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
    connection.execute("SET temp_directory = 'data/interim/duckdb-topic-similarity'")


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
