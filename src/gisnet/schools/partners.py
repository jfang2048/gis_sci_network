"""Compact latest-window per-school partner index and exact retrieval."""

from __future__ import annotations

import os
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "school-partner-index-2026-08-28-v1"
_SUPPORTED_WINDOWS = (12, 24, 36)
_REQUIRED_COLUMNS = {
    "window_start",
    "window_end",
    "window_months",
    "observed_month_count",
    "eligible_month_count",
    "coverage_ratio",
    "is_complete_window",
    "corpus_view",
    "hierarchy_view",
    "school_id",
    "school_name",
    "school_country",
    "school_macro_region",
    "school_subregion",
    "partner_id",
    "partner_name",
    "partner_country",
    "partner_macro_region",
    "partner_subregion",
    "full_count",
    "fractional_count",
    "distinct_work_count",
    "source_work_count",
    "target_work_count",
    "normalized_intensity",
    "active_month_count",
    "edge_persistence",
    "repeat_collaboration",
    "partner_rank",
    "support_status",
}


def build_school_partner_index(
    edge_intervals_path: str | Path,
    coverage_path: str | Path,
    institution_rolling_path: str | Path,
    school_identities_path: str | Path,
    school_index_path: str | Path,
    *,
    output_path: str | Path,
    corpus_views: tuple[str, ...] = ("strict", "broad"),
    window_months: tuple[int, ...] = _SUPPORTED_WINDOWS,
    top_k: int = 50,
    memory_limit: str = "4GB",
) -> dict[str, Any]:
    """Retain each school's strongest partners at the latest endpoint of each window."""
    identities_source = Path(school_identities_path)
    if not identities_source.is_file():
        raise ValueError(f"school partner input does not exist: {identities_source}")
    identities = pq.read_table(
        identities_source, columns=["institution_id", "canonical_school_id", "is_collapsed"]
    )
    if any(bool(value) for value in identities.column("is_collapsed").to_pylist()):
        raise ValueError(
            "school identities contain collapses; rebuild from Work memberships before using "
            "organization rolling facts"
        )
    if (
        identities.column("institution_id").to_pylist()
        != identities.column("canonical_school_id").to_pylist()
    ):
        raise ValueError(
            "school identities differ from organizations; rebuild from Work memberships"
        )
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    if not corpus_views or not set(corpus_views).issubset({"strict", "broad"}):
        raise ValueError("corpus_views must contain strict and/or broad")
    if not window_months or not set(window_months).issubset(set(_SUPPORTED_WINDOWS)):
        raise ValueError("window_months must contain only 12, 24, or 36")

    intervals_source = Path(edge_intervals_path)
    coverage_source = Path(coverage_path)
    rolling_source = Path(institution_rolling_path)
    schools_source = Path(school_index_path)
    for source in (intervals_source, coverage_source, rolling_source, schools_source):
        if not source.is_file():
            raise ValueError(f"school partner input does not exist: {source}")
    edge_month_source = _validated_edge_month_source(coverage_source)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".parquet.tmp")
    temporary.unlink(missing_ok=True)
    shard_paths: list[Path] = []
    build_started = time.perf_counter()
    connection = duckdb.connect()
    try:
        connection.execute(f"SET memory_limit='{_sql_string(memory_limit)}'")
        connection.execute("SET threads=1")
        for corpus in corpus_views:
            for width in window_months:
                coverage_row = connection.execute(
                    """
                    SELECT window_start, window_end, observed_month_count,
                           eligible_month_count, coverage_ratio, is_complete_window,
                           observation_start_month, observation_end_month
                    FROM read_parquet(?)
                    WHERE corpus_view = ? AND hierarchy_view = 'organization'
                      AND window_months = ?
                    ORDER BY window_end DESC
                    LIMIT 1
                    """,
                    [str(coverage_source), corpus, width],
                ).fetchone()
                if coverage_row is None:
                    raise ValueError(f"missing latest coverage for {corpus} rolling {width}m")
                shard = destination.parent / f".{destination.stem}-{corpus}-{width}.parquet.tmp"
                shard.unlink(missing_ok=True)
                _write_partner_shard(
                    connection,
                    shard,
                    intervals_source,
                    edge_month_source,
                    rolling_source,
                    schools_source,
                    corpus=corpus,
                    width=width,
                    coverage_row=coverage_row,
                    top_k=top_k,
                )
                shard_paths.append(shard)
        paths = ", ".join(f"'{_literal(path)}'" for path in shard_paths)
        connection.execute(
            f"""
            COPY (
                SELECT * FROM read_parquet([{paths}])
                ORDER BY corpus_view, window_months, school_id, partner_rank, partner_id
            ) TO '{_literal(temporary)}' (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
        for shard in shard_paths:
            shard.unlink(missing_ok=True)

    parquet_metrics(
        temporary,
        primary_key=[
            "window_end",
            "window_months",
            "corpus_view",
            "school_id",
            "partner_id",
        ],
        required_columns=_REQUIRED_COLUMNS,
    )
    os.replace(temporary, destination)
    metrics = parquet_metrics(
        destination,
        primary_key=[
            "window_end",
            "window_months",
            "corpus_view",
            "school_id",
            "partner_id",
        ],
        required_columns=_REQUIRED_COLUMNS,
    )
    benchmark = _benchmark_queries(destination)
    build_seconds = time.perf_counter() - build_started
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "identity_policy": (
            "The current school mapping is byte-equivalent to organization identity. Any future "
            "collapse invalidates this index and requires a Work-membership rebuild."
        ),
        "retention_policy": (
            f"Top {top_k} partners by exact fractional collaboration strength for every eligible "
            "school, corpus, and supported rolling width at its latest available endpoint."
        ),
        "historical_query_policy": (
            "Historical endpoints remain recoverable from the exact interval/month source; they "
            "are not expanded into a global Cartesian snapshot."
        ),
        "top_k": top_k,
        "corpus_views": list(corpus_views),
        "window_months": list(window_months),
        "directed_partner_row_count": metrics["row_count"],
        "school_count": _distinct_count(destination, "school_id"),
        "outside_prior_global_edge_core_count": _outside_edge_core_count(destination),
        "output_size_bytes": destination.stat().st_size,
        "build_seconds": build_seconds,
        "query_benchmark": benchmark,
        "checksum_sha256": metrics["checksum_sha256"],
        "output": str(destination),
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "edge_intervals_sha256": file_sha256(intervals_source),
                "edge_month_sha256": file_sha256(edge_month_source),
                "rolling_outputs_sha256": file_sha256(rolling_source),
                "school_identities_sha256": file_sha256(identities_source),
                "school_index_sha256": file_sha256(schools_source),
                "corpus_views": list(corpus_views),
                "window_months": list(window_months),
                "top_k": top_k,
            }
        ),
        "generated_at_utc": _timestamp(),
    }


def query_school_partners(
    partner_index_path: str | Path,
    *,
    school_id: str,
    corpus_view: str,
    window_months: int,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return retained exact partner rows for one stable canonical school ID."""
    if corpus_view not in {"strict", "broad"}:
        raise ValueError("invalid corpus_view")
    if window_months not in _SUPPORTED_WINDOWS:
        raise ValueError("window_months must be 12, 24, or 36")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    path = Path(partner_index_path)
    if not path.is_file():
        raise ValueError(f"school partner index does not exist: {path}")
    limit_clause = "" if limit is None else f" LIMIT {int(limit)}"
    connection = duckdb.connect()
    try:
        cursor = connection.execute(
            f"""
            SELECT *
            FROM read_parquet(?)
            WHERE school_id = ? AND corpus_view = ? AND window_months = ?
            ORDER BY partner_rank, partner_id{limit_clause}
            """,
            [str(path), school_id, corpus_view, window_months],
        )
        columns = [item[0] for item in cursor.description]
        rows = cursor.fetchall()
    finally:
        connection.close()
    return [dict(zip(columns, row, strict=True)) for row in rows]


def write_school_partner_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    school_decision_path: str | Path,
    command: str,
) -> None:
    """Write summary and manifest for the compact partner index."""
    config_hashes = {
        "project": config_file_hash(project_config_path),
        "school_decision": config_file_hash(school_decision_path),
    }
    source_manifests = [
        ".agent/manifests/school_index.json",
        ".agent/manifests/school_identities.json",
        ".agent/manifests/collaboration_edge_window_intervals.json",
        ".agent/manifests/institution_outputs_rolling.json",
    ]
    source_versions = {"school_partner_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="school_partner_index_summary",
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
        path=summary["output"],
        dataset_name="school_partner_index",
        primary_key=[
            "window_end",
            "window_months",
            "corpus_view",
            "school_id",
            "partner_id",
        ],
        required_columns=_REQUIRED_COLUMNS,
        year_column=None,
        run_id=run_id,
        config_hashes=config_hashes,
        source_manifests=source_manifests,
        source_versions=source_versions,
        command=command,
    )


