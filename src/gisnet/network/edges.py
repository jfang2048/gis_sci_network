"""Construct deterministic per-Work pairs and annual collaboration edges."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "annual-collaboration-edges-2026-08-05-v1"


def build_collaboration_edges(
    work_institutions_path: str | Path,
    *,
    work_edges_path: str | Path,
    edges_year_path: str | Path,
    diagnostics_path: str | Path,
    warning_institution_count: int = 25,
    exclusion_institution_count: int = 100,
    corpus_views: list[str] | None = None,
    hierarchy_views: list[str] | None = None,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Generate full/fractional pairs; source IDs are always lexically smaller than targets."""
    source = Path(work_institutions_path)
    if not source.is_file():
        raise ValueError(f"normalized Work institutions do not exist: {source}")
    if warning_institution_count < 2 or exclusion_institution_count < warning_institution_count:
        raise ValueError("invalid consortium thresholds")
    corpora = corpus_views or ["strict", "broad"]
    hierarchies = hierarchy_views or ["organization", "umbrella"]
    if not corpora or not set(corpora).issubset({"strict", "broad"}):
        raise ValueError("corpus views must contain only strict and broad")
    if not hierarchies or not set(hierarchies).issubset({"organization", "umbrella"}):
        raise ValueError("hierarchy views must contain only organization and umbrella")
    outputs = [Path(work_edges_path), Path(edges_year_path), Path(diagnostics_path)]
    for path in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary = {path: path.with_suffix(".parquet.tmp") for path in outputs}
    shard_paths = {
        (corpus, hierarchy): outputs[0].with_name(
            f".{outputs[0].stem}.{corpus}.{hierarchy}.parquet.tmp"
        )
        for corpus in corpora
        for hierarchy in hierarchies
    }
    for path in [*temporary.values(), *shard_paths.values()]:
        path.unlink(missing_ok=True)
    connection = duckdb.connect()
    try:
        _configure(connection, memory_limit, threads, "data/interim/duckdb-edges")
        for (corpus, hierarchy), shard in shard_paths.items():
            flag = f"{corpus}_primary"
            destination_literal = _literal(shard)
            connection.execute(
                f"""
                COPY (
                    WITH nodes AS (
                        SELECT DISTINCT
                            publication_year AS year,
                            work_id,
                            institution_id,
                            display_name,
                            macro_region,
                            subregion,
                            country_code,
                            normalized_category AS institution_category,
                            method_families
                        FROM read_parquet(?)
                        WHERE hierarchy_view = '{hierarchy}'
                          AND {flag}
                          AND is_primary_network_scope
                    ), counted AS (
                        SELECT *, count(*) OVER (PARTITION BY year, work_id) AS institution_count
                        FROM nodes
                    )
                    SELECT
                        a.year,
                        '{corpus}' AS corpus_view,
                        '{hierarchy}' AS hierarchy_view,
                        a.work_id,
                        a.institution_id AS source_id,
                        b.institution_id AS target_id,
                        a.display_name AS source_name,
                        b.display_name AS target_name,
                        a.macro_region AS source_region,
                        b.macro_region AS target_region,
                        a.subregion AS source_subregion,
                        b.subregion AS target_subregion,
                        a.country_code AS source_country,
                        b.country_code AS target_country,
                        a.institution_category AS source_category,
                        b.institution_category AS target_category,
                        a.institution_count::INTEGER AS institution_count,
                        1::INTEGER AS full_weight,
                        2.0 / (a.institution_count * (a.institution_count - 1))
                            AS fractional_weight,
                        a.institution_count >= {warning_institution_count}
                            AS is_large_consortium,
                        a.institution_count >= {exclusion_institution_count}
                            AS exceeds_consortium_exclusion_threshold,
                        a.method_families
                    FROM counted a
                    INNER JOIN counted b
                        ON a.year = b.year
                       AND a.work_id = b.work_id
                       AND a.institution_id < b.institution_id
                    WHERE a.institution_count >= 2
                    ORDER BY a.year, a.work_id, a.institution_id, b.institution_id
                ) TO '{destination_literal}'
                (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
                """,
                [str(source)],
            )
        sources = ", ".join(f"'{_literal(path)}'" for path in shard_paths.values())
        connection.execute(
            f"""
            COPY (
                SELECT * FROM read_parquet([{sources}])
                ORDER BY year, corpus_view, hierarchy_view, work_id, source_id, target_id
            ) TO '{_literal(temporary[outputs[0]])}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
            """
        )
        connection.execute(
            f"""
            COPY (
                SELECT
                    year,
                    corpus_view,
                    hierarchy_view,
                    work_id,
                    any_value(institution_count)::INTEGER AS institution_count,
                    count(*)::BIGINT AS generated_pair_count,
                    (any_value(institution_count) * (any_value(institution_count) - 1) / 2)::BIGINT
                        AS expected_pair_count,
                    sum(full_weight)::BIGINT AS full_weight_sum,
                    sum(fractional_weight) AS fractional_weight_sum,
                    abs(sum(fractional_weight) - 1.0) AS fractional_sum_absolute_error,
                    bool_or(is_large_consortium) AS is_large_consortium,
                    bool_or(exceeds_consortium_exclusion_threshold)
                        AS exceeds_consortium_exclusion_threshold
                FROM read_parquet(?)
                GROUP BY year, corpus_view, hierarchy_view, work_id
                ORDER BY year, corpus_view, hierarchy_view, work_id
            ) TO '{_literal(temporary[outputs[2]])}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
            """,
            [str(temporary[outputs[0]])],
        )
        connection.execute(
            f"""
            COPY (
                WITH aggregated AS (
                    SELECT
                        year,
                        corpus_view,
                        hierarchy_view,
                        source_id,
                        target_id,
                        any_value(source_name) AS source_name,
                        any_value(target_name) AS target_name,
                        any_value(source_region) AS source_region,
                        any_value(target_region) AS target_region,
                        any_value(source_subregion) AS source_subregion,
                        any_value(target_subregion) AS target_subregion,
                        any_value(source_country) AS source_country,
                        any_value(target_country) AS target_country,
                        any_value(source_category) AS source_category,
                        any_value(target_category) AS target_category,
                        sum(full_weight)::BIGINT AS full_count,
                        sum(fractional_weight) AS fractional_count,
                        count(*)::BIGINT AS distinct_work_count,
                        count(*) FILTER (WHERE is_large_consortium)::BIGINT
                            AS large_consortium_work_count,
                        count(*) FILTER (WHERE exceeds_consortium_exclusion_threshold)::BIGINT
                            AS excluded_threshold_work_count,
                        max(institution_count)::INTEGER AS maximum_consortium_size,
                        list_sort(list_distinct(flatten(list(method_families)))) AS topic_families,
                        list_slice(list_sort(list(work_id)), 1, 10) AS work_ids_sample
                    FROM read_parquet(?)
                    GROUP BY year, corpus_view, hierarchy_view, source_id, target_id
                )
                SELECT
                    *,
                    len(topic_families)::INTEGER AS distinct_topic_family_count
                FROM aggregated
                ORDER BY year, corpus_view, hierarchy_view, source_id, target_id
            ) TO '{_literal(temporary[outputs[1]])}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
            """,
            [str(temporary[outputs[0]])],
        )
    except BaseException:
        for path in [*temporary.values(), *shard_paths.values()]:
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    definitions = {
        outputs[0]: (
            ["work_id", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {"year", "work_id", "corpus_view", "source_id", "target_id", "fractional_weight"},
        ),
        outputs[1]: (
            ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {"year", "corpus_view", "hierarchy_view", "source_id", "target_id", "full_count"},
        ),
        outputs[2]: (
            ["year", "corpus_view", "hierarchy_view", "work_id"],
            {"year", "work_id", "institution_count", "fractional_weight_sum"},
        ),
    }
    for output_destination, path in temporary.items():
        primary_key, required = definitions[output_destination]
        parquet_metrics(
            path, primary_key=primary_key, required_columns=required, year_column="year"
        )
    validation = duckdb.connect()
    try:
        invariants = validation.execute(
            """
            SELECT
                count(*) FILTER (WHERE source_id >= target_id),
                count(*) FILTER (WHERE generated_pair_count <> expected_pair_count),
                max(fractional_sum_absolute_error),
                count(*) FILTER (WHERE abs(fractional_weight_sum - 1.0) > 1e-10),
                count(*) FILTER (WHERE is_large_consortium),
                count(*) FILTER (WHERE exceeds_consortium_exclusion_threshold)
            FROM read_parquet(?) diagnostics
            LEFT JOIN (
                SELECT year, corpus_view, hierarchy_view, work_id,
                       min(source_id) AS source_id, max(target_id) AS target_id
                FROM read_parquet(?)
                GROUP BY year, corpus_view, hierarchy_view, work_id
            ) pairs USING (year, corpus_view, hierarchy_view, work_id)
            """,
            [str(temporary[outputs[2]]), str(temporary[outputs[0]])],
        ).fetchone()
        counts = validation.execute(
            """
            SELECT
                (SELECT count(*) FROM read_parquet(?)),
                (SELECT count(*) FROM read_parquet(?)),
                (SELECT count(*) FROM read_parquet(?)),
                (SELECT sum(full_count) FROM read_parquet(?)),
                (SELECT sum(full_weight) FROM read_parquet(?))
            """,
            [
                str(temporary[outputs[0]]),
                str(temporary[outputs[1]]),
                str(temporary[outputs[2]]),
                str(temporary[outputs[1]]),
                str(temporary[outputs[0]]),
            ],
        ).fetchone()
    finally:
        validation.close()
    if invariants is None or counts is None:
        raise ValueError("edge validation query failed")
    if int(invariants[0]) or int(invariants[1]) or int(invariants[3]):
        raise ValueError("edge ordering, pair-count, or fractional-sum invariant failed")
    if int(counts[3]) != int(counts[4]):
        raise ValueError("annual full counts do not reconcile with Work-edge contributions")
    for output_destination, path in temporary.items():
        os.replace(path, output_destination)
    for path in shard_paths.values():
        path.unlink(missing_ok=True)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "work_institutions_sha256": file_sha256(source),
                "warning_institution_count": warning_institution_count,
                "exclusion_institution_count": exclusion_institution_count,
                "corpus_views": corpora,
                "hierarchy_views": hierarchies,
            }
        ),
        "work_edge_count": int(counts[0]),
        "annual_edge_count": int(counts[1]),
        "collaborative_work_view_count": int(counts[2]),
        "maximum_fractional_sum_absolute_error": float(invariants[2] or 0.0),
        "large_consortium_work_view_count": int(invariants[4]),
        "excluded_threshold_work_view_count": int(invariants[5]),
        "warning_institution_count": warning_institution_count,
        "exclusion_institution_count": exclusion_institution_count,
        "corpus_views": corpora,
        "hierarchy_views": hierarchies,
        "outputs": {
            "work_edges": str(outputs[0]),
            "edges_year": str(outputs[1]),
            "edge_work_diagnostics": str(outputs[2]),
        },
        "generated_at_utc": _timestamp(),
    }


def _configure(
    connection: duckdb.DuckDBPyConnection, memory_limit: str, threads: int, temp_directory: str
) -> None:
    connection.execute("SET memory_limit = ?", [memory_limit])
    connection.execute("SET threads = ?", [threads])
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET temp_directory = ?", [temp_directory])


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def write_edge_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [".agent/manifests/work_institutions.json"]
    source_versions = {"edge_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="collaboration_edges_summary",
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
        "work_edges": (
            ["work_id", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {"year", "work_id", "corpus_view", "hierarchy_view", "fractional_weight"},
        ),
        "edges_year": (
            ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {"year", "corpus_view", "hierarchy_view", "full_count", "fractional_count"},
        ),
        "edge_work_diagnostics": (
            ["year", "corpus_view", "hierarchy_view", "work_id"],
            {"year", "work_id", "institution_count", "fractional_weight_sum"},
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


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
