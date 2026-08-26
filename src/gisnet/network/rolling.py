"""Build exact-calendar rolling school-decision facts.

Publication month is bibliographic observation time. It is not a collaboration,
research, project, or author-mobility start date. Positive institution facts are
materialized; positive rolling edge endpoints are represented by exact maximal
intervals and their metrics are derived from the accepted monthly edge facts.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "rolling-school-facts-2026-08-17-v1"
_SCOPE = "primary_research"
# DuckDB's different grouped/window summation orders vary by less than 1e-6 over
# hundreds of thousands of fractional contributions; all integer counts remain exact.
_TOLERANCE = 1e-6
_MONTH_PATTERN = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
_SUPPORTED_WINDOWS = (12, 24, 36)


def build_rolling_facts(
    institution_month_path: str | Path,
    edge_month_path: str | Path,
    work_publication_dates_path: str | Path,
    work_institutions_path: str | Path,
    *,
    institution_rolling_path: str | Path,
    edge_intervals_path: str | Path,
    coverage_path: str | Path,
    reconciliation_path: str | Path,
    window_months: tuple[int, ...] = _SUPPORTED_WINDOWS,
    observation_start_month: str | None = None,
    observation_end_month: str | None = None,
    corpus_views: list[str] | None = None,
    hierarchy_views: list[str] | None = None,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build sparse positive rolling facts and an exact active-edge interval index."""
    institution_month = Path(institution_month_path)
    edge_month = Path(edge_month_path)
    work_dates = Path(work_publication_dates_path)
    work_institutions = Path(work_institutions_path)
    for source in (institution_month, edge_month, work_dates, work_institutions):
        if not source.is_file():
            raise ValueError(f"rolling input does not exist: {source}")

    windows = _validate_windows(window_months)
    corpora = corpus_views or _distinct_values(institution_month, "corpus_view")
    hierarchies = hierarchy_views or _distinct_values(institution_month, "hierarchy_view")
    if not corpora or not set(corpora).issubset({"strict", "broad"}):
        raise ValueError("corpus views must contain only strict and broad")
    if not hierarchies or not set(hierarchies).issubset({"organization", "umbrella"}):
        raise ValueError("hierarchy views must contain only organization and umbrella")
    bounds_source = (
        "explicit_declared_bounds"
        if observation_start_month is not None and observation_end_month is not None
        else "publication_date_supported_year_domain"
    )
    start_month, end_month = _resolve_observation_bounds(
        work_dates,
        observation_start_month,
        observation_end_month,
    )

    outputs = {
        "institution_outputs_rolling": Path(institution_rolling_path),
        "collaboration_edge_window_intervals": Path(edge_intervals_path),
        "rolling_window_coverage": Path(coverage_path),
        "rolling_reconciliation": Path(reconciliation_path),
    }
    for path in outputs.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    _recover_interrupted_promotion(outputs, _backup_paths(outputs))
    temporary = {name: path.with_suffix(".parquet.tmp") for name, path in outputs.items()}
    scratch_root = outputs["institution_outputs_rolling"].parent
    date_year = scratch_root / ".rolling.date-year.parquet.tmp"
    institution_shards: list[Path] = []
    edge_shards: list[Path] = []
    reconciliation_shards: list[Path] = []
    state_path = scratch_root / ".rolling.edge-state.parquet.tmp"
    all_scratch = [date_year, state_path]
    for corpus in corpora:
        for hierarchy in hierarchies:
            for width in windows:
                suffix = f"{corpus}-{hierarchy}-{width}"
                institution_shards.append(
                    scratch_root / f".rolling.institution-{suffix}.parquet.tmp"
                )
                edge_shards.append(scratch_root / f".rolling.edge-{suffix}.parquet.tmp")
                reconciliation_shards.append(
                    scratch_root / f".rolling.reconciliation-{suffix}.parquet.tmp"
                )
    all_scratch.extend(institution_shards)
    all_scratch.extend(edge_shards)
    all_scratch.extend(reconciliation_shards)
    for path in [*temporary.values(), *all_scratch]:
        path.unlink(missing_ok=True)

    connection = duckdb.connect()
    try:
        _configure(connection, memory_limit, threads)
        relative_edge_source = os.path.relpath(
            edge_month, outputs["rolling_window_coverage"].parent
        )
        _write_coverage(
            connection,
            temporary["rolling_window_coverage"],
            start_month,
            end_month,
            windows,
            corpora,
            hierarchies,
            relative_edge_source,
            file_sha256(edge_month),
            bounds_source,
        )
        _write_date_year(
            connection,
            work_dates,
            work_institutions,
            date_year,
            corpora,
            hierarchies,
        )

        shard_index = 0
        for corpus in corpora:
            for hierarchy in hierarchies:
                for width in windows:
                    institution_shard = institution_shards[shard_index]
                    edge_shard = edge_shards[shard_index]
                    reconciliation_shard = reconciliation_shards[shard_index]
                    _write_edge_state(
                        connection,
                        edge_month,
                        state_path,
                        corpus,
                        hierarchy,
                        width,
                        start_month,
                        end_month,
                    )
                    _write_active_edge_intervals(
                        connection,
                        state_path,
                        edge_shard,
                        corpus,
                        hierarchy,
                        width,
                    )
                    _write_institution_shard(
                        connection,
                        institution_month,
                        state_path,
                        date_year,
                        temporary["rolling_window_coverage"],
                        institution_shard,
                        corpus,
                        hierarchy,
                        width,
                    )
                    _write_reconciliation_shard(
                        connection,
                        institution_month,
                        edge_month,
                        institution_shard,
                        state_path,
                        temporary["rolling_window_coverage"],
                        reconciliation_shard,
                        corpus,
                        hierarchy,
                        width,
                    )
                    state_path.unlink(missing_ok=True)
                    shard_index += 1

        _combine_shards(
            connection,
            institution_shards,
            temporary["institution_outputs_rolling"],
            "corpus_view, hierarchy_view, institution_id, window_months, window_end",
        )
        _combine_shards(
            connection,
            edge_shards,
            temporary["collaboration_edge_window_intervals"],
            "corpus_view, hierarchy_view, source_id, target_id, window_months, "
            "valid_from_window_end",
        )
        _combine_shards(
            connection,
            reconciliation_shards,
            temporary["rolling_reconciliation"],
            "dimension, corpus_view, hierarchy_view, window_months, window_end",
        )
    except BaseException:
        for path in [*temporary.values(), *all_scratch]:
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()

    try:
        _validate_outputs(temporary)
        _promote_outputs(outputs, temporary)
    except BaseException:
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        for path in all_scratch:
            path.unlink(missing_ok=True)

    definitions = _definitions()
    metrics = {
        name: parquet_metrics(
            path,
            primary_key=definitions[name][0],
            required_columns=definitions[name][1],
        )
        for name, path in outputs.items()
    }
    interval_metrics = _interval_metrics(outputs["collaboration_edge_window_intervals"])
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "scope": _SCOPE,
        "publication_time_semantics": (
            "Bibliographic publication-time observation; not collaboration, research, project, "
            "or author-mobility start time."
        ),
        "observation_start_month": start_month,
        "observation_end_month": end_month,
        "observation_bounds_source": bounds_source,
        "window_months": list(windows),
        "corpus_views": corpora,
        "hierarchy_views": hierarchies,
        "coverage_policy": (
            "Observed and eligible months are the exact calendar intersection with declared "
            "dataset coverage; they are never inferred from positive fact rows."
        ),
        "date_coverage_policy": (
            "Exact Works are counted inside the rolling boundary. Annual-only Works are counted "
            "only as overlapping-year candidates; a ratio is emitted only when every affected "
            "calendar year is fully contained by the rolling window."
        ),
        "edge_physical_representation": (
            "Exact maximal positive window-end intervals; edge metrics are reconstructed from "
            "the accepted monthly edge facts for the selected exact calendar window."
        ),
        "reconciliation_tolerance": _TOLERANCE,
        "graph_metric_policy": (
            "Exact degree/strength inputs include isolates. PageRank/community are deferred to "
            "explicit selected windows; rolling betweenness is not precomputed and no "
            "approximation is silently substituted."
        ),
        "physical_representation_metrics": interval_metrics,
        "row_counts": {name: value["row_count"] for name, value in metrics.items()},
        "checksums_sha256": {name: value["checksum_sha256"] for name, value in metrics.items()},
        "outputs": {name: str(path) for name, path in outputs.items()},
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "institution_month_sha256": file_sha256(institution_month),
                "edge_month_sha256": file_sha256(edge_month),
                "work_dates_sha256": file_sha256(work_dates),
                "work_institutions_sha256": file_sha256(work_institutions),
                "observation_start_month": start_month,
                "observation_end_month": end_month,
                "window_months": list(windows),
                "corpus_views": corpora,
                "hierarchy_views": hierarchies,
            }
        ),
        "generated_at_utc": _timestamp(),
    }