def _write_partner_shard(
    connection: duckdb.DuckDBPyConnection,
    output: Path,
    intervals: Path,
    edge_month: Path,
    rolling_outputs: Path,
    schools: Path,
    *,
    corpus: str,
    width: int,
    coverage_row: tuple[Any, ...],
    top_k: int,
) -> None:
    window_start = str(coverage_row[0])
    window_end = str(coverage_row[1])
    observed_month_count = int(coverage_row[2])
    eligible_month_count = int(coverage_row[3])
    coverage_ratio = float(coverage_row[4])
    is_complete_window = str(bool(coverage_row[5])).lower()
    effective_start = max(window_start, str(coverage_row[6]))
    effective_end = min(window_end, str(coverage_row[7]))
    query = f"""
        WITH active AS (
            SELECT source_id, target_id
            FROM read_parquet('{_literal(intervals)}')
            WHERE corpus_view = '{_sql_string(corpus)}'
              AND hierarchy_view = 'organization'
              AND window_months = {width}
              AND valid_from_window_end <= '{_sql_string(window_end)}'
              AND valid_through_window_end >= '{_sql_string(window_end)}'
        ), rolled AS (
            SELECT
                e.source_id,
                e.target_id,
                sum(e.full_count)::BIGINT AS full_count,
                sum(e.fractional_count) AS fractional_count,
                sum(e.distinct_work_count)::BIGINT AS distinct_work_count,
                count(*)::BIGINT AS active_month_count
            FROM read_parquet('{_literal(edge_month)}') e
            JOIN active USING (source_id, target_id)
            WHERE e.corpus_view = '{_sql_string(corpus)}'
              AND e.hierarchy_view = 'organization'
              AND e.publication_month BETWEEN '{_sql_string(effective_start)}'
                                          AND '{_sql_string(effective_end)}'
            GROUP BY e.source_id, e.target_id
        ), output AS (
            SELECT institution_id, work_count
            FROM read_parquet('{_literal(rolling_outputs)}')
            WHERE corpus_view = '{_sql_string(corpus)}'
              AND hierarchy_view = 'organization'
              AND window_months = {width}
              AND window_end = '{_sql_string(window_end)}'
        ), undirected AS (
            SELECT r.*, s.work_count AS source_work_count,
                   t.work_count AS target_work_count
            FROM rolled r
            JOIN output s ON r.source_id = s.institution_id
            JOIN output t ON r.target_id = t.institution_id
        ), directed AS (
            SELECT source_id AS school_id, target_id AS partner_id, * EXCLUDE(source_id,target_id)
            FROM undirected
            UNION ALL
            SELECT target_id AS school_id, source_id AS partner_id, full_count,
                   fractional_count, distinct_work_count, active_month_count,
                   target_work_count AS source_work_count,
                   source_work_count AS target_work_count
            FROM undirected
        ), labelled AS (
            SELECT
                d.*,
                s.display_name AS school_name,
                s.country_code AS school_country,
                s.macro_region AS school_macro_region,
                s.subregion AS school_subregion,
                p.display_name AS partner_name,
                p.country_code AS partner_country,
                p.macro_region AS partner_macro_region,
                p.subregion AS partner_subregion
            FROM directed d
            JOIN read_parquet('{_literal(schools)}') s
              ON d.school_id = s.canonical_school_id
            JOIN read_parquet('{_literal(schools)}') p
              ON d.partner_id = p.canonical_school_id
        ), ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY school_id
                ORDER BY fractional_count DESC, full_count DESC, partner_id
            )::INTEGER AS partner_rank
            FROM labelled
        )
        SELECT
            '{_sql_string(window_start)}' AS window_start,
            '{_sql_string(window_end)}' AS window_end,
            {width}::INTEGER AS window_months,
            {observed_month_count}::INTEGER AS observed_month_count,
            {eligible_month_count}::INTEGER AS eligible_month_count,
            {coverage_ratio}::DOUBLE AS coverage_ratio,
            {is_complete_window}::BOOLEAN AS is_complete_window,
            '{_sql_string(corpus)}' AS corpus_view,
            'school' AS hierarchy_view,
            school_id,
            school_name,
            school_country,
            school_macro_region,
            school_subregion,
            partner_id,
            partner_name,
            partner_country,
            partner_macro_region,
            partner_subregion,
            full_count,
            fractional_count,
            distinct_work_count,
            source_work_count,
            target_work_count,
            fractional_count / sqrt(source_work_count * target_work_count)
                AS normalized_intensity,
            active_month_count,
            active_month_count::DOUBLE / {width} AS edge_persistence,
            active_month_count >= 2 AS repeat_collaboration,
            partner_rank,
            'supported' AS support_status
        FROM ranked
        WHERE partner_rank <= {top_k}
        ORDER BY school_id, partner_rank, partner_id
    """
    connection.execute(f"COPY ({query}) TO '{_literal(output)}' (FORMAT PARQUET, COMPRESSION ZSTD)")


