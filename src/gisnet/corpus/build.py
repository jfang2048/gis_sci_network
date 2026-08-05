"""Materialize Strict, Broad, and configured sensitivity corpus memberships."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.corpus.work_types import WorkTypePolicy
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "work-corpus-membership-2026-08-05-v1"


def build_work_corpus(
    works_path: str | Path,
    work_topics_path: str | Path,
    version_diagnostics_path: str | Path,
    policy: WorkTypePolicy,
    *,
    corpus_path: str | Path,
    annual_counts_path: str | Path,
    topic_family_counts_path: str | Path,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build all flags without filtering out any normalized source Work."""
    inputs = [Path(works_path), Path(work_topics_path), Path(version_diagnostics_path)]
    for source in inputs:
        if not source.is_file():
            raise ValueError(f"corpus input does not exist: {source}")
    outputs = [Path(corpus_path), Path(annual_counts_path), Path(topic_family_counts_path)]
    temporary = {path: path.with_suffix(".parquet.tmp") for path in outputs}
    for path in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
    for path in temporary.values():
        path.unlink(missing_ok=True)
    policy_rows = [
        {
            "work_type": work_type,
            "primary_included": rule.primary,
            "preprint_included": rule.preprint_sensitivity,
            "expanded_included": rule.expanded_sensitivity,
        }
        for work_type, rule in sorted(policy.types.items())
    ]
    policy_table = pa.Table.from_pylist(policy_rows)
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        connection.execute("SET preserve_insertion_order = false")
        connection.execute("SET temp_directory = 'data/interim/duckdb-corpus'")
        connection.register("work_type_policy", policy_table)
        connection.execute(
            """
            CREATE TEMP TABLE work_corpus AS
            WITH topic_flags AS (
                SELECT
                    work_id,
                    bool_or(corpus_membership = 'strict') AS has_strict_topic,
                    bool_or(corpus_membership IN ('strict', 'broad_only')) AS has_broad_topic,
                    bool_or(corpus_membership = 'uncertain') AS has_uncertain_topic,
                    list_sort(list_distinct(list(method_family) FILTER (
                        WHERE method_family IS NOT NULL
                          AND corpus_membership IN ('strict', 'broad_only', 'uncertain')
                    ))) AS method_families
                FROM read_parquet(?)
                GROUP BY work_id
            ),
            joined AS (
                SELECT
                    w.work_id,
                    w.title,
                    w.doi,
                    w.publication_year,
                    w.publication_date,
                    w.work_type,
                    w.primary_topic_id,
                    w.primary_topic_name,
                    w.is_retracted,
                    w.is_paratext,
                    coalesce(t.has_strict_topic, false) AS has_strict_topic,
                    coalesce(t.has_broad_topic, false) AS has_broad_topic,
                    coalesce(t.has_uncertain_topic, false) AS has_uncertain_topic,
                    coalesce(t.method_families, []::VARCHAR[]) AS method_families,
                    coalesce(p.primary_included, false) AS work_type_primary,
                    coalesce(p.preprint_included, false) AS work_type_preprint_sensitivity,
                    coalesce(p.expanded_included, false) AS work_type_expanded_sensitivity,
                    v.version_family_id,
                    v.primary_representative_work_id,
                    v.is_recommended_primary_representative,
                    v.ambiguous_possible_family
                FROM read_parquet(?) w
                LEFT JOIN topic_flags t USING (work_id)
                LEFT JOIN work_type_policy p USING (work_type)
                INNER JOIN read_parquet(?) v USING (work_id)
            )
            SELECT
                *,
                has_strict_topic
                    AND work_type_primary
                    AND NOT is_retracted
                    AND NOT is_paratext
                    AND is_recommended_primary_representative AS strict_primary,
                has_broad_topic
                    AND work_type_primary
                    AND NOT is_retracted
                    AND NOT is_paratext
                    AND is_recommended_primary_representative AS broad_primary,
                has_strict_topic
                    AND work_type_preprint_sensitivity
                    AND NOT is_retracted
                    AND NOT is_paratext
                    AND is_recommended_primary_representative AS strict_preprint_sensitivity,
                has_broad_topic
                    AND work_type_preprint_sensitivity
                    AND NOT is_retracted
                    AND NOT is_paratext
                    AND is_recommended_primary_representative AS broad_preprint_sensitivity,
                has_strict_topic
                    AND work_type_expanded_sensitivity
                    AND NOT is_retracted
                    AND NOT is_paratext
                    AND is_recommended_primary_representative AS strict_expanded_sensitivity,
                has_broad_topic
                    AND work_type_expanded_sensitivity
                    AND NOT is_retracted
                    AND NOT is_paratext
                    AND is_recommended_primary_representative AS broad_expanded_sensitivity,
                has_strict_topic
                    AND work_type_primary
                    AND NOT is_retracted
                    AND NOT is_paratext AS strict_all_versions_sensitivity,
                has_broad_topic
                    AND work_type_primary
                    AND NOT is_retracted
                    AND NOT is_paratext AS broad_all_versions_sensitivity,
                has_uncertain_topic
                    AND work_type_primary
                    AND NOT is_retracted
                    AND NOT is_paratext
                    AND is_recommended_primary_representative
                    AS uncertain_topic_sensitivity,
                list_filter([
                    CASE WHEN NOT has_strict_topic THEN 'no_strict_topic' END,
                    CASE WHEN NOT work_type_primary THEN 'work_type_not_primary' END,
                    CASE WHEN is_retracted THEN 'retracted' END,
                    CASE WHEN is_paratext THEN 'paratext' END,
                    CASE WHEN NOT is_recommended_primary_representative
                         THEN 'non_representative_exact_doi_duplicate' END
                ], value -> value IS NOT NULL) AS strict_exclusion_reasons,
                list_filter([
                    CASE WHEN NOT has_broad_topic THEN 'no_broad_topic' END,
                    CASE WHEN NOT work_type_primary THEN 'work_type_not_primary' END,
                    CASE WHEN is_retracted THEN 'retracted' END,
                    CASE WHEN is_paratext THEN 'paratext' END,
                    CASE WHEN NOT is_recommended_primary_representative
                         THEN 'non_representative_exact_doi_duplicate' END
                ], value -> value IS NOT NULL) AS broad_exclusion_reasons
            FROM joined
            ORDER BY work_id
            """,
            [str(work_topics_path), str(works_path), str(version_diagnostics_path)],
        )
        _copy(connection, "SELECT * FROM work_corpus", temporary[outputs[0]])
        _copy(
            connection,
            """
            WITH flags AS (
                SELECT publication_year, unnest([
                    {'corpus_variant': 'strict_primary', 'included': strict_primary},
                    {'corpus_variant': 'broad_primary', 'included': broad_primary},
                    {'corpus_variant': 'strict_preprint_sensitivity',
                     'included': strict_preprint_sensitivity},
                    {'corpus_variant': 'broad_preprint_sensitivity',
                     'included': broad_preprint_sensitivity},
                    {'corpus_variant': 'strict_expanded_sensitivity',
                     'included': strict_expanded_sensitivity},
                    {'corpus_variant': 'broad_expanded_sensitivity',
                     'included': broad_expanded_sensitivity},
                    {'corpus_variant': 'strict_all_versions_sensitivity',
                     'included': strict_all_versions_sensitivity},
                    {'corpus_variant': 'broad_all_versions_sensitivity',
                     'included': broad_all_versions_sensitivity},
                    {'corpus_variant': 'uncertain_topic_sensitivity',
                     'included': uncertain_topic_sensitivity}
                ]) AS flag
                FROM work_corpus
            )
            SELECT publication_year, flag.corpus_variant AS corpus_variant, count(*) AS work_count
            FROM flags
            WHERE flag.included
            GROUP BY publication_year, flag.corpus_variant
            ORDER BY publication_year, flag.corpus_variant
            """,
            temporary[outputs[1]],
        )
        _copy(
            connection,
            """
            WITH included AS (
                SELECT work_id, publication_year, 'strict_primary' AS corpus_variant
                FROM work_corpus WHERE strict_primary
                UNION ALL
                SELECT work_id, publication_year, 'broad_primary' AS corpus_variant
                FROM work_corpus WHERE broad_primary
            ), eligible_topics AS (
                SELECT DISTINCT work_id, method_family, corpus_membership
                FROM read_parquet(?)
                WHERE method_family IS NOT NULL
            )
            SELECT
                included.publication_year,
                included.corpus_variant,
                eligible_topics.method_family,
                count(DISTINCT included.work_id) AS work_count
            FROM included
            JOIN eligible_topics USING (work_id)
            WHERE (included.corpus_variant = 'strict_primary'
                   AND eligible_topics.corpus_membership = 'strict')
               OR (included.corpus_variant = 'broad_primary'
                   AND eligible_topics.corpus_membership IN ('strict', 'broad_only'))
            GROUP BY included.publication_year, included.corpus_variant,
                     eligible_topics.method_family
            ORDER BY included.publication_year, included.corpus_variant,
                     eligible_topics.method_family
            """,
            temporary[outputs[2]],
            [str(work_topics_path)],
        )
    except BaseException:
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    definitions = {
        outputs[0]: (["work_id"], {"work_id", "strict_primary", "broad_primary"}, None),
        outputs[1]: (
            ["publication_year", "corpus_variant"],
            {"publication_year", "corpus_variant", "work_count"},
            "publication_year",
        ),
        outputs[2]: (
            ["publication_year", "corpus_variant", "method_family"],
            {"publication_year", "corpus_variant", "method_family", "work_count"},
            "publication_year",
        ),
    }
    for destination, path in temporary.items():
        key, required, year = definitions[destination]
        parquet_metrics(path, primary_key=key, required_columns=required, year_column=year)
    for destination, path in temporary.items():
        os.replace(path, destination)
    metrics = parquet_metrics(
        outputs[0],
        primary_key=["work_id"],
        required_columns={"work_id", "strict_primary", "broad_primary"},
        year_column="publication_year",
    )
    source_metrics = parquet_metrics(
        inputs[0], primary_key=["work_id"], required_columns={"work_id"}
    )
    if metrics["row_count"] != source_metrics["row_count"]:
        raise ValueError("corpus rows do not reconcile with normalized Works")
    summary_connection = duckdb.connect()
    try:
        values = summary_connection.execute(
            """
            SELECT
                count(*) FILTER (WHERE strict_primary),
                count(*) FILTER (WHERE broad_primary),
                count(*) FILTER (WHERE strict_primary AND NOT broad_primary),
                count(*) FILTER (WHERE NOT strict_primary AND len(strict_exclusion_reasons) = 0),
                count(*) FILTER (WHERE NOT broad_primary AND len(broad_exclusion_reasons) = 0),
                count(*) FILTER (WHERE strict_preprint_sensitivity),
                count(*) FILTER (WHERE broad_preprint_sensitivity),
                count(*) FILTER (WHERE strict_expanded_sensitivity),
                count(*) FILTER (WHERE broad_expanded_sensitivity)
            FROM read_parquet(?)
            """,
            [str(outputs[0])],
        ).fetchone()
    finally:
        summary_connection.close()
    if values is None or int(values[2]) != 0 or int(values[3]) != 0 or int(values[4]) != 0:
        raise ValueError("corpus subset or exclusion-reason invariant failed")
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "input_hashes": [file_sha256(path) for path in inputs],
                "work_type_policy": policy.model_dump(mode="json"),
            }
        ),
        "work_count": int(metrics["row_count"]),
        "strict_primary_count": int(values[0]),
        "broad_primary_count": int(values[1]),
        "strict_not_broad_error_count": int(values[2]),
        "strict_missing_exclusion_reason_count": int(values[3]),
        "broad_missing_exclusion_reason_count": int(values[4]),
        "strict_preprint_sensitivity_count": int(values[5]),
        "broad_preprint_sensitivity_count": int(values[6]),
        "strict_expanded_sensitivity_count": int(values[7]),
        "broad_expanded_sensitivity_count": int(values[8]),
        "outputs": {
            "work_corpus": str(outputs[0]),
            "corpus_annual_counts": str(outputs[1]),
            "corpus_topic_family_counts": str(outputs[2]),
        },
        "generated_at_utc": _timestamp(),
    }