def query_rolling_edges(
    edge_intervals_path: str | Path,
    coverage_path: str | Path,
    *,
    window_end: str,
    window_months: int,
    corpus_view: str,
    hierarchy_view: str,
    institution_id: str | None = None,
    limit: int | None = None,
    memory_limit: str = "2GB",
) -> list[dict[str, Any]]:
    """Return exact rolling edge metrics for one indexed window endpoint."""
    _validate_month(window_end, "window_end")
    width = _validate_windows((window_months,))[0]
    if corpus_view not in {"strict", "broad"}:
        raise ValueError("invalid corpus_view")
    if hierarchy_view not in {"organization", "umbrella"}:
        raise ValueError("invalid hierarchy_view")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be positive")
    intervals = Path(edge_intervals_path)
    coverage = Path(coverage_path)
    for source in (intervals, coverage):
        if not source.is_file():
            raise ValueError(f"rolling query input does not exist: {source}")

    connection = duckdb.connect()
    try:
        _configure(connection, memory_limit, 1)
        coverage_row = connection.execute(
            """
            SELECT window_start, window_end, window_months, observed_month_count,
                   eligible_month_count, coverage_ratio, is_complete_window,
                   observation_start_month, observation_end_month, edge_month_source_path,
                   edge_month_source_sha256
            FROM read_parquet(?)
            WHERE window_end = ? AND window_months = ? AND corpus_view = ?
              AND hierarchy_view = ?
            """,
            [str(coverage), window_end, width, corpus_view, hierarchy_view],
        ).fetchone()
        if coverage_row is None:
            raise ValueError("requested rolling window is outside the coverage ledger")
        edge_month = (coverage.parent / str(coverage_row[9])).resolve()
        if not edge_month.is_file():
            raise ValueError(f"accepted monthly edge source is missing: {edge_month}")
        if file_sha256(edge_month) != str(coverage_row[10]):
            raise ValueError("monthly edge source checksum does not match the rolling index")
        institution_filter = ""
        parameters: list[Any] = [
            str(intervals),
            corpus_view,
            hierarchy_view,
            width,
            window_end,
            window_end,
        ]
        if institution_id is not None:
            institution_filter = "AND (source_id = ? OR target_id = ?)"
            parameters.extend([institution_id, institution_id])
        effective_start = max(str(coverage_row[0]), str(coverage_row[7]))
        effective_end = min(window_end, str(coverage_row[8]))
        parameters.extend([str(edge_month), effective_start, effective_end])
        limit_clause = "" if limit is None else f" LIMIT {int(limit)}"
        description = connection.execute(
            f"""
            WITH active AS (
                SELECT source_id, target_id
                FROM read_parquet(?)
                WHERE corpus_view = ? AND hierarchy_view = ? AND window_months = ?
                  AND valid_from_window_end <= ? AND valid_through_window_end >= ?
                  {institution_filter}
            ), rolled AS (
                SELECT
                    e.corpus_view,
                    e.hierarchy_view,
                    any_value(e.scope) AS scope,
                    e.source_id,
                    e.target_id,
                    any_value(e.source_name) AS source_name,
                    any_value(e.target_name) AS target_name,
                    any_value(e.source_region) AS source_region,
                    any_value(e.target_region) AS target_region,
                    any_value(e.source_subregion) AS source_subregion,
                    any_value(e.target_subregion) AS target_subregion,
                    any_value(e.source_country) AS source_country,
                    any_value(e.target_country) AS target_country,
                    any_value(e.source_category) AS source_category,
                    any_value(e.target_category) AS target_category,
                    sum(e.full_count)::BIGINT AS full_count,
                    sum(e.fractional_count) AS fractional_count,
                    sum(e.distinct_work_count)::BIGINT AS distinct_work_count,
                    count(*)::BIGINT AS active_month_count
                FROM read_parquet(?) e
                JOIN active USING (source_id, target_id)
                WHERE e.corpus_view = '{_sql_string(corpus_view)}'
                  AND e.hierarchy_view = '{_sql_string(hierarchy_view)}'
                  AND e.publication_month BETWEEN ? AND ?
                GROUP BY e.corpus_view, e.hierarchy_view, e.source_id, e.target_id
            )
            SELECT
                '{_sql_string(str(coverage_row[0]))}' AS window_start,
                '{_sql_string(window_end)}' AS window_end,
                {width}::INTEGER AS window_months,
                {int(coverage_row[3])}::INTEGER AS observed_month_count,
                {int(coverage_row[4])}::INTEGER AS eligible_month_count,
                {float(coverage_row[5])}::DOUBLE AS coverage_ratio,
                {str(bool(coverage_row[6])).lower()}::BOOLEAN AS is_complete_window,
                *,
                active_month_count::DOUBLE / {width} AS edge_persistence
            FROM rolled
            ORDER BY source_id, target_id{limit_clause}
            """,
            parameters,
        )
        columns = [item[0] for item in description.description]
        rows = description.fetchall()
    finally:
        connection.close()
    return [dict(zip(columns, row, strict=True)) for row in rows]