def _validated_edge_month_source(coverage: Path) -> Path:
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """
            SELECT DISTINCT edge_month_source_path, edge_month_source_sha256
            FROM read_parquet(?)
            """,
            [str(coverage)],
        ).fetchall()
    finally:
        connection.close()
    if len(rows) != 1:
        raise ValueError("rolling coverage must reference one accepted monthly edge source")
    path = (coverage.parent / str(rows[0][0])).resolve()
    if not path.is_file():
        raise ValueError(f"accepted monthly edge source is missing: {path}")
    if file_sha256(path) != str(rows[0][1]):
        raise ValueError("monthly edge source checksum does not match rolling coverage")
    return path


def _benchmark_queries(path: Path) -> dict[str, Any]:
    connection = duckdb.connect()
    try:
        school_ids = [
            str(row[0])
            for row in connection.execute(
                """
                SELECT school_id
                FROM read_parquet(?)
                WHERE corpus_view = 'broad' AND window_months = 24
                GROUP BY school_id
                ORDER BY max(partner_rank) DESC, school_id
                LIMIT 5
                """,
                [str(path)],
            ).fetchall()
        ]
        timings: list[float] = []
        row_counts: list[int] = []
        for school_id in school_ids:
            started = time.perf_counter()
            rows = connection.execute(
                """
                SELECT partner_id, fractional_count, normalized_intensity, edge_persistence
                FROM read_parquet(?)
                WHERE school_id = ? AND corpus_view = 'broad' AND window_months = 24
                ORDER BY partner_rank
                """,
                [str(path), school_id],
            ).fetchall()
            timings.append((time.perf_counter() - started) * 1000)
            row_counts.append(len(rows))
    finally:
        connection.close()
    return {
        "sample_school_count": len(school_ids),
        "median_query_milliseconds": statistics.median(timings) if timings else None,
        "maximum_query_milliseconds": max(timings) if timings else None,
        "rows_per_query": row_counts,
        "performance_budget_milliseconds": 1000,
        "budget_passed": bool(timings) and max(timings) < 1000,
    }


