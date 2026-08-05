"""Compute institutional full/fractional output and collaboration shares."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "institution-output-2026-08-05-v1"


def build_institution_outputs(
    work_institutions_path: str | Path,
    *,
    outputs_year_path: str | Path,
    reconciliation_path: str | Path,
    corpus_views: list[str] | None = None,
    hierarchy_views: list[str] | None = None,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Aggregate nodes while retaining output-producing institutions with no pair edge."""
    source = Path(work_institutions_path)
    if not source.is_file():
        raise ValueError(f"normalized Work institutions do not exist: {source}")
    corpora = corpus_views or ["strict", "broad"]
    hierarchies = hierarchy_views or ["organization", "umbrella"]
    if not set(corpora).issubset({"strict", "broad"}):
        raise ValueError("unsupported corpus view")
    if not set(hierarchies).issubset({"organization", "umbrella"}):
        raise ValueError("unsupported hierarchy view")
    outputs = [Path(outputs_year_path), Path(reconciliation_path)]
    for path in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary = {path: path.with_suffix(".parquet.tmp") for path in outputs}
    shards = {
        (corpus, hierarchy): outputs[0].with_name(
            f".{outputs[0].stem}.{corpus}.{hierarchy}.parquet.tmp"
        )
        for corpus in corpora
        for hierarchy in hierarchies
    }
    for path in [*temporary.values(), *shards.values()]:
        path.unlink(missing_ok=True)
    connection = duckdb.connect()
    try:
        _configure(connection, memory_limit, threads)
        for (corpus, hierarchy), shard in shards.items():
            flag = f"{corpus}_primary"
            connection.execute(
                f"""
                COPY (
                    WITH nodes AS (
                        SELECT DISTINCT
                            publication_year AS year,
                            work_id,
                            institution_id,
                            display_name,
                            ror_id,
                            country_code,
                            country_name,
                            macro_region,
                            subregion,
                            normalized_category AS institution_category,
                            analytical_scope,
                            latitude,
                            longitude
                        FROM read_parquet(?)
                        WHERE hierarchy_view = '{hierarchy}'
                          AND {flag}
                          AND is_primary_network_scope
                    ), context AS (
                        SELECT
                            *,
                            count(*) OVER (PARTITION BY year, work_id) AS institution_count,
                            count(DISTINCT country_code)
                                OVER (PARTITION BY year, work_id) AS country_count,
                            count(DISTINCT macro_region)
                                OVER (PARTITION BY year, work_id) AS region_count
                        FROM nodes
                    )
                    SELECT
                        year,
                        '{corpus}' AS corpus_view,
                        '{hierarchy}' AS hierarchy_view,
                        institution_id,
                        any_value(display_name) AS display_name,
                        any_value(ror_id) AS ror_id,
                        any_value(country_code) AS country_code,
                        any_value(country_name) AS country_name,
                        any_value(macro_region) AS macro_region,
                        any_value(subregion) AS subregion,
                        any_value(institution_category) AS institution_category,
                        any_value(analytical_scope) AS analytical_scope,
                        any_value(latitude) AS latitude,
                        any_value(longitude) AS longitude,
                        count(*)::BIGINT AS work_count,
                        sum(1.0 / institution_count) AS fractional_work_count,
                        count(*) FILTER (WHERE institution_count >= 2)::BIGINT
                            AS collaborative_work_count,
                        count(*) FILTER (WHERE institution_count = 1)::BIGINT
                            AS single_institution_work_count,
                        count(*) FILTER (WHERE country_count >= 2)::BIGINT
                            AS international_work_count,
                        count(*) FILTER (WHERE region_count >= 2)::BIGINT
                            AS cross_region_work_count,
                        count(*) FILTER (WHERE country_count >= 2)::DOUBLE / count(*)
                            AS international_collaboration_share,
                        count(*) FILTER (WHERE region_count >= 2)::DOUBLE / count(*)
                            AS cross_region_collaboration_share
                    FROM context
                    GROUP BY year, institution_id
                    ORDER BY year, institution_id
                ) TO '{_literal(shard)}'
                (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
                """,
                [str(source)],
            )
        sources = ", ".join(f"'{_literal(path)}'" for path in shards.values())
        connection.execute(
            f"""
            COPY (
                SELECT * FROM read_parquet([{sources}])
                ORDER BY year, corpus_view, hierarchy_view, institution_id
            ) TO '{_literal(temporary[outputs[0]])}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
            """
        )
        membership_queries = []
        for corpus in corpora:
            for hierarchy in hierarchies:
                membership_queries.append(
                    f"""
                    SELECT publication_year AS year, '{corpus}' AS corpus_view,
                           '{hierarchy}' AS hierarchy_view, work_id, institution_id
                    FROM read_parquet(?)
                    WHERE hierarchy_view = '{hierarchy}'
                      AND {corpus}_primary
                      AND is_primary_network_scope
                    """
                )
        membership_sql = " UNION ALL ".join(membership_queries)
        connection.execute(
            f"""
            COPY (
                WITH memberships AS ({membership_sql}), expected AS (
                    SELECT
                        year, corpus_view, hierarchy_view,
                        count(DISTINCT work_id)::BIGINT AS expected_work_count,
                        count(*)::BIGINT AS expected_work_institution_row_count
                    FROM memberships
                    GROUP BY year, corpus_view, hierarchy_view
                ), actual AS (
                    SELECT
                        year, corpus_view, hierarchy_view,
                        sum(work_count)::BIGINT AS output_work_count_sum,
                        sum(fractional_work_count) AS fractional_work_count_sum,
                        count(*)::BIGINT AS output_node_count,
                        count(*) FILTER (WHERE collaborative_work_count = 0)::BIGINT
                            AS zero_edge_output_node_count
                    FROM read_parquet(?)
                    GROUP BY year, corpus_view, hierarchy_view
                )
                SELECT
                    expected.*,
                    actual.output_work_count_sum,
                    actual.fractional_work_count_sum,
                    actual.output_node_count,
                    actual.zero_edge_output_node_count,
                    actual.output_work_count_sum - expected.expected_work_institution_row_count
                        AS work_institution_row_difference,
                    actual.fractional_work_count_sum - expected.expected_work_count
                        AS fractional_work_difference
                FROM expected
                INNER JOIN actual USING (year, corpus_view, hierarchy_view)
                ORDER BY year, corpus_view, hierarchy_view
            ) TO '{_literal(temporary[outputs[1]])}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [*[str(source)] * len(membership_queries), str(temporary[outputs[0]])],
        )
    except BaseException:
        for path in [*temporary.values(), *shards.values()]:
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    definitions = {
        outputs[0]: (
            ["year", "corpus_view", "hierarchy_view", "institution_id"],
            {"year", "institution_id", "work_count", "fractional_work_count"},
        ),
        outputs[1]: (
            ["year", "corpus_view", "hierarchy_view"],
            {"year", "expected_work_count", "fractional_work_difference"},
        ),
    }
    for destination, path in temporary.items():
        primary_key, required = definitions[destination]
        parquet_metrics(
            path, primary_key=primary_key, required_columns=required, year_column="year"
        )
    validation = duckdb.connect()
    try:
        invariant = validation.execute(
            """
            SELECT
                max(abs(work_institution_row_difference)),
                max(abs(fractional_work_difference)),
                sum(zero_edge_output_node_count),
                sum(expected_work_count),
                sum(expected_work_institution_row_count)
            FROM read_parquet(?)
            """,
            [str(temporary[outputs[1]])],
        ).fetchone()
        row_count = validation.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(temporary[outputs[0]])]
        ).fetchone()
    finally:
        validation.close()
    if invariant is None or row_count is None:
        raise ValueError("institution-output validation query failed")
    if int(invariant[0]) != 0 or float(invariant[1]) > 1e-8:
        raise ValueError("institution output full/fractional reconciliation failed")
    for destination, path in temporary.items():
        os.replace(path, destination)
    for path in shards.values():
        path.unlink(missing_ok=True)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "work_institutions_sha256": file_sha256(source),
                "corpus_views": corpora,
                "hierarchy_views": hierarchies,
            }
        ),
        "node_year_count": int(row_count[0]),
        "eligible_work_view_year_count": int(invariant[3]),
        "eligible_work_institution_row_count": int(invariant[4]),
        "zero_edge_output_node_year_count": int(invariant[2]),
        "maximum_fractional_reconciliation_error": float(invariant[1]),
        "corpus_views": corpora,
        "hierarchy_views": hierarchies,
        "outputs": {
            "institution_outputs_year": str(outputs[0]),
            "institution_output_reconciliation": str(outputs[1]),
        },
        "generated_at_utc": _timestamp(),
    }


def _configure(connection: duckdb.DuckDBPyConnection, memory_limit: str, threads: int) -> None:
    connection.execute("SET memory_limit = ?", [memory_limit])
    connection.execute("SET threads = ?", [threads])
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET temp_directory = 'data/interim/duckdb-institution-outputs'")


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def write_output_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [".agent/manifests/work_institutions.json"]
    source_versions = {"institution_output_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="institution_outputs_summary",
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
        "institution_outputs_year": (
            ["year", "corpus_view", "hierarchy_view", "institution_id"],
            {"year", "institution_id", "work_count", "fractional_work_count"},
        ),
        "institution_output_reconciliation": (
            ["year", "corpus_view", "hierarchy_view"],
            {"year", "expected_work_count", "fractional_work_difference"},
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