def _write_coverage(
    connection: duckdb.DuckDBPyConnection,
    output: Path,
    start_month: str,
    end_month: str,
    windows: tuple[int, ...],
    corpora: list[str],
    hierarchies: list[str],
    edge_month_source_path: str,
    edge_month_source_sha256: str,
    bounds_source: str,
) -> None:
    window_values = ", ".join(f"({value})" for value in windows)
    corpus_values = ", ".join(f"('{_sql_string(value)}')" for value in corpora)
    hierarchy_values = ", ".join(f"('{_sql_string(value)}')" for value in hierarchies)
    query = f"""
        WITH ends AS (
            SELECT generate_series AS end_date
            FROM generate_series(
                strptime('{start_month}-01', '%Y-%m-%d')::DATE,
                strptime('{end_month}-01', '%Y-%m-%d')::DATE,
                INTERVAL 1 MONTH
            )
        ), widths(window_months) AS (VALUES {window_values}),
        corpora(corpus_view) AS (VALUES {corpus_values}),
        hierarchies(hierarchy_view) AS (VALUES {hierarchy_values}),
        base AS (
            SELECT
                strftime(end_date - (window_months - 1) * INTERVAL 1 MONTH, '%Y-%m')
                    AS window_start,
                strftime(end_date, '%Y-%m') AS window_end,
                window_months::INTEGER AS window_months,
                corpus_view,
                hierarchy_view,
                greatest(
                    0,
                    date_diff(
                        'month',
                        greatest(
                            end_date - (window_months - 1) * INTERVAL 1 MONTH,
                            strptime('{start_month}-01', '%Y-%m-%d')::DATE
                        ),
                        least(end_date, strptime('{end_month}-01', '%Y-%m-%d')::DATE)
                    ) + 1
                )::INTEGER AS observed_month_count
            FROM ends CROSS JOIN widths CROSS JOIN corpora CROSS JOIN hierarchies
        )
        SELECT
            window_start,
            window_end,
            window_months,
            corpus_view,
            hierarchy_view,
            '{_SCOPE}' AS scope,
            '{start_month}' AS observation_start_month,
            '{end_month}' AS observation_end_month,
            observed_month_count,
            observed_month_count AS eligible_month_count,
            observed_month_count::DOUBLE / window_months AS coverage_ratio,
            observed_month_count = window_months AS is_complete_window,
            '{_sql_string(bounds_source)}' AS observation_bounds_source,
            '{_sql_string(edge_month_source_path)}' AS edge_month_source_path,
            '{edge_month_source_sha256}' AS edge_month_source_sha256
        FROM base
        ORDER BY corpus_view, hierarchy_view, window_months, window_end
    """
    _copy(connection, query, output)


def _write_date_year(
    connection: duckdb.DuckDBPyConnection,
    dates: Path,
    memberships: Path,
    output: Path,
    corpora: list[str],
    hierarchies: list[str],
) -> None:
    corpus_values = ", ".join(f"('{_sql_string(value)}')" for value in corpora)
    hierarchy_values = ", ".join(f"('{_sql_string(value)}')" for value in hierarchies)
    query = f"""
        WITH corpora(corpus_view) AS (VALUES {corpus_values}),
        hierarchies(hierarchy_view) AS (VALUES {hierarchy_values}),
        memberships AS (
            SELECT DISTINCT
                c.corpus_view,
                wi.hierarchy_view,
                wi.institution_id,
                wi.work_id,
                wi.publication_year
            FROM read_parquet(?) wi
            CROSS JOIN corpora c
            JOIN hierarchies h ON wi.hierarchy_view = h.hierarchy_view
            WHERE wi.is_primary_research_scope
              AND CASE c.corpus_view
                    WHEN 'strict' THEN wi.strict_primary
                    WHEN 'broad' THEN wi.broad_primary
                  END
        )
        SELECT
            m.corpus_view,
            m.hierarchy_view,
            m.institution_id,
            m.publication_year,
            count(*) FILTER (WHERE d.subannual_date_eligible)::BIGINT
                AS exact_date_work_count,
            count(*) FILTER (WHERE NOT d.subannual_date_eligible)::BIGINT
                AS annual_only_work_count
        FROM memberships m
        JOIN read_parquet(?) d USING (work_id, publication_year)
        GROUP BY m.corpus_view, m.hierarchy_view, m.institution_id, m.publication_year
        ORDER BY m.corpus_view, m.hierarchy_view, m.institution_id, m.publication_year
    """
    _copy(connection, query, output, [str(memberships), str(dates)])


def _write_edge_state(
    connection: duckdb.DuckDBPyConnection,
    edge_month: Path,
    output: Path,
    corpus: str,
    hierarchy: str,
    width: int,
    start_month: str,
    end_month: str,
) -> None:
    end_index = _month_index(end_month)
    query = f"""
        WITH monthly AS (
            SELECT
                publication_year * 12 + cast(substr(publication_month, 6, 2) AS INTEGER) - 1
                    AS month_index,
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
                sum(full_count)::BIGINT AS full_count,
                sum(fractional_count) AS fractional_count,
                sum(distinct_work_count)::BIGINT AS distinct_work_count
            FROM read_parquet(?)
            WHERE corpus_view = '{_sql_string(corpus)}'
              AND hierarchy_view = '{_sql_string(hierarchy)}'
              AND publication_month BETWEEN '{start_month}' AND '{end_month}'
            GROUP BY publication_year, publication_month, source_id, target_id
        ), dimensions AS (
            SELECT
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
                any_value(target_category) AS target_category
            FROM monthly
            GROUP BY source_id, target_id
        ), events AS (
            SELECT source_id, target_id, month_index AS event_index,
                   full_count AS full_delta,
                   fractional_count AS fractional_delta,
                   distinct_work_count AS distinct_delta,
                   1::BIGINT AS active_delta
            FROM monthly
            UNION ALL
            SELECT source_id, target_id, month_index + {width} AS event_index,
                   -full_count AS full_delta,
                   -fractional_count AS fractional_delta,
                   -distinct_work_count AS distinct_delta,
                   -1::BIGINT AS active_delta
            FROM monthly
        ), event_sums AS (
            SELECT source_id, target_id, event_index,
                   sum(full_delta)::BIGINT AS full_delta,
                   sum(fractional_delta) AS fractional_delta,
                   sum(distinct_delta)::BIGINT AS distinct_delta,
                   sum(active_delta)::BIGINT AS active_delta
            FROM events
            GROUP BY source_id, target_id, event_index
        ), states AS (
            SELECT
                source_id,
                target_id,
                event_index AS valid_from_index,
                least(
                    {end_index},
                    lead(event_index, 1, {end_index + 1}) OVER (
                        PARTITION BY source_id, target_id ORDER BY event_index
                    ) - 1
                ) AS valid_through_index,
                sum(full_delta) OVER (
                    PARTITION BY source_id, target_id ORDER BY event_index
                )::BIGINT AS full_count,
                sum(fractional_delta) OVER (
                    PARTITION BY source_id, target_id ORDER BY event_index
                ) AS fractional_count,
                sum(distinct_delta) OVER (
                    PARTITION BY source_id, target_id ORDER BY event_index
                )::BIGINT AS distinct_work_count,
                sum(active_delta) OVER (
                    PARTITION BY source_id, target_id ORDER BY event_index
                )::BIGINT AS active_month_count
            FROM event_sums
        )
        SELECT
            '{_sql_string(corpus)}' AS corpus_view,
            '{_sql_string(hierarchy)}' AS hierarchy_view,
            '{_SCOPE}' AS scope,
            {width}::INTEGER AS window_months,
            s.valid_from_index,
            s.valid_through_index,
            s.source_id,
            s.target_id,
            d.source_name,
            d.target_name,
            d.source_region,
            d.target_region,
            d.source_subregion,
            d.target_subregion,
            d.source_country,
            d.target_country,
            d.source_category,
            d.target_category,
            s.full_count,
            s.fractional_count,
            s.distinct_work_count,
            s.active_month_count
        FROM states s
        JOIN dimensions d USING (source_id, target_id)
        WHERE s.valid_from_index <= {end_index}
          AND s.valid_through_index >= s.valid_from_index
          AND s.full_count > 0
        ORDER BY source_id, target_id, valid_from_index
    """
    _copy(connection, query, output, [str(edge_month)])