def _copy(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    output: Path,
    parameters: list[str] | None = None,
) -> None:
    destination = str(output).replace("'", "''")
    connection.execute(
        f"COPY ({query}) TO '{destination}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)",
        parameters or [],
    )


def write_corpus_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    topic_registry_path: str | Path,
    work_type_path: str | Path,
    command: str,
) -> None:
    config_hashes = {
        "project": config_file_hash(project_config_path),
        "topic_registry": config_file_hash(topic_registry_path),
        "work_types": config_file_hash(work_type_path),
    }
    source_manifests = [
        ".agent/manifests/works.json",
        ".agent/manifests/work_topics.json",
        ".agent/manifests/work_version_diagnostics.json",
    ]
    source_versions = {"corpus_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="work_corpus_summary",
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
        "work_corpus": (
            ["work_id"],
            {"work_id", "strict_primary", "broad_primary"},
            "publication_year",
        ),
        "corpus_annual_counts": (
            ["publication_year", "corpus_variant"],
            {"publication_year", "corpus_variant", "work_count"},
            "publication_year",
        ),
        "corpus_topic_family_counts": (
            ["publication_year", "corpus_variant", "method_family"],
            {"publication_year", "corpus_variant", "method_family", "work_count"},
            "publication_year",
        ),
    }
    for dataset_name, path in summary["outputs"].items():
        primary_key, required, year = definitions[dataset_name]
        write_parquet_manifest(
            path=path,
            dataset_name=dataset_name,
            primary_key=primary_key,
            required_columns=required,
            year_column=year,
            run_id=run_id,
            config_hashes=config_hashes,
            source_manifests=source_manifests,
            source_versions=source_versions,
            command=command,
        )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