def _distinct_count(path: Path, column: str) -> int:
    connection = duckdb.connect()
    try:
        row = connection.execute(
            f"SELECT count(DISTINCT {column}) FROM read_parquet(?)", [str(path)]
        ).fetchone()
        if row is None:
            raise ValueError("school partner distinct-count query returned no row")
        return int(row[0])
    finally:
        connection.close()


def _outside_edge_core_count(path: Path) -> int:
    """Count indexed schools outside the old 1,000-edge public snapshot when available."""
    old_core = Path("data/processed/network_view_edges_year.parquet")
    if not old_core.is_file():
        return 0
    connection = duckdb.connect()
    try:
        row = connection.execute(
            """
            WITH indexed AS (SELECT DISTINCT school_id FROM read_parquet(?)),
            old AS (
                SELECT source_id AS institution_id FROM read_parquet(?)
                UNION SELECT target_id FROM read_parquet(?)
            )
            SELECT count(*) FROM indexed
            WHERE school_id NOT IN (SELECT institution_id FROM old)
            """,
            [str(path), str(old_core), str(old_core)],
        ).fetchone()
        if row is None:
            raise ValueError("school partner core-coverage query returned no row")
        return int(row[0])
    finally:
        connection.close()


def _literal(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def _sql_string(value: str) -> str:
    return value.replace("'", "''")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