def _write_active_edge_intervals(
    connection: duckdb.DuckDBPyConnection,
    state_path: Path,
    output: Path,
    corpus: str,
    hierarchy: str,
    width: int,
) -> None:
    query = f"""
        WITH ordered AS (
            SELECT *,
                   lag(valid_through_index) OVER (
                       PARTITION BY source_id, target_id ORDER BY valid_from_index
                   ) AS previous_through
            FROM read_parquet(?)
        ), grouped AS (
            SELECT *,
                   sum(
                       CASE WHEN previous_through IS NULL
                                  OR valid_from_index > previous_through + 1
                            THEN 1 ELSE 0 END
                   ) OVER (
                       PARTITION BY source_id, target_id ORDER BY valid_from_index
                   ) AS interval_group
            FROM ordered
        )
        SELECT
            '{_sql_string(corpus)}' AS corpus_view,
            '{_sql_string(hierarchy)}' AS hierarchy_view,
            '{_SCOPE}' AS scope,
            {width}::INTEGER AS window_months,
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
            {_month_from_index_sql("min(valid_from_index)")} AS valid_from_window_end,
            {_month_from_index_sql("max(valid_through_index)")} AS valid_through_window_end,
            min(valid_from_index)::INTEGER AS valid_from_month_index,
            max(valid_through_index)::INTEGER AS valid_through_month_index
        FROM grouped
        GROUP BY source_id, target_id, interval_group
        ORDER BY source_id, target_id, valid_from_window_end
    """
    _copy(connection, query, output, [str(state_path)])


