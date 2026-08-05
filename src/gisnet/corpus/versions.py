"""Exact-DOI version families and conservative possible-version diagnostics."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "work-version-diagnostics-2026-08-05-v1"
_PRIMARY_TYPES = "'article','conference-paper','review','data-paper','software-paper'"


def build_version_diagnostics(
    works_path: str | Path,
    *,
    diagnostics_path: str | Path,
    duplicate_doi_path: str | Path,
    ambiguous_path: str | Path,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Preserve every Work and select representatives only for exact normalized DOI families."""
    if threads < 1:
        raise ValueError("threads must be positive")
    source = Path(works_path)
    if not source.is_file():
        raise ValueError(f"works dataset does not exist: {source}")
    diagnostics = Path(diagnostics_path)
    duplicate_doi = Path(duplicate_doi_path)
    ambiguous = Path(ambiguous_path)
    for path in (diagnostics, duplicate_doi, ambiguous):
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary_paths = {
        diagnostics: diagnostics.with_suffix(".parquet.tmp"),
        duplicate_doi: duplicate_doi.with_suffix(".parquet.tmp"),
        ambiguous: ambiguous.with_suffix(".parquet.tmp"),
    }
    for temporary in temporary_paths.values():
        temporary.unlink(missing_ok=True)
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        connection.execute("SET preserve_insertion_order = false")
        connection.execute("SET temp_directory = 'data/interim/duckdb-version-diagnostics'")
        connection.execute(
            f"""
            CREATE TEMP TABLE version_diagnostics AS
            WITH base AS (
                SELECT
                    work_id,
                    CASE
                        WHEN doi IS NULL THEN NULL
                        ELSE nullif(
                            regexp_replace(
                                lower(trim(doi)),
                                '^https?://(dx\\.)?doi\\.org/',
                                ''
                            ),
                            ''
                        )
                    END AS normalized_doi,
                    CASE
                        WHEN title IS NULL THEN NULL
                        ELSE nullif(regexp_replace(lower(trim(title)), '[^[:alnum:]]', '', 'g'), '')
                    END AS title_fingerprint,
                    title,
                    publication_year,
                    publication_date,
                    work_type,
                    is_retracted,
                    is_paratext,
                    cited_by_count,
                    updated_date
                FROM read_parquet(?)
            ),
            doi_stats AS (
                SELECT normalized_doi, count(*) AS doi_member_count
                FROM base
                WHERE normalized_doi IS NOT NULL
                GROUP BY normalized_doi
            ),
            title_stats AS (
                SELECT
                    title_fingerprint,
                    count(*) AS title_member_count,
                    count(*) FILTER (WHERE work_type = 'preprint') AS preprint_count,
                    count(*) FILTER (WHERE work_type <> 'preprint') AS published_count,
                    min(publication_year) AS min_year,
                    max(publication_year) AS max_year
                FROM base
                WHERE title_fingerprint IS NOT NULL AND length(title_fingerprint) >= 20
                GROUP BY title_fingerprint
                HAVING count(*) > 1
                   AND count(*) FILTER (WHERE work_type = 'preprint') > 0
                   AND count(*) FILTER (WHERE work_type <> 'preprint') > 0
                   AND max(publication_year) - min(publication_year) BETWEEN 0 AND 3
            ),
            annotated AS (
                SELECT
                    base.*,
                    coalesce(doi_stats.doi_member_count, 1) AS exact_doi_member_count,
                    coalesce(title_stats.title_member_count, 0) AS possible_title_member_count,
                    CASE WHEN doi_stats.doi_member_count > 1
                         THEN 'doi:' || md5(base.normalized_doi) END AS exact_doi_family_id,
                    CASE WHEN title_stats.title_member_count > 1
                         THEN 'title:' || md5(base.title_fingerprint) END
                         AS possible_title_family_id,
                    CASE WHEN doi_stats.doi_member_count > 1
                         THEN base.normalized_doi ELSE base.work_id END AS representative_partition
                FROM base
                LEFT JOIN doi_stats USING (normalized_doi)
                LEFT JOIN title_stats USING (title_fingerprint)
            ),
            ranked AS (
                SELECT
                    *,
                    row_number() OVER representative_window AS representative_rank,
                    first_value(work_id) OVER representative_window
                        AS primary_representative_work_id
                FROM annotated
                WINDOW representative_window AS (
                    PARTITION BY representative_partition
                    ORDER BY
                        is_retracted ASC NULLS LAST,
                        is_paratext ASC NULLS LAST,
                        CASE
                            WHEN work_type IN ({_PRIMARY_TYPES}) THEN 0
                            WHEN work_type = 'preprint' THEN 2
                            ELSE 1
                        END,
                        cited_by_count DESC NULLS LAST,
                        updated_date DESC NULLS LAST,
                        work_id
                )
            )
            SELECT
                work_id,
                normalized_doi,
                exact_doi_family_id,
                possible_title_family_id,
                coalesce(exact_doi_family_id, 'work:' || work_id) AS version_family_id,
                CASE
                    WHEN exact_doi_family_id IS NOT NULL THEN 'exact_normalized_doi'
                    WHEN possible_title_family_id IS NOT NULL THEN 'possible_title_match_unresolved'
                    ELSE 'singleton'
                END AS family_match_basis,
                exact_doi_member_count,
                possible_title_member_count,
                possible_title_family_id IS NOT NULL AND exact_doi_family_id IS NULL
                    AS ambiguous_possible_family,
                representative_rank = 1 AS is_recommended_primary_representative,
                representative_rank,
                primary_representative_work_id,
                CASE
                    WHEN exact_doi_family_id IS NOT NULL AND representative_rank = 1
                        THEN 'selected_deterministically_within_exact_doi_family'
                    WHEN exact_doi_family_id IS NOT NULL
                        THEN 'non_representative_exact_doi_duplicate'
                    WHEN possible_title_family_id IS NOT NULL
                        THEN 'retained_because_title_only_family_is_ambiguous'
                    ELSE 'singleton_record'
                END AS representative_reason,
                true AS include_all_versions_sensitivity
            FROM ranked
            ORDER BY work_id
            """,
            [str(source)],
        )
        _copy_table(connection, "version_diagnostics", temporary_paths[diagnostics])
        connection.execute(
            """
            COPY (
                SELECT * FROM version_diagnostics
                WHERE exact_doi_family_id IS NOT NULL
                ORDER BY exact_doi_family_id, representative_rank, work_id
            ) TO ? (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
            """,
            [str(temporary_paths[duplicate_doi])],
        )
        connection.execute(
            """
            COPY (
                SELECT * FROM version_diagnostics
                WHERE ambiguous_possible_family
                ORDER BY possible_title_family_id, work_id
            ) TO ? (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
            """,
            [str(temporary_paths[ambiguous])],
        )
    except BaseException:
        for temporary in temporary_paths.values():
            temporary.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    definitions = {
        diagnostics: (["work_id"], {"work_id", "version_family_id", "representative_rank"}),
        duplicate_doi: (["work_id"], {"work_id", "exact_doi_family_id"}),
        ambiguous: (["work_id"], {"work_id", "possible_title_family_id"}),
    }
    for destination, temporary in temporary_paths.items():
        primary_key, required = definitions[destination]
        parquet_metrics(temporary, primary_key=primary_key, required_columns=required)
    for destination, temporary in temporary_paths.items():
        os.replace(temporary, destination)
    metrics = parquet_metrics(
        diagnostics,
        primary_key=["work_id"],
        required_columns={
            "work_id",
            "version_family_id",
            "is_recommended_primary_representative",
            "include_all_versions_sensitivity",
        },
    )
    source_metrics = parquet_metrics(source, primary_key=["work_id"], required_columns={"work_id"})
    if metrics["row_count"] != source_metrics["row_count"]:
        raise ValueError("version diagnostics did not preserve every source Work")
    summary_connection = duckdb.connect()
    try:
        summary_row = summary_connection.execute(
            """
            SELECT
                count(*) FILTER (WHERE exact_doi_family_id IS NOT NULL),
                count(DISTINCT exact_doi_family_id) FILTER (WHERE exact_doi_family_id IS NOT NULL),
                count(*) FILTER (WHERE ambiguous_possible_family),
                count(DISTINCT possible_title_family_id)
                    FILTER (WHERE ambiguous_possible_family),
                count(*) FILTER (WHERE NOT is_recommended_primary_representative),
                count(*) FILTER (WHERE include_all_versions_sensitivity)
            FROM read_parquet(?)
            """,
            [str(diagnostics)],
        ).fetchone()
    finally:
        summary_connection.close()
    if summary_row is None:
        raise ValueError("version diagnostics summary query failed")
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {"stage_version": _STAGE_VERSION, "works_sha256": file_sha256(source)}
        ),
        "work_count": int(metrics["row_count"]),
        "exact_doi_family_member_count": int(summary_row[0]),
        "exact_doi_family_count": int(summary_row[1]),
        "ambiguous_possible_family_member_count": int(summary_row[2]),
        "ambiguous_possible_family_count": int(summary_row[3]),
        "excluded_duplicate_representative_count": int(summary_row[4]),
        "all_versions_sensitivity_count": int(summary_row[5]),
        "outputs": {
            "work_version_diagnostics": str(diagnostics),
            "work_duplicate_doi_diagnostics": str(duplicate_doi),
            "work_ambiguous_version_candidates": str(ambiguous),
        },
        "generated_at_utc": _timestamp(),
    }


def _copy_table(connection: duckdb.DuckDBPyConnection, table: str, output: Path) -> None:
    connection.execute(
        f"COPY (SELECT * FROM {table}) TO ? "
        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)",
        [str(output)],
    )


def write_version_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [".agent/manifests/works.json"]
    source_versions = {"version_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="work_version_diagnostics_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions=source_versions,
        source_manifests=source_manifests,
        command=command,
    )
    for dataset_name, path in summary["outputs"].items():
        write_parquet_manifest(
            path=path,
            dataset_name=dataset_name,
            primary_key=["work_id"],
            required_columns={"work_id", "version_family_id", "representative_rank"},
            year_column=None,
            run_id=run_id,
            config_hashes=config_hashes,
            source_manifests=source_manifests,
            source_versions=source_versions,
            command=command,
        )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
