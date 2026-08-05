"""Reproducible audit samples for top institutions and cross-region edges."""

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

_STAGE_VERSION = "top-entity-audit-2026-08-05-v1"


def build_top_entity_audit(
    nodes_path: str | Path,
    edges_metrics_path: str | Path,
    work_institutions_path: str | Path,
    institutions_path: str | Path,
    hierarchy_path: str | Path,
    *,
    institution_output_path: str | Path,
    edge_output_path: str | Path,
    sample_size: int = 50,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build deterministic top-institution and top-cross-region edge audits."""
    sources = [
        Path(nodes_path),
        Path(edges_metrics_path),
        Path(work_institutions_path),
        Path(institutions_path),
        Path(hierarchy_path),
    ]
    for path in sources:
        if not path.is_file():
            raise ValueError(f"audit input does not exist: {path}")
    institution_output = Path(institution_output_path)
    edge_output = Path(edge_output_path)
    for path in (institution_output, edge_output):
        path.parent.mkdir(parents=True, exist_ok=True)
    institution_temporary = institution_output.with_suffix(".parquet.tmp")
    edge_temporary = edge_output.with_suffix(".parquet.tmp")
    institution_temporary.unlink(missing_ok=True)
    edge_temporary.unlink(missing_ok=True)

    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        connection.execute(
            f"""
            COPY (
                WITH aggregate_nodes AS (
                    SELECT
                        institution_id,
                        max_by(display_name, year) AS display_name,
                        max_by(country_code, year) AS country_code,
                        max_by(macro_region, year) AS macro_region,
                        max_by(institution_category, year) AS institution_category,
                        sum(work_count)::BIGINT AS total_work_count,
                        max(pagerank) AS maximum_pagerank,
                        max(betweenness) AS maximum_betweenness,
                        count(DISTINCT display_name)::INTEGER AS observed_name_count,
                        count(DISTINCT country_code)::INTEGER AS observed_country_count
                    FROM read_parquet(?)
                    WHERE corpus_view = 'broad' AND hierarchy_view = 'organization'
                    GROUP BY institution_id
                ), ranked AS (
                    SELECT *,
                        row_number() OVER (
                            ORDER BY total_work_count DESC, institution_id
                        )::INTEGER AS output_rank,
                        row_number() OVER (
                            ORDER BY maximum_pagerank DESC, institution_id
                        )::INTEGER AS pagerank_rank
                    FROM aggregate_nodes
                ), sampled AS (
                    SELECT * FROM ranked
                    WHERE output_rank <= ? OR pagerank_rank <= ?
                ), affiliation_samples AS (
                    SELECT
                        institution_id,
                        list_slice(list_sort(list_distinct(list(work_id))), 1, 10)
                            AS work_ids_sample,
                        list_slice(
                            list_sort(list_distinct(flatten(list(raw_affiliation_strings)))), 1, 10
                        ) AS raw_affiliation_samples
                    FROM read_parquet(?)
                    WHERE institution_id IN (SELECT institution_id FROM sampled)
                    GROUP BY institution_id
                )
                SELECT
                    sampled.*,
                    institutions.ror_id,
                    institutions.canonical_institution_id,
                    institutions.canonicalization_rule_id,
                    institutions.metadata_source,
                    hierarchy.is_collapsed,
                    hierarchy.canonicalization_rule_ids,
                    hierarchy.canonicalization_reasons,
                    hierarchy.canonicalization_provenance,
                    affiliation_samples.work_ids_sample,
                    affiliation_samples.raw_affiliation_samples,
                    list_filter([
                        CASE WHEN sampled.country_code IS NULL OR sampled.country_code = ''
                            THEN 'missing_country' END,
                        CASE WHEN sampled.institution_category IS NULL
                                  OR sampled.institution_category = 'unknown'
                            THEN 'unknown_type' END,
                        CASE WHEN sampled.observed_name_count > 1 THEN 'name_changed' END,
                        CASE WHEN sampled.observed_country_count > 1 THEN 'country_changed' END,
                        CASE WHEN affiliation_samples.institution_id IS NULL
                            THEN 'missing_affiliation_sample' END
                    ], value -> value IS NOT NULL) AS suspicious_flags,
                    'config/institution_overrides.csv' AS correction_route,
                    false AS correction_applied_automatically
                FROM sampled
                LEFT JOIN read_parquet(?) institutions USING (institution_id)
                LEFT JOIN read_parquet(?) hierarchy
                    ON hierarchy.institution_id = sampled.institution_id
                   AND hierarchy.hierarchy_view = 'organization'
                LEFT JOIN affiliation_samples USING (institution_id)
                ORDER BY least(output_rank, pagerank_rank), institution_id
            ) TO '{_literal(institution_temporary)}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [
                str(sources[0]),
                sample_size,
                sample_size,
                str(sources[2]),
                str(sources[3]),
                str(sources[4]),
            ],
        )
        edge_rows = (
            connection.execute(
                """
            WITH aggregated AS (
                SELECT
                    source_id,
                    target_id,
                    max_by(source_name, year) AS source_name,
                    max_by(target_name, year) AS target_name,
                    max_by(source_region, year) AS source_region,
                    max_by(target_region, year) AS target_region,
                    max_by(source_country, year) AS source_country,
                    max_by(target_country, year) AS target_country,
                    sum(full_count)::BIGINT AS total_full_count,
                    sum(fractional_count) AS total_fractional_count,
                    max(normalized_intensity) AS maximum_normalized_intensity,
                    max(persistence_5y) AS maximum_persistence_5y,
                    list_slice(
                        list_sort(list_distinct(flatten(list(work_ids_sample)))), 1, 10
                    ) AS work_ids_sample
                FROM read_parquet(?)
                WHERE corpus_view = 'broad'
                  AND hierarchy_view = 'organization'
                  AND source_region != target_region
                GROUP BY source_id, target_id
            )
            SELECT * FROM aggregated
            ORDER BY total_fractional_count DESC, source_id, target_id
            LIMIT ?
            """,
                [str(sources[1]), sample_size],
            )
            .to_arrow_table()
            .to_pylist()
        )
        for row in edge_rows:
            work_ids = [str(value) for value in row["work_ids_sample"]]
            for side in ("source", "target"):
                institution_id = str(row[f"{side}_id"])
                affiliations = connection.execute(
                    """
                    SELECT list_slice(
                        list_sort(list_distinct(flatten(list(raw_affiliation_strings)))), 1, 10
                    )
                    FROM read_parquet(?)
                    WHERE institution_id = ? AND work_id IN (SELECT unnest(?::VARCHAR[]))
                    """,
                    [str(sources[2]), institution_id, work_ids],
                ).fetchone()
                row[f"{side}_raw_affiliation_samples"] = (
                    affiliations[0] if affiliations and affiliations[0] else []
                )
                canonical = connection.execute(
                    """
                    SELECT canonical_institution_id, canonicalization_rule_ids,
                           canonicalization_reasons, canonicalization_provenance
                    FROM read_parquet(?)
                    WHERE hierarchy_view = 'organization' AND institution_id = ?
                    """,
                    [str(sources[4]), institution_id],
                ).fetchone()
                row[f"{side}_canonical_institution_id"] = canonical[0] if canonical else None
                row[f"{side}_canonicalization_rule_ids"] = canonical[1] if canonical else []
                row[f"{side}_canonicalization_reasons"] = canonical[2] if canonical else []
                row[f"{side}_canonicalization_provenance"] = canonical[3] if canonical else None
            flags = []
            if row["source_country"] in (None, "") or row["target_country"] in (None, ""):
                flags.append("missing_country")
            if row["source_region"] == row["target_region"]:
                flags.append("not_cross_region")
            if not row["source_raw_affiliation_samples"]:
                flags.append("missing_source_affiliation_sample")
            if not row["target_raw_affiliation_samples"]:
                flags.append("missing_target_affiliation_sample")
            row["suspicious_flags"] = flags
            row["correction_route"] = "config/institution_overrides.csv"
            row["correction_applied_automatically"] = False
        pq.write_table(pa.Table.from_pylist(edge_rows), edge_temporary, compression="zstd")
    except BaseException:
        institution_temporary.unlink(missing_ok=True)
        edge_temporary.unlink(missing_ok=True)
        raise
    finally:
        connection.close()

    institution_metrics = parquet_metrics(
        institution_temporary,
        primary_key=["institution_id"],
        required_columns={
            "institution_id",
            "output_rank",
            "pagerank_rank",
            "work_ids_sample",
            "suspicious_flags",
        },
    )
    edge_metrics = parquet_metrics(
        edge_temporary,
        primary_key=["source_id", "target_id"],
        required_columns={
            "source_id",
            "target_id",
            "total_fractional_count",
            "work_ids_sample",
            "suspicious_flags",
        },
    )
    validation = duckdb.connect()
    try:
        validation_flags = validation.execute(
            """
            SELECT
                (SELECT count(*) FROM read_parquet(?) WHERE len(suspicious_flags) > 0),
                (SELECT count(*) FROM read_parquet(?) WHERE len(suspicious_flags) > 0),
                (SELECT count(*) FROM read_parquet(?) WHERE correction_applied_automatically),
                (SELECT count(*) FROM read_parquet(?) WHERE correction_applied_automatically)
            """,
            [
                str(institution_temporary),
                str(edge_temporary),
                str(institution_temporary),
                str(edge_temporary),
            ],
        ).fetchone()
    finally:
        validation.close()
    if validation_flags is None or int(validation_flags[2]) or int(validation_flags[3]):
        raise ValueError("audit correction provenance invariant failed")
    os.replace(institution_temporary, institution_output)
    os.replace(edge_temporary, edge_output)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "source_sha256": [file_sha256(path) for path in sources],
                "sample_size": sample_size,
            }
        ),
        "institution_audit_row_count": int(institution_metrics["row_count"]),
        "edge_audit_row_count": int(edge_metrics["row_count"]),
        "flagged_institution_count": int(validation_flags[0]),
        "flagged_edge_count": int(validation_flags[1]),
        "automatic_correction_count": 0,
        "correction_route": "config/institution_overrides.csv",
        "outputs": {
            "top_institution_audit": str(institution_output),
            "top_edge_audit": str(edge_output),
        },
        "generated_at_utc": _timestamp(),
    }


def write_audit_artifacts(
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
        ".agent/manifests/institution_hierarchy.json",
    ]
    source_versions = {"top_entity_audit_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="top_entity_audit_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions=source_versions,
        source_manifests=source_manifests,
        command=command,
    )
    for dataset_name, primary_key in (
        ("top_institution_audit", ["institution_id"]),
        ("top_edge_audit", ["source_id", "target_id"]),
    ):
        write_parquet_manifest(
            path=summary["outputs"][dataset_name],
            dataset_name=dataset_name,
            primary_key=primary_key,
            required_columns={*primary_key, "work_ids_sample", "suspicious_flags"},
            year_column=None,
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