def _write_institution_shard(
    connection: duckdb.DuckDBPyConnection,
    institution_month: Path,
    edge_state: Path,
    date_year: Path,
    coverage: Path,
    output: Path,
    corpus: str,
    hierarchy: str,
    width: int,
) -> None:
    query = f"""
        WITH coverage AS (
            SELECT *,
                   cast(substr(window_end, 1, 4) AS INTEGER) * 12
                       + cast(substr(window_end, 6, 2) AS INTEGER) - 1 AS window_end_index
            FROM read_parquet(?)
            WHERE corpus_view = '{_sql_string(corpus)}'
              AND hierarchy_view = '{_sql_string(hierarchy)}'
              AND window_months = {width}
        ), activity AS (
            SELECT
                c.window_start,
                c.window_end,
                c.window_end_index,
                c.window_months,
                c.observation_start_month,
                c.observation_end_month,
                c.observed_month_count,
                c.eligible_month_count,
                c.coverage_ratio,
                c.is_complete_window,
                m.corpus_view,
                m.hierarchy_view,
                any_value(m.scope) AS scope,
                m.institution_id,
                any_value(m.display_name) AS display_name,
                any_value(m.ror_id) AS ror_id,
                any_value(m.country_code) AS country_code,
                any_value(m.country_name) AS country_name,
                any_value(m.macro_region) AS macro_region,
                any_value(m.subregion) AS subregion,
                any_value(m.institution_category) AS institution_category,
                any_value(m.analytical_scope) AS analytical_scope,
                any_value(m.latitude) AS latitude,
                any_value(m.longitude) AS longitude,
                sum(m.work_count)::BIGINT AS work_count,
                sum(m.fractional_work_count) AS fractional_work_count,
                sum(m.collaborative_work_count)::BIGINT AS collaborative_work_count,
                sum(m.single_institution_work_count)::BIGINT AS single_institution_work_count,
                sum(m.international_work_count)::BIGINT AS international_work_count,
                sum(m.cross_region_work_count)::BIGINT AS cross_region_work_count,
                count(*)::BIGINT AS active_publication_month_count
            FROM coverage c
            JOIN read_parquet(?) m
             ON m.corpus_view = c.corpus_view
             AND m.hierarchy_view = c.hierarchy_view
             AND m.publication_month BETWEEN greatest(
                    c.window_start, c.observation_start_month
                 ) AND least(c.window_end, c.observation_end_month)
            GROUP BY c.window_start, c.window_end, c.window_end_index, c.window_months,
                     c.observation_start_month, c.observation_end_month,
                     c.observed_month_count, c.eligible_month_count, c.coverage_ratio,
                     c.is_complete_window, m.corpus_view, m.hierarchy_view, m.institution_id
        ), incidence AS (
            SELECT source_id AS institution_id, target_id AS partner_id,
                   target_country AS partner_country, valid_from_index, valid_through_index,
                   fractional_count AS strength, active_month_count
            FROM read_parquet(?)
            UNION ALL
            SELECT target_id AS institution_id, source_id AS partner_id,
                   source_country AS partner_country, valid_from_index, valid_through_index,
                   fractional_count AS strength, active_month_count
            FROM read_parquet(?)
        ), partner_events AS (
            SELECT institution_id, valid_from_index AS event_index,
                   1::BIGINT AS partner_delta,
                   (active_month_count >= 2)::BIGINT AS repeat_delta,
                   strength AS strength_delta,
                   CASE WHEN strength > 0 THEN strength * ln(strength) ELSE 0.0 END
                       AS entropy_delta
            FROM incidence
            UNION ALL
            SELECT institution_id, valid_through_index + 1 AS event_index,
                   -1::BIGINT AS partner_delta,
                   -(active_month_count >= 2)::BIGINT AS repeat_delta,
                   -strength AS strength_delta,
                   CASE WHEN strength > 0 THEN -strength * ln(strength) ELSE 0.0 END
                       AS entropy_delta
            FROM incidence
        ), country_ordered AS (
            SELECT institution_id, partner_country, valid_from_index, valid_through_index,
                   max(valid_through_index) OVER (
                       PARTITION BY institution_id, partner_country
                       ORDER BY valid_from_index, valid_through_index
                       ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                   ) AS prior_maximum
            FROM incidence
            WHERE partner_country IS NOT NULL
        ), country_grouped AS (
            SELECT *,
                   sum(CASE WHEN prior_maximum IS NULL
                                  OR valid_from_index > prior_maximum + 1
                            THEN 1 ELSE 0 END)
                   OVER (
                       PARTITION BY institution_id, partner_country
                       ORDER BY valid_from_index, valid_through_index
                   ) AS country_group
            FROM country_ordered
        ), country_islands AS (
            SELECT institution_id, partner_country, country_group,
                   min(valid_from_index) AS valid_from_index,
                   max(valid_through_index) AS valid_through_index
            FROM country_grouped
            GROUP BY institution_id, partner_country, country_group
        ), country_events AS (
            SELECT institution_id, valid_from_index AS event_index,
                   1::BIGINT AS country_delta
            FROM country_islands
            UNION ALL
            SELECT institution_id, valid_through_index + 1 AS event_index,
                   -1::BIGINT AS country_delta
            FROM country_islands
        ), combined_events AS (
            SELECT institution_id, event_index,
                   sum(partner_delta)::BIGINT AS partner_delta,
                   sum(repeat_delta)::BIGINT AS repeat_delta,
                   sum(strength_delta) AS strength_delta,
                   sum(entropy_delta) AS entropy_delta,
                   0::BIGINT AS country_delta
            FROM partner_events
            GROUP BY institution_id, event_index
            UNION ALL
            SELECT institution_id, event_index, 0::BIGINT, 0::BIGINT, 0.0, 0.0,
                   sum(country_delta)::BIGINT
            FROM country_events
            GROUP BY institution_id, event_index
        ), event_sums AS (
            SELECT institution_id, event_index,
                   sum(partner_delta)::BIGINT AS partner_delta,
                   sum(repeat_delta)::BIGINT AS repeat_delta,
                   sum(strength_delta) AS strength_delta,
                   sum(entropy_delta) AS entropy_delta,
                   sum(country_delta)::BIGINT AS country_delta
            FROM combined_events
            GROUP BY institution_id, event_index
        ), partner_states AS (
            SELECT
                institution_id,
                event_index AS valid_from_index,
                lead(event_index, 1, 2147483647) OVER (
                    PARTITION BY institution_id ORDER BY event_index
                ) - 1 AS valid_through_index,
                sum(partner_delta) OVER (
                    PARTITION BY institution_id ORDER BY event_index
                )::BIGINT AS partner_institution_count,
                sum(repeat_delta) OVER (
                    PARTITION BY institution_id ORDER BY event_index
                )::BIGINT AS repeat_partner_count,
                sum(strength_delta) OVER (
                    PARTITION BY institution_id ORDER BY event_index
                ) AS fractional_collaboration_strength,
                sum(entropy_delta) OVER (
                    PARTITION BY institution_id ORDER BY event_index
                ) AS entropy_numerator,
                sum(country_delta) OVER (
                    PARTITION BY institution_id ORDER BY event_index
                )::BIGINT AS partner_country_count
            FROM event_sums
        ), date_qa AS (
            SELECT
                a.window_end,
                a.institution_id,
                sum(d.annual_only_work_count)::BIGINT AS annual_only_work_count,
                bool_or(
                    d.annual_only_work_count > 0
                    AND NOT (
                        greatest(a.window_start, a.observation_start_month)
                            <= cast(d.publication_year AS VARCHAR) || '-01'
                        AND least(a.window_end, a.observation_end_month)
                            >= cast(d.publication_year AS VARCHAR) || '-12'
                    )
                ) AS has_indeterminate_boundary_year
            FROM activity a
            LEFT JOIN read_parquet(?) d
              ON d.corpus_view = a.corpus_view
             AND d.hierarchy_view = a.hierarchy_view
             AND d.institution_id = a.institution_id
             AND d.publication_year BETWEEN cast(
                    substr(greatest(a.window_start, a.observation_start_month), 1, 4)
                    AS INTEGER
                 ) AND cast(
                    substr(least(a.window_end, a.observation_end_month), 1, 4)
                    AS INTEGER
                 )
            GROUP BY a.window_end, a.institution_id
        )
        SELECT
            a.window_start,
            a.window_end,
            a.window_months,
            a.observed_month_count,
            a.eligible_month_count,
            a.coverage_ratio,
            a.is_complete_window,
            a.corpus_view,
            a.hierarchy_view,
            a.scope,
            a.institution_id,
            a.display_name,
            a.ror_id,
            a.country_code,
            a.country_name,
            a.macro_region,
            a.subregion,
            a.institution_category,
            a.analytical_scope,
            a.latitude,
            a.longitude,
            a.work_count,
            a.fractional_work_count,
            a.collaborative_work_count,
            a.single_institution_work_count,
            a.international_work_count,
            a.cross_region_work_count,
            a.international_work_count::DOUBLE / a.work_count
                AS international_collaboration_share,
            a.cross_region_work_count::DOUBLE / a.work_count
                AS cross_region_collaboration_share,
            a.active_publication_month_count,
            coalesce(p.partner_institution_count, 0)::BIGINT AS partner_institution_count,
            coalesce(p.partner_country_count, 0)::BIGINT AS partner_country_count,
            CASE WHEN coalesce(p.partner_institution_count, 0) > 0
                 THEN greatest(coalesce(p.fractional_collaboration_strength, 0.0), 0.0)
                 ELSE 0.0 END AS fractional_collaboration_strength,
            coalesce(p.repeat_partner_count, 0)::BIGINT AS repeat_partner_count,
            CASE WHEN coalesce(p.partner_institution_count, 0) > 0
                 THEN p.repeat_partner_count::DOUBLE / p.partner_institution_count
                 ELSE NULL END AS repeat_partner_ratio,
            CASE WHEN coalesce(p.partner_institution_count, 0) > 0
                       AND coalesce(p.fractional_collaboration_strength, 0.0) > 1e-15
                 THEN exp(
                     ln(p.fractional_collaboration_strength)
                     - p.entropy_numerator / p.fractional_collaboration_strength
                 ) ELSE 0.0 END AS effective_partner_count,
            a.work_count AS exact_date_work_count,
            coalesce(d.annual_only_work_count, 0)::BIGINT AS annual_only_work_count,
            CASE WHEN coalesce(d.has_indeterminate_boundary_year, false) THEN NULL
                 ELSE a.work_count::DOUBLE
                      / nullif(a.work_count + coalesce(d.annual_only_work_count, 0), 0)
                 END AS date_coverage_ratio,
            CASE WHEN coalesce(d.has_indeterminate_boundary_year, false)
                 THEN 'indeterminate_boundary_year' ELSE 'exact' END
                 AS date_coverage_status,
            'exact_window_plus_overlapping_annual_only_candidates' AS date_coverage_basis
        FROM activity a
        LEFT JOIN partner_states p
          ON p.institution_id = a.institution_id
         AND a.window_end_index BETWEEN p.valid_from_index AND p.valid_through_index
        LEFT JOIN date_qa d
          ON d.window_end = a.window_end AND d.institution_id = a.institution_id
        ORDER BY a.institution_id, a.window_end
    """
    _copy(
        connection,
        query,
        output,
        [str(coverage), str(institution_month), str(edge_state), str(edge_state), str(date_year)],
    )


