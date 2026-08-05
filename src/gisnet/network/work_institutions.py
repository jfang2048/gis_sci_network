"""Join Work corpus memberships to deduplicated institution hierarchy views."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "normalized-work-institutions-2026-08-05-v1"


def build_normalized_work_institutions(
    extracted_path: str | Path,
    corpus_path: str | Path,
    institutions_path: str | Path,
    hierarchy_path: str | Path,
    *,
    output_path: str | Path,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build one row per Work, hierarchy view, and canonical institution."""
    inputs = [
        Path(extracted_path),
        Path(corpus_path),
        Path(institutions_path),
        Path(hierarchy_path),
    ]
    for source in inputs:
        if not source.is_file():
            raise ValueError(f"work-institution input does not exist: {source}")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".parquet.tmp")
    temporary.unlink(missing_ok=True)
    view_temporaries = {
        view: output.with_name(f".{output.stem}.{view}.parquet.tmp")
        for view in ("organization", "umbrella")
    }
    for path in view_temporaries.values():
        path.unlink(missing_ok=True)
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        connection.execute("SET preserve_insertion_order = false")
        connection.execute("SET temp_directory = 'data/interim/duckdb-work-institutions'")
        for view, view_temporary in view_temporaries.items():
            destination = str(view_temporary).replace("'", "''")
            connection.execute(
                f"""
            COPY (
                WITH joined AS (
                    SELECT
                        wi.work_id,
                        wi.publication_year,
                        h.hierarchy_view,
                        h.canonical_institution_id,
                        wi.institution_id AS original_institution_id,
                        wi.display_name AS original_display_name,
                        wi.raw_affiliation_strings,
                        wi.authorship_count,
                        wi.assertion_count,
                        h.is_collapsed,
                        h.canonicalization_rule_ids,
                        c.title,
                        c.doi,
                        c.work_type,
                        c.primary_topic_id,
                        c.primary_topic_name,
                        c.method_families,
                        c.strict_primary,
                        c.broad_primary,
                        c.strict_preprint_sensitivity,
                        c.broad_preprint_sensitivity,
                        c.strict_expanded_sensitivity,
                        c.broad_expanded_sensitivity,
                        c.strict_all_versions_sensitivity,
                        c.broad_all_versions_sensitivity,
                        c.uncertain_topic_sensitivity
                    FROM read_parquet(?) wi
                    INNER JOIN read_parquet(?) c USING (work_id)
                    INNER JOIN read_parquet(?) h
                        ON h.institution_id = wi.institution_id
                       AND h.hierarchy_view = '{view}'
                ), aggregated AS (
                    SELECT
                        work_id,
                        publication_year,
                        hierarchy_view,
                        canonical_institution_id AS institution_id,
                        list_sort(list_distinct(list(original_institution_id)))
                            AS original_institution_ids,
                        list_sort(list_distinct(list(original_display_name)
                            FILTER (WHERE original_display_name IS NOT NULL)))
                            AS original_display_names,
                        list_sort(list_distinct(flatten(list(raw_affiliation_strings))))
                            AS raw_affiliation_strings,
                        sum(authorship_count)::BIGINT AS authorship_count,
                        sum(assertion_count)::BIGINT AS assertion_count,
                        count(DISTINCT original_institution_id)::INTEGER
                            AS contributing_organization_count,
                        bool_or(is_collapsed) OR count(DISTINCT original_institution_id) > 1
                            AS was_collapsed,
                        list_sort(list_distinct(flatten(list(canonicalization_rule_ids))))
                            AS canonicalization_rule_ids,
                        any_value(title) AS title,
                        any_value(doi) AS doi,
                        any_value(work_type) AS work_type,
                        any_value(primary_topic_id) AS primary_topic_id,
                        any_value(primary_topic_name) AS primary_topic_name,
                        any_value(method_families) AS method_families,
                        bool_or(strict_primary) AS strict_primary,
                        bool_or(broad_primary) AS broad_primary,
                        bool_or(strict_preprint_sensitivity) AS strict_preprint_sensitivity,
                        bool_or(broad_preprint_sensitivity) AS broad_preprint_sensitivity,
                        bool_or(strict_expanded_sensitivity) AS strict_expanded_sensitivity,
                        bool_or(broad_expanded_sensitivity) AS broad_expanded_sensitivity,
                        bool_or(strict_all_versions_sensitivity)
                            AS strict_all_versions_sensitivity,
                        bool_or(broad_all_versions_sensitivity)
                            AS broad_all_versions_sensitivity,
                        bool_or(uncertain_topic_sensitivity) AS uncertain_topic_sensitivity
                    FROM joined
                    GROUP BY work_id, publication_year, hierarchy_view,
                             canonical_institution_id
                )
                SELECT
                    a.*,
                    i.ror_id,
                    i.display_name,
                    i.institution_type,
                    i.normalized_category,
                    i.analytical_scope,
                    i.is_primary_research_scope,
                    i.country_code,
                    i.country_name,
                    i.macro_region,
                    i.subregion,
                    i.latitude,
                    i.longitude,
                    i.macro_region IN ('Europe', 'Asia', 'Americas') AS is_target_macro_region,
                    i.is_primary_research_scope
                        AND i.macro_region IN ('Europe', 'Asia', 'Americas')
                        AS is_primary_network_scope
                FROM aggregated a
                INNER JOIN read_parquet(?) i
                    ON i.institution_id = a.institution_id
                ORDER BY a.publication_year, a.work_id, a.hierarchy_view, a.institution_id
            ) TO '{destination}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
                """,
                [
                    str(extracted_path),
                    str(corpus_path),
                    str(hierarchy_path),
                    str(institutions_path),
                ],
            )
        sources = ", ".join(
            f"'{str(path).replace(chr(39), chr(39) * 2)}'" for path in view_temporaries.values()
        )
        final_destination = str(temporary).replace("'", "''")
        connection.execute(
            f"""
            COPY (
                SELECT * FROM read_parquet([{sources}])
                ORDER BY publication_year, work_id, hierarchy_view, institution_id
            ) TO '{final_destination}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
            """
        )
    except BaseException:
        temporary.unlink(missing_ok=True)
        for path in view_temporaries.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    for path in view_temporaries.values():
        path.unlink(missing_ok=True)
    metrics = parquet_metrics(
        temporary,
        primary_key=["work_id", "hierarchy_view", "institution_id"],
        required_columns={
            "work_id",
            "publication_year",
            "hierarchy_view",
            "institution_id",
            "original_institution_ids",
            "strict_primary",
            "broad_primary",
            "is_primary_network_scope",
        },
        year_column="publication_year",
    )
    os.replace(temporary, output)
    summary_connection = duckdb.connect()
    try:
        values = summary_connection.execute(
            """
            WITH per_work AS (
                SELECT hierarchy_view, work_id, count(*) AS institution_count
                FROM read_parquet(?)
                GROUP BY hierarchy_view, work_id
            )
            SELECT
                (SELECT count(*) FROM read_parquet(?)
                    WHERE hierarchy_view = 'organization'),
                (SELECT count(*) FROM read_parquet(?)
                    WHERE hierarchy_view = 'umbrella'),
                count(DISTINCT work_id) FILTER (WHERE hierarchy_view = 'organization'),
                count(DISTINCT work_id) FILTER (WHERE hierarchy_view = 'umbrella'),
                count(*) FILTER (WHERE hierarchy_view = 'organization' AND institution_count = 1),
                count(*) FILTER (WHERE hierarchy_view = 'umbrella' AND institution_count = 1),
                max(institution_count),
                count(*) FILTER (WHERE institution_count < 1)
            FROM per_work
            """,
            [str(output), str(output), str(output)],
        ).fetchone()
    finally:
        summary_connection.close()
    if values is None or int(values[7]) != 0 or int(values[2]) != int(values[3]):
        raise ValueError("work-institution hierarchy views failed reconciliation")
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "input_hashes": [file_sha256(path) for path in inputs],
            }
        ),
        "row_count": int(metrics["row_count"]),
        "organization_row_count": int(values[0]),
        "umbrella_row_count": int(values[1]),
        "organization_work_count": int(values[2]),
        "umbrella_work_count": int(values[3]),
        "organization_single_institution_work_count": int(values[4]),
        "umbrella_single_institution_work_count": int(values[5]),
        "maximum_institution_count_per_work": int(values[6]),
        "outputs": {"work_institutions": str(output)},
        "generated_at_utc": _timestamp(),
    }


def write_work_institution_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [
        ".agent/manifests/work_institutions_extracted.json",
        ".agent/manifests/work_corpus.json",
        ".agent/manifests/institutions_ror.json",
        ".agent/manifests/institution_hierarchy.json",
    ]
    source_versions = {"work_institution_schema": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="work_institutions_summary",
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
        path=summary["outputs"]["work_institutions"],
        dataset_name="work_institutions",
        primary_key=["work_id", "hierarchy_view", "institution_id"],
        required_columns={
            "work_id",
            "publication_year",
            "hierarchy_view",
            "institution_id",
            "strict_primary",
            "broad_primary",
            "is_primary_network_scope",
        },
        year_column="publication_year",
        run_id=run_id,
        config_hashes=config_hashes,
        source_manifests=source_manifests,
        source_versions=source_versions,
        command=command,
    )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
