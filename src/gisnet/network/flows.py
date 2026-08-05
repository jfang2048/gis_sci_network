"""Aggregate undirected institution pairs into geographic flows."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "geographic-flows-2026-08-05-v1"


def build_geographic_flows(
    work_edges_path: str | Path,
    *,
    flows_path: str | Path,
    reconciliation_path: str | Path,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build canonical macro-region, subregion, and country flow pairs."""
    source = Path(work_edges_path)
    if not source.is_file():
        raise ValueError(f"Work edges do not exist: {source}")
    outputs = [Path(flows_path), Path(reconciliation_path)]
    for path in outputs:
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary = {path: path.with_suffix(".parquet.tmp") for path in outputs}
    for path in temporary.values():
        path.unlink(missing_ok=True)
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        connection.execute("SET preserve_insertion_order = false")
        connection.execute("SET temp_directory = 'data/interim/duckdb-flows'")
        connection.execute(
            f"""
            COPY (
                WITH expanded AS (
                    SELECT *, unnest([
                        {{'geographic_level': 'macro_region',
                          'source_geography': source_region,
                          'target_geography': target_region}},
                        {{'geographic_level': 'subregion',
                          'source_geography': source_subregion,
                          'target_geography': target_subregion}},
                        {{'geographic_level': 'country',
                          'source_geography': source_country,
                          'target_geography': target_country}}
                    ]) AS geography
                    FROM read_parquet(?)
                ), canonical AS (
                    SELECT
                        year, corpus_view, hierarchy_view, work_id, source_id, target_id,
                        full_weight, fractional_weight,
                        geography.geographic_level AS geographic_level,
                        least(geography.source_geography, geography.target_geography)
                            AS source_geography,
                        greatest(geography.source_geography, geography.target_geography)
                            AS target_geography
                    FROM expanded
                ), aggregated AS (
                    SELECT
                        year,
                        corpus_view,
                        hierarchy_view,
                        geographic_level,
                        source_geography,
                        target_geography,
                        sum(full_weight)::BIGINT AS full_count,
                        sum(fractional_weight) AS fractional_count,
                        count(DISTINCT work_id)::BIGINT AS distinct_work_count,
                        count(DISTINCT (source_id, target_id))::BIGINT
                            AS distinct_institution_pair_count,
                        list_slice(list_sort(list_distinct(list(work_id))), 1, 10)
                            AS work_ids_sample
                    FROM canonical
                    GROUP BY year, corpus_view, hierarchy_view, geographic_level,
                             source_geography, target_geography
                )
                SELECT
                    *,
                    fractional_count / sum(fractional_count) OVER (
                        PARTITION BY year, corpus_view, hierarchy_view, geographic_level
                    ) AS normalized_share
                FROM aggregated
                ORDER BY year, corpus_view, hierarchy_view, geographic_level,
                         source_geography, target_geography
            ) TO '{_literal(temporary[outputs[0]])}'
            (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
            """,
            [str(source)],
        )
        connection.execute(
            f"""
            COPY (
                WITH expected AS (
                    SELECT
                        year, corpus_view, hierarchy_view,
                        sum(full_weight)::BIGINT AS expected_full_count,
                        sum(fractional_weight) AS expected_fractional_count
                    FROM read_parquet(?)
                    GROUP BY year, corpus_view, hierarchy_view
                ), actual AS (
                    SELECT
                        year, corpus_view, hierarchy_view, geographic_level,
                        sum(full_count)::BIGINT AS flow_full_count,
                        sum(fractional_count) AS flow_fractional_count,
                        sum(normalized_share) AS normalized_share_sum
                    FROM read_parquet(?)
                    GROUP BY year, corpus_view, hierarchy_view, geographic_level
                )
                SELECT
                    actual.*,
                    expected.expected_full_count,
                    expected.expected_fractional_count,
                    actual.flow_full_count - expected.expected_full_count AS full_count_difference,
                    actual.flow_fractional_count - expected.expected_fractional_count
                        AS fractional_count_difference,
                    actual.normalized_share_sum - 1.0 AS normalized_share_difference
                FROM actual
                INNER JOIN expected USING (year, corpus_view, hierarchy_view)
                ORDER BY year, corpus_view, hierarchy_view, geographic_level
            ) TO '{_literal(temporary[outputs[1]])}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [str(source), str(temporary[outputs[0]])],
        )
    except BaseException:
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    definitions = {
        outputs[0]: (
            [
                "year",
                "corpus_view",
                "hierarchy_view",
                "geographic_level",
                "source_geography",
                "target_geography",
            ],
            {"year", "geographic_level", "full_count", "fractional_count", "normalized_share"},
        ),
        outputs[1]: (
            ["year", "corpus_view", "hierarchy_view", "geographic_level"],
            {"year", "geographic_level", "full_count_difference", "fractional_count_difference"},
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
                max(abs(full_count_difference)),
                max(abs(fractional_count_difference)),
                max(abs(normalized_share_difference))
            FROM read_parquet(?)
            """,
            [str(temporary[outputs[1]])],
        ).fetchone()
        properties = validation.execute(
            """
            SELECT
                count(*) FILTER (WHERE source_geography > target_geography),
                count(*),
                count(*) FILTER (WHERE geographic_level = 'macro_region')
            FROM read_parquet(?)
            """,
            [str(temporary[outputs[0]])],
        ).fetchone()
        country_counts = validation.execute(
            """
            WITH values AS (
                SELECT source_region AS region, source_country AS country FROM read_parquet(?)
                UNION
                SELECT target_region AS region, target_country AS country FROM read_parquet(?)
            )
            SELECT
                count(DISTINCT country) FILTER (WHERE region = 'Asia'),
                count(DISTINCT country) FILTER (WHERE region = 'Americas')
            FROM values
            """,
            [str(source), str(source)],
        ).fetchone()
    finally:
        validation.close()
    if invariant is None or properties is None or country_counts is None:
        raise ValueError("geographic flow validation query failed")
    if int(invariant[0]) or float(invariant[1]) > 1e-6 or float(invariant[2]) > 1e-10:
        raise ValueError("geographic flows do not reconcile with institution edges")
    if int(properties[0]) or int(country_counts[0]) <= 1 or int(country_counts[1]) <= 1:
        raise ValueError("geographic pair ordering or macro-region country coverage failed")
    for destination, path in temporary.items():
        os.replace(path, destination)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {"stage_version": _STAGE_VERSION, "work_edges_sha256": file_sha256(source)}
        ),
        "flow_row_count": int(properties[1]),
        "macro_region_flow_row_count": int(properties[2]),
        "asia_country_count": int(country_counts[0]),
        "americas_country_count": int(country_counts[1]),
        "maximum_fractional_reconciliation_error": float(invariant[1]),
        "maximum_normalized_share_error": float(invariant[2]),
        "outputs": {
            "region_flows_year": str(outputs[0]),
            "region_flow_reconciliation": str(outputs[1]),
        },
        "generated_at_utc": _timestamp(),
    }


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def write_flow_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [".agent/manifests/work_edges.json", ".agent/manifests/country_regions.json"]
    source_versions = {"geographic_flow_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="region_flows_summary",
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
        "region_flows_year": (
            [
                "year",
                "corpus_view",
                "hierarchy_view",
                "geographic_level",
                "source_geography",
                "target_geography",
            ],
            {"year", "geographic_level", "full_count", "fractional_count", "normalized_share"},
        ),
        "region_flow_reconciliation": (
            ["year", "corpus_view", "hierarchy_view", "geographic_level"],
            {"year", "geographic_level", "full_count_difference", "fractional_count_difference"},
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