def _write_reconciliation_shard(
    connection: duckdb.DuckDBPyConnection,
    institution_month: Path,
    edge_month: Path,
    institution_rolling: Path,
    edge_state: Path,
    coverage: Path,
    output: Path,
    corpus: str,
    hierarchy: str,
    width: int,
) -> None:
    query = f"""
        WITH coverage AS (
            SELECT *,
                   cast(substr(window_end, 1, 4) AS INTEGER) * 12
                       + cast(substr(window_end, 6, 2) AS INTEGER) - 1 AS window_end_index
            FROM read_parquet(?)
            WHERE corpus_view = '{_sql_string(corpus)}'
              AND hierarchy_view = '{_sql_string(hierarchy)}'
              AND window_months = {width}
        ), institution_expected AS (
            SELECT c.window_end,
                   coalesce(sum(m.work_count), 0)::DOUBLE AS expected_full,
                   coalesce(sum(m.fractional_work_count), 0.0) AS expected_fractional
            FROM coverage c
            LEFT JOIN read_parquet(?) m
              ON m.corpus_view = c.corpus_view AND m.hierarchy_view = c.hierarchy_view
             AND m.publication_month BETWEEN greatest(
                    c.window_start, c.observation_start_month
                 ) AND least(c.window_end, c.observation_end_month)
            GROUP BY c.window_end
        ), institution_actual AS (
            SELECT window_end, sum(work_count)::DOUBLE AS actual_full,
                   sum(fractional_work_count) AS actual_fractional
            FROM read_parquet(?) GROUP BY window_end
        ), edge_expected AS (
            SELECT c.window_end,
                   coalesce(sum(m.full_count), 0)::DOUBLE AS expected_full,
                   coalesce(sum(m.fractional_count), 0.0) AS expected_fractional
            FROM coverage c
            LEFT JOIN read_parquet(?) m
              ON m.corpus_view = c.corpus_view AND m.hierarchy_view = c.hierarchy_view
             AND m.publication_month BETWEEN greatest(
                    c.window_start, c.observation_start_month
                 ) AND least(c.window_end, c.observation_end_month)
            GROUP BY c.window_end
        ), edge_actual AS (
            SELECT c.window_end,
                   coalesce(sum(s.full_count), 0)::DOUBLE AS actual_full,
                   coalesce(sum(s.fractional_count), 0.0) AS actual_fractional
            FROM coverage c
            LEFT JOIN read_parquet(?) s
              ON c.window_end_index BETWEEN s.valid_from_index AND s.valid_through_index
            GROUP BY c.window_end
        ), combined AS (
            SELECT 'institution' AS dimension, c.window_start, c.window_end,
                   i.expected_full, coalesce(a.actual_full, 0) AS actual_full,
                   i.expected_fractional,
                   coalesce(a.actual_fractional, 0) AS actual_fractional
            FROM coverage c
            JOIN institution_expected i USING (window_end)
            LEFT JOIN institution_actual a USING (window_end)
            UNION ALL
            SELECT 'edge', c.window_start, c.window_end,
                   e.expected_full, a.actual_full,
                   e.expected_fractional, a.actual_fractional
            FROM coverage c
            JOIN edge_expected e USING (window_end)
            JOIN edge_actual a USING (window_end)
        )
        SELECT
            dimension,
            window_start,
            window_end,
            {width}::INTEGER AS window_months,
            '{_sql_string(corpus)}' AS corpus_view,
            '{_sql_string(hierarchy)}' AS hierarchy_view,
            expected_full AS expected_full_count,
            actual_full AS actual_full_count,
            actual_full - expected_full AS full_count_difference,
            expected_fractional AS expected_fractional_count,
            actual_fractional AS actual_fractional_count,
            actual_fractional - expected_fractional AS fractional_count_difference,
            abs(actual_full - expected_full) <= {_TOLERANCE}
              AND abs(actual_fractional - expected_fractional) <= {_TOLERANCE}
              AS reconciliation_passed
        FROM combined
        ORDER BY dimension, window_end
    """
    _copy(
        connection,
        query,
        output,
        [
            str(coverage),
            str(institution_month),
            str(institution_rolling),
            str(edge_month),
            str(edge_state),
        ],
    )


def _combine_shards(
    connection: duckdb.DuckDBPyConnection,
    shards: list[Path],
    output: Path,
    order_by: str,
) -> None:
    paths = ", ".join(f"'{_literal(path)}'" for path in shards)
    _copy(connection, f"SELECT * FROM read_parquet([{paths}]) ORDER BY {order_by}", output)


def _definitions() -> dict[str, tuple[list[str], set[str]]]:
    coverage_fields = {
        "window_start",
        "window_end",
        "window_months",
        "observed_month_count",
        "eligible_month_count",
        "coverage_ratio",
        "is_complete_window",
    }
    return {
        "institution_outputs_rolling": (
            ["window_end", "window_months", "corpus_view", "hierarchy_view", "institution_id"],
            coverage_fields
            | {
                "corpus_view",
                "hierarchy_view",
                "scope",
                "institution_id",
                "work_count",
                "fractional_work_count",
                "partner_institution_count",
                "partner_country_count",
                "fractional_collaboration_strength",
                "effective_partner_count",
                "repeat_partner_ratio",
                "exact_date_work_count",
                "annual_only_work_count",
                "date_coverage_ratio",
                "date_coverage_status",
            },
        ),
        "collaboration_edge_window_intervals": (
            [
                "corpus_view",
                "hierarchy_view",
                "source_id",
                "target_id",
                "window_months",
                "valid_from_window_end",
            ],
            {
                "corpus_view",
                "hierarchy_view",
                "source_id",
                "target_id",
                "window_months",
                "valid_from_window_end",
                "valid_through_window_end",
            },
        ),
        "rolling_window_coverage": (
            ["window_end", "window_months", "corpus_view", "hierarchy_view"],
            coverage_fields
            | {
                "corpus_view",
                "hierarchy_view",
                "observation_bounds_source",
                "edge_month_source_path",
                "edge_month_source_sha256",
            },
        ),
        "rolling_reconciliation": (
            ["dimension", "window_end", "window_months", "corpus_view", "hierarchy_view"],
            {
                "dimension",
                "window_end",
                "window_months",
                "corpus_view",
                "hierarchy_view",
                "full_count_difference",
                "fractional_count_difference",
                "reconciliation_passed",
            },
        ),
    }


def _validate_outputs(temporary: dict[str, Path]) -> None:
    definitions = _definitions()
    for name, path in temporary.items():
        key, required = definitions[name]
        parquet_metrics(path, primary_key=key, required_columns=required)
    connection = duckdb.connect()
    try:
        bad_coverage = connection.execute(
            """
            SELECT count(*) FROM read_parquet(?)
            WHERE eligible_month_count > observed_month_count
               OR observed_month_count > window_months
               OR abs(coverage_ratio - eligible_month_count::DOUBLE / window_months) > 1e-12
               OR is_complete_window <>
                  (observed_month_count = window_months AND eligible_month_count = window_months)
            """,
            [str(temporary["rolling_window_coverage"])],
        ).fetchone()
        bad_institutions = connection.execute(
            """
            SELECT count(*) FROM read_parquet(?)
            WHERE work_count <= 0 OR collaborative_work_count + single_institution_work_count
                                      <> work_count
               OR partner_institution_count < 0 OR partner_country_count < 0
               OR fractional_collaboration_strength < -1e-10
               OR repeat_partner_count > partner_institution_count
               OR (partner_institution_count = 0 AND repeat_partner_ratio IS NOT NULL)
               OR (partner_institution_count = 0
                   AND (fractional_collaboration_strength <> 0
                        OR effective_partner_count <> 0
                        OR repeat_partner_count <> 0
                        OR partner_country_count <> 0))
            """,
            [str(temporary["institution_outputs_rolling"])],
        ).fetchone()
        bad_edges = connection.execute(
            """
            WITH ordered AS (
                SELECT *, lag(valid_through_month_index) OVER (
                    PARTITION BY corpus_view, hierarchy_view, source_id, target_id, window_months
                    ORDER BY valid_from_month_index
                ) AS prior_through
                FROM read_parquet(?)
            )
            SELECT count(*) FROM ordered
            WHERE source_id >= target_id
               OR valid_from_month_index > valid_through_month_index
               OR prior_through + 1 >= valid_from_month_index
               OR valid_from_window_end <>
                  printf('%04d-%02d', floor(valid_from_month_index / 12)::INTEGER,
                         (valid_from_month_index % 12 + 1)::INTEGER)
               OR valid_through_window_end <>
                  printf('%04d-%02d', floor(valid_through_month_index / 12)::INTEGER,
                         (valid_through_month_index % 12 + 1)::INTEGER)
            """,
            [str(temporary["collaboration_edge_window_intervals"])],
        ).fetchone()
        failed_reconciliation = connection.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE NOT reconciliation_passed",
            [str(temporary["rolling_reconciliation"])],
        ).fetchone()
    finally:
        connection.close()
    if bad_coverage is None or int(bad_coverage[0]):
        raise ValueError("rolling coverage ledger is invalid")
    if bad_institutions is None or int(bad_institutions[0]):
        raise ValueError("rolling institution facts are invalid")
    if bad_edges is None or int(bad_edges[0]):
        raise ValueError("rolling edge interval index is invalid")
    _validate_edge_interval_identity(temporary)
    if failed_reconciliation is None or int(failed_reconciliation[0]):
        raise ValueError("rolling facts failed monthly-source reconciliation")


def _validate_edge_interval_identity(temporary: dict[str, Path]) -> None:
    """Prove every exact positive edge endpoint is represented once by a maximal interval."""
    coverage = temporary["rolling_window_coverage"]
    intervals = temporary["collaboration_edge_window_intervals"]
    connection = duckdb.connect()
    try:
        _configure(connection, "4GB", 1)
        source_rows = connection.execute(
            """
            SELECT DISTINCT edge_month_source_path, edge_month_source_sha256
            FROM read_parquet(?)
            """,
            [str(coverage)],
        ).fetchall()
        if len(source_rows) != 1:
            raise ValueError("rolling coverage references multiple monthly edge generations")
        edge_month = (coverage.parent / str(source_rows[0][0])).resolve()
        if not edge_month.is_file() or file_sha256(edge_month) != str(source_rows[0][1]):
            raise ValueError("rolling coverage monthly edge provenance is invalid")
        mismatch = connection.execute(
            """
            WITH bounds AS (
                SELECT DISTINCT
                    corpus_view,
                    hierarchy_view,
                    window_months,
                    observation_start_month,
                    observation_end_month,
                    cast(substr(observation_end_month, 1, 4) AS INTEGER) * 12
                        + cast(substr(observation_end_month, 6, 2) AS INTEGER) - 1
                        AS observation_end_index
                FROM read_parquet(?)
            ), monthly AS (
                SELECT
                    b.corpus_view,
                    b.hierarchy_view,
                    b.window_months,
                    b.observation_end_index,
                    e.source_id,
                    e.target_id,
                    e.publication_year * 12
                        + cast(substr(e.publication_month, 6, 2) AS INTEGER) - 1
                        AS month_index
                FROM read_parquet(?) e
                JOIN bounds b USING (corpus_view, hierarchy_view)
                WHERE e.publication_month BETWEEN b.observation_start_month
                                              AND b.observation_end_month
            ), ordered AS (
                SELECT *,
                       lag(month_index) OVER (
                           PARTITION BY corpus_view, hierarchy_view, window_months,
                                        source_id, target_id
                           ORDER BY month_index
                       ) AS previous_month_index
                FROM monthly
            ), tagged AS (
                SELECT *,
                       sum(
                           CASE WHEN previous_month_index IS NULL
                                      OR month_index - previous_month_index > window_months
                                THEN 1 ELSE 0 END
                       ) OVER (
                           PARTITION BY corpus_view, hierarchy_view, window_months,
                                        source_id, target_id
                           ORDER BY month_index
                       ) AS interval_group
                FROM ordered
            ), expected AS (
                SELECT
                    corpus_view,
                    hierarchy_view,
                    source_id,
                    target_id,
                    window_months,
                    min(month_index)::INTEGER AS valid_from_month_index,
                    least(
                        any_value(observation_end_index),
                        max(month_index) + window_months - 1
                    )::INTEGER AS valid_through_month_index,
                    true AS expected_present
                FROM tagged
                GROUP BY corpus_view, hierarchy_view, source_id, target_id,
                         window_months, interval_group
            ), actual AS (
                SELECT corpus_view, hierarchy_view, source_id, target_id, window_months,
                       valid_from_month_index, valid_through_month_index,
                       true AS actual_present
                FROM read_parquet(?)
            )
            SELECT count(*)
            FROM expected e
            FULL OUTER JOIN actual a USING (
                corpus_view, hierarchy_view, source_id, target_id, window_months,
                valid_from_month_index, valid_through_month_index
            )
            WHERE e.expected_present IS NULL OR a.actual_present IS NULL
            """,
            [str(coverage), str(edge_month), str(intervals)],
        ).fetchone()
    finally:
        connection.close()
    if mismatch is None or int(mismatch[0]):
        raise ValueError("rolling edge intervals do not exactly match monthly edge activity")


def _promote_outputs(outputs: dict[str, Path], temporary: dict[str, Path]) -> None:
    backups = _backup_paths(outputs)
    _recover_interrupted_promotion(outputs, backups)
    previously_existing: set[str] = set()
    promoted: set[str] = set()
    try:
        for name, destination in outputs.items():
            if destination.exists():
                os.replace(destination, backups[name])
                previously_existing.add(name)
            os.replace(temporary[name], destination)
            promoted.add(name)
    except BaseException:
        for name in promoted:
            outputs[name].unlink(missing_ok=True)
        for name in previously_existing:
            if backups[name].exists():
                os.replace(backups[name], outputs[name])
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise
    for backup in backups.values():
        backup.unlink(missing_ok=True)


def _recover_interrupted_promotion(outputs: dict[str, Path], backups: dict[str, Path]) -> None:
    for name, backup in backups.items():
        if not backup.exists():
            continue
        outputs[name].unlink(missing_ok=True)
        os.replace(backup, outputs[name])


def _backup_paths(outputs: dict[str, Path]) -> dict[str, Path]:
    return {
        name: destination.with_name(f".{destination.name}.rollback.tmp")
        for name, destination in outputs.items()
    }


def write_rolling_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    school_decision_path: str | Path,
    command: str,
) -> None:
    """Write the tracked rolling summary and Parquet provenance manifests."""
    config_hashes = {
        "project": config_file_hash(project_config_path),
        "school_decision": config_file_hash(school_decision_path),
    }
    source_manifests = [
        ".agent/manifests/institution_outputs_month.json",
        ".agent/manifests/collaboration_edges_month.json",
        ".agent/manifests/work_publication_dates.json",
        ".agent/manifests/work_institutions.json",
        ".agent/manifests/school_decision_contract.json",
    ]
    versions = {
        "rolling_fact_policy": _STAGE_VERSION,
        "entity_scope": _SCOPE,
        "rolling_corpus_views": ",".join(summary["corpus_views"]),
        "rolling_hierarchy_views": ",".join(summary["hierarchy_views"]),
        "rolling_observation_bounds": (
            f"{summary['observation_start_month']}:{summary['observation_end_month']}"
        ),
    }
    write_json_artifact(
        path=summary_path,
        dataset_name="rolling_temporal_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions=versions,
        source_manifests=source_manifests,
        command=command,
    )
    for dataset_name, path in summary["outputs"].items():
        primary_key, required = _definitions()[dataset_name]
        write_parquet_manifest(
            path=path,
            dataset_name=dataset_name,
            primary_key=primary_key,
            required_columns=required,
            year_column=None,
            run_id=run_id,
            config_hashes=config_hashes,
            source_manifests=source_manifests,
            source_versions=versions,
            command=command,
        )


def _resolve_observation_bounds(
    work_dates: Path,
    requested_start: str | None,
    requested_end: str | None,
) -> tuple[str, str]:
    if requested_start is not None:
        _validate_month(requested_start, "observation_start_month")
    if requested_end is not None:
        _validate_month(requested_end, "observation_end_month")
    connection = duckdb.connect()
    try:
        row = connection.execute(
            """
            SELECT min(publication_year), max(publication_year)
            FROM read_parquet(?)
            """,
            [str(work_dates)],
        ).fetchone()
    finally:
        connection.close()
    if row is None or row[0] is None or row[1] is None:
        raise ValueError("publication-date facts contain no supported years")
    start = requested_start or f"{int(row[0]):04d}-01"
    end = requested_end or f"{int(row[1]):04d}-12"
    if _month_index(start) > _month_index(end):
        raise ValueError("observation_start_month must not follow observation_end_month")
    return start, end


def _validate_windows(values: tuple[int, ...]) -> tuple[int, ...]:
    if not values or len(values) != len(set(values)):
        raise ValueError("rolling window lengths must be unique and non-empty")
    result = tuple(sorted(values))
    if any(value not in _SUPPORTED_WINDOWS for value in result):
        raise ValueError("rolling window lengths must be 12, 24, or 36 months")
    return result


def _validate_month(value: str, label: str) -> None:
    if not _MONTH_PATTERN.fullmatch(value):
        raise ValueError(f"{label} must use canonical YYYY-MM")


def _month_index(value: str) -> int:
    _validate_month(value, "month")
    return int(value[:4]) * 12 + int(value[5:7]) - 1


def _month_from_index_sql(expression: str) -> str:
    return (
        f"printf('%04d-%02d', floor(({expression}) / 12)::INTEGER, "
        f"(({expression}) % 12 + 1)::INTEGER)"
    )


def _distinct_values(path: Path, column: str) -> list[str]:
    if column not in {"corpus_view", "hierarchy_view"}:
        raise ValueError("unsupported distinct-value column")
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            f"SELECT DISTINCT {column} FROM read_parquet(?) ORDER BY {column}", [str(path)]
        ).fetchall()
    finally:
        connection.close()
    return [str(row[0]) for row in rows]


def _interval_metrics(path: Path) -> dict[str, int | float]:
    connection = duckdb.connect()
    try:
        row = connection.execute(
            """
            SELECT count(*)::BIGINT,
                   sum(valid_through_month_index - valid_from_month_index + 1)::BIGINT
            FROM read_parquet(?)
            """,
            [str(path)],
        ).fetchone()
    finally:
        connection.close()
    if row is None or not row[0] or not row[1]:
        raise ValueError("rolling edge interval index is empty")
    interval_count = int(row[0])
    represented_endpoints = int(row[1])
    return {
        "edge_interval_count": interval_count,
        "represented_positive_edge_window_count": represented_endpoints,
        "avoided_materialized_edge_window_rows": represented_endpoints - interval_count,
        "endpoint_to_interval_ratio": represented_endpoints / interval_count,
        "interval_file_size_bytes": path.stat().st_size,
    }


def _copy(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    output: Path,
    parameters: list[Any] | None = None,
) -> None:
    connection.execute(
        f"COPY ({query}) TO '{_literal(output)}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)",
        parameters or [],
    )


def _combine_empty_safe(paths: list[Path]) -> str:
    return ", ".join(f"'{_literal(path)}'" for path in paths)


def _configure(connection: duckdb.DuckDBPyConnection, memory_limit: str, threads: int) -> None:
    connection.execute("SET memory_limit = ?", [memory_limit])
    connection.execute("SET threads = ?", [threads])
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET temp_directory = 'data/interim/duckdb-rolling'")


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _sql_string(value: str) -> str:
    return value.replace("'", "''")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
