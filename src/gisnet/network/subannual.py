"""Build sparse school-decision publication-month and publication-quarter facts.

Publication time is bibliographic observation time.  It is not a collaboration,
research, project, or author-mobility start date.
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

_STAGE_VERSION = "subannual-school-facts-2026-08-17-v1"
_SCOPE = "primary_research"
_AGGREGATE_TOLERANCE = 1e-7
_MONTH_PATTERN = re.compile(r"^[0-9]{4}-(0[1-9]|1[0-2])$")
_ACTIVITY_TIERS: tuple[dict[str, int | str | None], ...] = (
    {"activity_tier": "1_to_4_works", "minimum_work_count": 1, "maximum_work_count": 4},
    {"activity_tier": "5_to_19_works", "minimum_work_count": 5, "maximum_work_count": 19},
    {"activity_tier": "20_to_99_works", "minimum_work_count": 20, "maximum_work_count": 99},
    {"activity_tier": "100_plus_works", "minimum_work_count": 100, "maximum_work_count": None},
)


def build_subannual_facts(
    work_publication_dates_path: str | Path,
    work_institutions_path: str | Path,
    *,
    institution_month_path: str | Path,
    institution_quarter_path: str | Path,
    edge_month_path: str | Path,
    edge_quarter_path: str | Path,
    reconciliation_path: str | Path,
    sparsity_path: str | Path,
    corpus_views: list[str] | None = None,
    hierarchy_views: list[str] | None = None,
    warning_institution_count: int = 25,
    exclusion_institution_count: int = 100,
    observation_start_month: str | None = None,
    observation_end_month: str | None = None,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build positive subannual facts and compact, recoverable sparsity diagnostics."""
    dates = Path(work_publication_dates_path)
    memberships = Path(work_institutions_path)
    for source in (dates, memberships):
        if not source.is_file():
            raise ValueError(f"subannual input does not exist: {source}")
    corpora = corpus_views or ["strict", "broad"]
    hierarchies = hierarchy_views or ["organization", "umbrella"]
    if not corpora or not set(corpora).issubset({"strict", "broad"}):
        raise ValueError("corpus views must contain only strict and broad")
    if not hierarchies or not set(hierarchies).issubset({"organization", "umbrella"}):
        raise ValueError("hierarchy views must contain only organization and umbrella")
    if warning_institution_count < 2 or exclusion_institution_count < warning_institution_count:
        raise ValueError("invalid consortium thresholds")

    start_month, end_month = _resolve_observation_bounds(
        dates, observation_start_month, observation_end_month
    )
    eligible_month_count = _month_index(end_month) - _month_index(start_month) + 1
    eligible_quarter_count = (
        _quarter_index(_month_to_quarter(end_month))
        - _quarter_index(_month_to_quarter(start_month))
        + 1
    )

    outputs = {
        "institution_outputs_month": Path(institution_month_path),
        "institution_outputs_quarter": Path(institution_quarter_path),
        "collaboration_edges_month": Path(edge_month_path),
        "collaboration_edges_quarter": Path(edge_quarter_path),
        "subannual_reconciliation": Path(reconciliation_path),
        "subannual_sparsity": Path(sparsity_path),
    }
    for path in outputs.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    temporary = {name: path.with_suffix(".parquet.tmp") for name, path in outputs.items()}
    scratch_root = outputs["institution_outputs_month"].parent
    scratch = {
        "dated_memberships": scratch_root / ".subannual.dated-memberships.parquet.tmp",
        "dated_pairs": scratch_root / ".subannual.dated-pairs.parquet.tmp",
        "annual_institutions": scratch_root / ".subannual.annual-institutions.parquet.tmp",
        "annual_pairs": scratch_root / ".subannual.annual-pairs.parquet.tmp",
    }
    sparsity_shards = {
        (dimension, grain): scratch_root / f".subannual.sparsity-{dimension}-{grain}.parquet.tmp"
        for dimension in ("institution", "edge")
        for grain in ("month", "quarter")
    }
    for path in [*temporary.values(), *scratch.values(), *sparsity_shards.values()]:
        path.unlink(missing_ok=True)

    connection = duckdb.connect()
    try:
        _configure(connection, memory_limit, threads)
        _write_dated_memberships(
            connection,
            dates,
            memberships,
            scratch["dated_memberships"],
            corpora,
            hierarchies,
            start_month,
            end_month,
        )
        _write_dated_pairs(
            connection,
            scratch["dated_memberships"],
            scratch["dated_pairs"],
            warning_institution_count,
            exclusion_institution_count,
        )
        _validate_work_pairs(connection, scratch["dated_pairs"])
        _write_annual_institutions(
            connection,
            dates,
            memberships,
            scratch["annual_institutions"],
            corpora,
            hierarchies,
            start_month,
            end_month,
        )
        _write_annual_pairs(
            connection,
            dates,
            memberships,
            scratch["annual_pairs"],
            corpora,
            hierarchies,
            start_month,
            end_month,
        )
        _write_institution_facts(
            connection,
            scratch["dated_memberships"],
            temporary["institution_outputs_month"],
            "month",
        )
        _write_institution_facts(
            connection,
            scratch["dated_memberships"],
            temporary["institution_outputs_quarter"],
            "quarter",
        )
        _write_edge_facts(
            connection,
            scratch["dated_pairs"],
            temporary["collaboration_edges_month"],
            "month",
        )
        _write_edge_facts(
            connection,
            scratch["dated_pairs"],
            temporary["collaboration_edges_quarter"],
            "quarter",
        )
        _write_reconciliation(
            connection,
            scratch["dated_memberships"],
            scratch["dated_pairs"],
            temporary,
        )
        for dimension, grain in sparsity_shards:
            universe = scratch[
                "annual_institutions" if dimension == "institution" else "annual_pairs"
            ]
            facts = temporary[
                f"institution_outputs_{grain}"
                if dimension == "institution"
                else f"collaboration_edges_{grain}"
            ]
            eligible_periods = eligible_month_count if grain == "month" else eligible_quarter_count
            _write_sparsity_shard(
                connection,
                universe,
                facts,
                sparsity_shards[(dimension, grain)],
                dimension,
                grain,
                start_month,
                end_month,
                eligible_periods,
            )
        shard_literals = ", ".join(f"'{_literal(path)}'" for path in sparsity_shards.values())
        _copy(
            connection,
            f"""
            SELECT * FROM read_parquet([{shard_literals}])
            ORDER BY dimension, temporal_grain, corpus_view, hierarchy_view,
                     macro_region, activity_tier
            """,
            temporary["subannual_sparsity"],
        )
    except BaseException:
        for path in [*temporary.values(), *scratch.values(), *sparsity_shards.values()]:
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
        for path in [*scratch.values(), *sparsity_shards.values()]:
            path.unlink(missing_ok=True)

    metrics = {
        name: parquet_metrics(
            path,
            primary_key=_definitions()[name][0],
            required_columns=_definitions()[name][1],
            year_column="publication_year" if name != "subannual_sparsity" else None,
        )
        for name, path in outputs.items()
    }
    overall_sparsity = _read_overall_sparsity(outputs["subannual_sparsity"])
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "scope": _SCOPE,
        "scope_definition": (
            "All is_primary_research_scope institutions in every configured geography; this "
            "is broader than the released Europe/Asia/Americas annual network scope."
        ),
        "publication_time_semantics": (
            "Bibliographic publication-time observation; not collaboration, research, project, "
            "or author-mobility start time."
        ),
        "observation_start_month": start_month,
        "observation_end_month": end_month,
        "eligible_month_count": eligible_month_count,
        "eligible_quarter_count": eligible_quarter_count,
        "activity_tier_policy": list(_ACTIVITY_TIERS),
        "sparsity_denominator_policy": (
            "Primary-research entities with an exact date inside the requested month bounds, "
            "plus annual-only entities whose publication year overlaps those bounds, multiplied "
            "by the full calendar period count; positive cells are sparse and zeros are derived."
        ),
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "work_publication_dates_sha256": file_sha256(dates),
                "work_institutions_sha256": file_sha256(memberships),
                "scope": _SCOPE,
                "corpus_views": corpora,
                "hierarchy_views": hierarchies,
                "warning_institution_count": warning_institution_count,
                "exclusion_institution_count": exclusion_institution_count,
                "observation_start_month": start_month,
                "observation_end_month": end_month,
                "activity_tiers": _ACTIVITY_TIERS,
            }
        ),
        "row_counts": {name: int(value["row_count"]) for name, value in metrics.items()},
        "checksums_sha256": {
            name: str(value["checksum_sha256"]) for name, value in metrics.items()
        },
        "overall_sparsity": overall_sparsity,
        "outputs": {name: str(path) for name, path in outputs.items()},
        "generated_at_utc": _timestamp(),
    }


def _resolve_observation_bounds(
    dates: Path, requested_start: str | None, requested_end: str | None
) -> tuple[str, str]:
    for value, label in (
        (requested_start, "observation start month"),
        (requested_end, "observation end month"),
    ):
        if value is not None and not _MONTH_PATTERN.fullmatch(value):
            raise ValueError(f"{label} must use canonical YYYY-MM")
    connection = duckdb.connect()
    try:
        row = connection.execute(
            """
            SELECT min(publication_month), max(publication_month)
            FROM read_parquet(?)
            WHERE subannual_date_eligible
            """,
            [str(dates)],
        ).fetchone()
    finally:
        connection.close()
    if row is None or row[0] is None or row[1] is None:
        raise ValueError("no exact-date-eligible publication month is available")
    start = requested_start or str(row[0])
    end = requested_end or str(row[1])
    if _month_index(start) > _month_index(end):
        raise ValueError("observation start month must not follow end month")
    return start, end


def _write_dated_memberships(
    connection: duckdb.DuckDBPyConnection,
    dates: Path,
    memberships: Path,
    output: Path,
    corpora: list[str],
    hierarchies: list[str],
    start_month: str,
    end_month: str,
) -> None:
    selects = []
    parameters: list[str] = []
    for corpus in corpora:
        for hierarchy in hierarchies:
            selects.append(
                f"""
                SELECT * FROM (
                    WITH nodes AS (
                        SELECT DISTINCT
                            d.publication_year,
                            d.publication_month,
                            d.publication_quarter,
                            '{corpus}' AS corpus_view,
                            '{hierarchy}' AS hierarchy_view,
                            wi.work_id,
                            wi.institution_id,
                            wi.display_name,
                            wi.ror_id,
                            wi.country_code,
                            wi.country_name,
                            coalesce(wi.macro_region, 'Unknown') AS macro_region,
                            wi.subregion,
                            wi.normalized_category AS institution_category,
                            wi.analytical_scope,
                            wi.latitude,
                            wi.longitude,
                            wi.method_families
                        FROM read_parquet(?) wi
                        INNER JOIN read_parquet(?) d USING (work_id)
                        WHERE wi.hierarchy_view = '{hierarchy}'
                          AND wi.{corpus}_primary
                          AND wi.is_primary_research_scope
                          AND d.subannual_date_eligible
                          AND d.publication_month BETWEEN ? AND ?
                    )
                    SELECT
                        *,
                        count(*) OVER (PARTITION BY work_id)::INTEGER AS institution_count,
                        count(DISTINCT country_code) OVER (PARTITION BY work_id)::INTEGER
                            AS country_count,
                        count(DISTINCT macro_region) OVER (PARTITION BY work_id)::INTEGER
                            AS region_count
                    FROM nodes
                )
                """
            )
            parameters.extend([str(memberships), str(dates), start_month, end_month])
    _copy(
        connection,
        " UNION ALL ".join(selects)
        + " ORDER BY publication_month, corpus_view, hierarchy_view, work_id, institution_id",
        output,
        parameters,
    )


def _write_dated_pairs(
    connection: duckdb.DuckDBPyConnection,
    memberships: Path,
    output: Path,
    warning_count: int,
    exclusion_count: int,
) -> None:
    _copy(
        connection,
        f"""
        SELECT
            a.publication_year,
            a.publication_month,
            a.publication_quarter,
            a.corpus_view,
            a.hierarchy_view,
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
            a.institution_count,
            1::INTEGER AS full_weight,
            2.0 / (a.institution_count * (a.institution_count - 1)) AS fractional_weight,
            a.institution_count >= {warning_count} AS is_large_consortium,
            a.institution_count >= {exclusion_count}
                AS exceeds_consortium_exclusion_threshold,
            a.method_families
        FROM read_parquet(?) a
        INNER JOIN read_parquet(?) b
          ON a.corpus_view = b.corpus_view
         AND a.hierarchy_view = b.hierarchy_view
         AND a.work_id = b.work_id
         AND a.institution_id < b.institution_id
        WHERE a.institution_count >= 2
        ORDER BY a.publication_month, a.corpus_view, a.hierarchy_view,
                 a.work_id, a.institution_id, b.institution_id
        """,
        output,
        [str(memberships), str(memberships)],
    )


def _activity_tier_sql(column: str) -> str:
    return (
        f"CASE WHEN {column} BETWEEN 1 AND 4 THEN '1_to_4_works' "
        f"WHEN {column} BETWEEN 5 AND 19 THEN '5_to_19_works' "
        f"WHEN {column} BETWEEN 20 AND 99 THEN '20_to_99_works' "
        "ELSE '100_plus_works' END"
    )


def _write_annual_institutions(
    connection: duckdb.DuckDBPyConnection,
    dates: Path,
    memberships: Path,
    output: Path,
    corpora: list[str],
    hierarchies: list[str],
    start_month: str,
    end_month: str,
) -> None:
    selects = []
    parameters: list[str] = []
    for corpus in corpora:
        for hierarchy in hierarchies:
            selects.append(
                f"""
                SELECT DISTINCT
                    '{corpus}' AS corpus_view,
                    '{hierarchy}' AS hierarchy_view,
                    work_id,
                    institution_id,
                    coalesce(macro_region, 'Unknown') AS macro_region
                FROM read_parquet(?) wi
                INNER JOIN read_parquet(?) d USING (work_id)
                WHERE wi.hierarchy_view = '{hierarchy}'
                  AND wi.{corpus}_primary
                  AND wi.is_primary_research_scope
                  AND (
                    (d.subannual_date_eligible AND d.publication_month BETWEEN ? AND ?)
                    OR (
                        NOT d.subannual_date_eligible
                        AND d.publication_year BETWEEN {int(start_month[:4])}
                                                   AND {int(end_month[:4])}
                    )
                  )
                """
            )
            parameters.extend([str(memberships), str(dates), start_month, end_month])
    union_sql = " UNION ALL ".join(selects)
    tier = _activity_tier_sql("count(*)")
    _copy(
        connection,
        f"""
        WITH member_rows AS ({union_sql})
        SELECT
            corpus_view,
            hierarchy_view,
            institution_id,
            any_value(macro_region) AS macro_region,
            count(*)::BIGINT AS lifetime_work_count,
            {tier} AS activity_tier
        FROM member_rows
        GROUP BY corpus_view, hierarchy_view, institution_id
        ORDER BY corpus_view, hierarchy_view, institution_id
        """,
        output,
        parameters,
    )


def _write_annual_pairs(
    connection: duckdb.DuckDBPyConnection,
    dates: Path,
    memberships: Path,
    output: Path,
    corpora: list[str],
    hierarchies: list[str],
    start_month: str,
    end_month: str,
) -> None:
    selects = []
    parameters: list[str] = []
    for corpus in corpora:
        for hierarchy in hierarchies:
            selects.append(
                f"""
                SELECT DISTINCT
                    '{corpus}' AS corpus_view,
                    '{hierarchy}' AS hierarchy_view,
                    work_id,
                    institution_id,
                    coalesce(macro_region, 'Unknown') AS macro_region
                FROM read_parquet(?) wi
                INNER JOIN read_parquet(?) d USING (work_id)
                WHERE wi.hierarchy_view = '{hierarchy}'
                  AND wi.{corpus}_primary
                  AND wi.is_primary_research_scope
                  AND (
                    (d.subannual_date_eligible AND d.publication_month BETWEEN ? AND ?)
                    OR (
                        NOT d.subannual_date_eligible
                        AND d.publication_year BETWEEN {int(start_month[:4])}
                                                   AND {int(end_month[:4])}
                    )
                  )
                """
            )
            parameters.extend([str(memberships), str(dates), start_month, end_month])
    union_sql = " UNION ALL ".join(selects)
    _copy(
        connection,
        f"""
        WITH members AS ({union_sql}), pairs AS (
            SELECT
                a.corpus_view,
                a.hierarchy_view,
                a.work_id,
                a.institution_id AS source_id,
                b.institution_id AS target_id,
                least(a.macro_region, b.macro_region) || ' | ' ||
                    greatest(a.macro_region, b.macro_region) AS macro_region
            FROM members a
            INNER JOIN members b
              ON a.corpus_view = b.corpus_view
             AND a.hierarchy_view = b.hierarchy_view
             AND a.work_id = b.work_id
             AND a.institution_id < b.institution_id
        )
        SELECT
            corpus_view,
            hierarchy_view,
            source_id,
            target_id,
            any_value(macro_region) AS macro_region,
            count(*)::BIGINT AS lifetime_work_count,
            'all' AS activity_tier
        FROM pairs
        GROUP BY corpus_view, hierarchy_view, source_id, target_id
        ORDER BY corpus_view, hierarchy_view, source_id, target_id
        """,
        output,
        parameters,
    )


def _write_institution_facts(
    connection: duckdb.DuckDBPyConnection,
    memberships: Path,
    output: Path,
    grain: str,
) -> None:
    period = f"publication_{grain}"
    _copy(
        connection,
        f"""
        SELECT
            {period},
            min(publication_year)::INTEGER AS publication_year,
            corpus_view,
            hierarchy_view,
            '{_SCOPE}' AS scope,
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
            count(*) FILTER (WHERE country_count >= 2)::BIGINT AS international_work_count,
            count(*) FILTER (WHERE region_count >= 2)::BIGINT AS cross_region_work_count,
            count(*) FILTER (WHERE country_count >= 2)::DOUBLE / count(*)
                AS international_collaboration_share,
            count(*) FILTER (WHERE region_count >= 2)::DOUBLE / count(*)
                AS cross_region_collaboration_share
        FROM read_parquet(?)
        GROUP BY {period}, corpus_view, hierarchy_view, institution_id
        ORDER BY {period}, corpus_view, hierarchy_view, institution_id
        """,
        output,
        [str(memberships)],
    )


def _write_edge_facts(
    connection: duckdb.DuckDBPyConnection,
    pairs: Path,
    output: Path,
    grain: str,
) -> None:
    period = f"publication_{grain}"
    _copy(
        connection,
        f"""
        WITH aggregated AS (
            SELECT
                {period},
                min(publication_year)::INTEGER AS publication_year,
                corpus_view,
                hierarchy_view,
                '{_SCOPE}' AS scope,
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
            GROUP BY {period}, corpus_view, hierarchy_view, source_id, target_id
        )
        SELECT *, len(topic_families)::INTEGER AS distinct_topic_family_count
        FROM aggregated
        ORDER BY {period}, corpus_view, hierarchy_view, source_id, target_id
        """,
        output,
        [str(pairs)],
    )


def _write_reconciliation(
    connection: duckdb.DuckDBPyConnection,
    memberships: Path,
    pairs: Path,
    temporary: dict[str, Path],
) -> None:
    selects: list[str] = []
    parameters: list[str] = []
    for dimension, source in (("institution", memberships), ("edge", pairs)):
        expected_full = "count(*)" if dimension == "institution" else "sum(full_weight)"
        expected_fractional = (
            "count(DISTINCT work_id)" if dimension == "institution" else "sum(fractional_weight)"
        )
        actual_full = "work_count" if dimension == "institution" else "full_count"
        actual_fractional = (
            "fractional_work_count" if dimension == "institution" else "fractional_count"
        )
        actual_observations = "work_count" if dimension == "institution" else "distinct_work_count"
        for grain in ("month", "quarter"):
            fact = temporary[
                f"institution_outputs_{grain}"
                if dimension == "institution"
                else f"collaboration_edges_{grain}"
            ]
            selects.append(
                f"""
                SELECT * FROM (
                    WITH expected AS (
                        SELECT
                            publication_year,
                            corpus_view,
                            hierarchy_view,
                            {expected_full}::DOUBLE AS expected_full_count,
                            {expected_fractional}::DOUBLE AS expected_fractional_count,
                            count(DISTINCT work_id)::BIGINT AS expected_distinct_work_count
                        FROM read_parquet(?)
                        GROUP BY publication_year, corpus_view, hierarchy_view
                    ), actual AS (
                        SELECT
                            publication_year,
                            corpus_view,
                            hierarchy_view,
                            sum({actual_full})::DOUBLE AS actual_full_count,
                            sum({actual_fractional})::DOUBLE AS actual_fractional_count,
                            sum({actual_observations})::BIGINT AS actual_work_observation_count
                        FROM read_parquet(?)
                        GROUP BY publication_year, corpus_view, hierarchy_view
                    )
                    SELECT
                        '{dimension}' AS dimension,
                        '{grain}' AS temporal_grain,
                        expected.publication_year,
                        expected.corpus_view,
                        expected.hierarchy_view,
                        expected.expected_full_count,
                        actual.actual_full_count,
                        actual.actual_full_count - expected.expected_full_count
                            AS full_count_difference,
                        expected.expected_fractional_count,
                        actual.actual_fractional_count,
                        actual.actual_fractional_count - expected.expected_fractional_count
                            AS fractional_count_difference,
                        expected.expected_distinct_work_count,
                        actual.actual_work_observation_count,
                        abs(actual.actual_full_count - expected.expected_full_count)
                            < {_AGGREGATE_TOLERANCE}
                          AND abs(actual.actual_fractional_count
                                  - expected.expected_fractional_count)
                            < {_AGGREGATE_TOLERANCE}
                            AS reconciliation_passed
                    FROM expected
                    INNER JOIN actual USING (publication_year, corpus_view, hierarchy_view)
                )
                """
            )
            parameters.extend([str(source), str(fact)])
    _copy(
        connection,
        " UNION ALL ".join(selects)
        + " ORDER BY dimension, temporal_grain, publication_year, corpus_view, hierarchy_view",
        temporary["subannual_reconciliation"],
        parameters,
    )


def _period_index_sql(period: str, grain: str) -> str:
    if grain == "month":
        return (
            f"cast(substr({period}, 1, 4) AS INTEGER) * 12 "
            f"+ cast(substr({period}, 6, 2) AS INTEGER)"
        )
    return f"cast(substr({period}, 1, 4) AS INTEGER) * 4 + cast(substr({period}, 7, 1) AS INTEGER)"


def _write_sparsity_shard(
    connection: duckdb.DuckDBPyConnection,
    universe: Path,
    facts: Path,
    output: Path,
    dimension: str,
    grain: str,
    start_month: str,
    end_month: str,
    eligible_period_count: int,
) -> None:
    period = f"publication_{grain}"
    work_count = "work_count" if dimension == "institution" else "distinct_work_count"
    join_keys = (
        "u.institution_id = f.institution_id"
        if dimension == "institution"
        else "u.source_id = f.source_id AND u.target_id = f.target_id"
    )
    active_group_keys = "institution_id" if dimension == "institution" else "source_id, target_id"
    index = _period_index_sql(period, grain)
    if dimension == "institution":
        entity_expansion = """
            SELECT *, 'all' AS stratum_region, 'all' AS stratum_tier FROM entity_base
            UNION ALL SELECT *, macro_region, 'all' FROM entity_base
            UNION ALL SELECT *, 'all', activity_tier FROM entity_base
            UNION ALL SELECT *, macro_region, activity_tier FROM entity_base
        """
        cell_expansion = """
            SELECT *, 'all' AS stratum_region, 'all' AS stratum_tier FROM cells
            UNION ALL SELECT *, macro_region, 'all' FROM cells
            UNION ALL SELECT *, 'all', activity_tier FROM cells
            UNION ALL SELECT *, macro_region, activity_tier FROM cells
        """
    else:
        entity_expansion = """
            SELECT *, 'all' AS stratum_region, 'all' AS stratum_tier FROM entity_base
            UNION ALL SELECT *, macro_region, 'all' FROM entity_base
        """
        cell_expansion = """
            SELECT *, 'all' AS stratum_region, 'all' AS stratum_tier FROM cells
            UNION ALL SELECT *, macro_region, 'all' FROM cells
        """
    query = f"""
        WITH active_entities AS (
            SELECT
                corpus_view,
                hierarchy_view,
                {active_group_keys},
                count(*)::BIGINT AS active_periods_per_entity,
                min({index})::BIGINT AS first_period_index,
                max({index})::BIGINT AS last_period_index
            FROM read_parquet(?)
            GROUP BY corpus_view, hierarchy_view, {active_group_keys}
        ), entity_base AS (
            SELECT
                u.*,
                coalesce(a.active_periods_per_entity, 0)::BIGINT AS active_periods_per_entity,
                CASE WHEN a.active_periods_per_entity IS NULL THEN 0
                     ELSE a.last_period_index - a.first_period_index + 1 END::BIGINT
                    AS span_periods_per_entity
            FROM read_parquet(?) u
            LEFT JOIN active_entities a
              ON u.corpus_view = a.corpus_view
             AND u.hierarchy_view = a.hierarchy_view
             AND {join_keys.replace("f.", "a.")}
        ), entity_expanded AS (
            {entity_expansion}
        ), entity_summary AS (
            SELECT
                corpus_view,
                hierarchy_view,
                stratum_region AS macro_region,
                stratum_tier AS activity_tier,
                count(*)::BIGINT AS annual_entity_count,
                count(*) FILTER (WHERE active_periods_per_entity > 0)::BIGINT
                    AS date_eligible_entity_count,
                sum(span_periods_per_entity)::BIGINT AS span_possible_period_count,
                sum(active_periods_per_entity)::BIGINT AS active_period_count,
                quantile_cont(active_periods_per_entity, 0.25) FILTER (
                    WHERE active_periods_per_entity > 0) AS active_period_p25,
                median(active_periods_per_entity) FILTER (
                    WHERE active_periods_per_entity > 0) AS median_active_periods_per_entity,
                quantile_cont(active_periods_per_entity, 0.75) FILTER (
                    WHERE active_periods_per_entity > 0) AS active_period_p75,
                quantile_cont(active_periods_per_entity, 0.90) FILTER (
                    WHERE active_periods_per_entity > 0) AS active_period_p90,
                quantile_cont(active_periods_per_entity, 0.95) FILTER (
                    WHERE active_periods_per_entity > 0) AS active_period_p95,
                quantile_cont(active_periods_per_entity, 0.99) FILTER (
                    WHERE active_periods_per_entity > 0) AS active_period_p99,
                max(active_periods_per_entity)::BIGINT AS maximum_active_periods_per_entity,
                count(*) FILTER (WHERE active_periods_per_entity = 1)::BIGINT
                    AS one_active_period_entity_count,
                count(*) FILTER (WHERE active_periods_per_entity BETWEEN 2 AND 3)::BIGINT
                    AS two_to_three_active_period_entity_count,
                count(*) FILTER (WHERE active_periods_per_entity BETWEEN 4 AND 6)::BIGINT
                    AS four_to_six_active_period_entity_count,
                count(*) FILTER (WHERE active_periods_per_entity BETWEEN 7 AND 12)::BIGINT
                    AS seven_to_twelve_active_period_entity_count,
                count(*) FILTER (WHERE active_periods_per_entity BETWEEN 13 AND 24)::BIGINT
                    AS thirteen_to_twenty_four_active_period_entity_count,
                count(*) FILTER (WHERE active_periods_per_entity >= 25)::BIGINT
                    AS twenty_five_plus_active_period_entity_count
            FROM entity_expanded
            GROUP BY corpus_view, hierarchy_view, stratum_region, stratum_tier
        ), cells AS (
            SELECT
                f.corpus_view,
                f.hierarchy_view,
                u.macro_region,
                u.activity_tier,
                f.{work_count}::BIGINT AS cell_work_count
            FROM read_parquet(?) f
            INNER JOIN read_parquet(?) u
              ON u.corpus_view = f.corpus_view
             AND u.hierarchy_view = f.hierarchy_view
             AND {join_keys}
        ), cell_expanded AS (
            {cell_expansion}
        ), cell_summary AS (
            SELECT
                corpus_view,
                hierarchy_view,
                stratum_region AS macro_region,
                stratum_tier AS activity_tier,
                count(*)::BIGINT AS active_cell_count,
                quantile_cont(cell_work_count, 0.25) AS work_count_per_active_cell_p25,
                median(cell_work_count) AS median_work_count_per_active_cell,
                quantile_cont(cell_work_count, 0.75) AS work_count_per_active_cell_p75,
                quantile_cont(cell_work_count, 0.90) AS work_count_per_active_cell_p90,
                quantile_cont(cell_work_count, 0.95) AS work_count_per_active_cell_p95,
                quantile_cont(cell_work_count, 0.99) AS work_count_per_active_cell_p99,
                max(cell_work_count)::BIGINT AS maximum_work_count_per_active_cell,
                count(*) FILTER (WHERE cell_work_count = 1)::BIGINT
                    AS one_work_active_cell_count,
                count(*) FILTER (WHERE cell_work_count BETWEEN 2 AND 4)::BIGINT
                    AS two_to_four_work_active_cell_count,
                count(*) FILTER (WHERE cell_work_count BETWEEN 5 AND 9)::BIGINT
                    AS five_to_nine_work_active_cell_count,
                count(*) FILTER (WHERE cell_work_count >= 10)::BIGINT
                    AS ten_plus_work_active_cell_count
            FROM cell_expanded
            GROUP BY corpus_view, hierarchy_view, stratum_region, stratum_tier
        )
        SELECT
            '{dimension}' AS dimension,
            '{grain}' AS temporal_grain,
            e.corpus_view,
            e.hierarchy_view,
            e.macro_region,
            e.activity_tier,
            '{start_month}' AS observation_start_month,
            '{end_month}' AS observation_end_month,
            {eligible_period_count}::BIGINT AS eligible_period_count,
            e.annual_entity_count,
            e.date_eligible_entity_count,
            e.annual_entity_count * {eligible_period_count} AS possible_period_count,
            e.active_period_count,
            e.annual_entity_count * {eligible_period_count} - e.active_period_count
                AS zero_period_count,
            (e.annual_entity_count * {eligible_period_count} - e.active_period_count)::DOUBLE
                / nullif(e.annual_entity_count * {eligible_period_count}, 0) AS zero_rate,
            e.span_possible_period_count,
            e.span_possible_period_count - e.active_period_count AS span_zero_period_count,
            (e.span_possible_period_count - e.active_period_count)::DOUBLE
                / nullif(e.span_possible_period_count, 0) AS span_zero_rate,
            e.active_period_p25,
            e.median_active_periods_per_entity,
            e.active_period_p75,
            e.active_period_p90,
            e.active_period_p95,
            e.active_period_p99,
            e.maximum_active_periods_per_entity,
            e.one_active_period_entity_count,
            e.two_to_three_active_period_entity_count,
            e.four_to_six_active_period_entity_count,
            e.seven_to_twelve_active_period_entity_count,
            e.thirteen_to_twenty_four_active_period_entity_count,
            e.twenty_five_plus_active_period_entity_count,
            c.work_count_per_active_cell_p25,
            c.median_work_count_per_active_cell,
            c.work_count_per_active_cell_p75,
            c.work_count_per_active_cell_p90,
            c.work_count_per_active_cell_p95,
            c.work_count_per_active_cell_p99,
            coalesce(c.maximum_work_count_per_active_cell, 0)::BIGINT
                AS maximum_work_count_per_active_cell,
            coalesce(c.one_work_active_cell_count, 0)::BIGINT AS one_work_active_cell_count,
            coalesce(c.two_to_four_work_active_cell_count, 0)::BIGINT
                AS two_to_four_work_active_cell_count,
            coalesce(c.five_to_nine_work_active_cell_count, 0)::BIGINT
                AS five_to_nine_work_active_cell_count,
            coalesce(c.ten_plus_work_active_cell_count, 0)::BIGINT
                AS ten_plus_work_active_cell_count
        FROM entity_summary e
        LEFT JOIN cell_summary c
          USING (corpus_view, hierarchy_view, macro_region, activity_tier)
        ORDER BY e.corpus_view, e.hierarchy_view, e.macro_region, e.activity_tier
    """
    _copy(connection, query, output, [str(facts), str(universe), str(facts), str(universe)])


def _validate_work_pairs(connection: duckdb.DuckDBPyConnection, pairs: Path) -> None:
    row = connection.execute(
        """
        WITH per_work AS (
            SELECT
                corpus_view,
                hierarchy_view,
                work_id,
                any_value(institution_count)::BIGINT AS institution_count,
                count(*)::BIGINT AS generated_pair_count,
                sum(fractional_weight) AS fractional_weight_sum,
                min(source_id < target_id) AS ordered_pairs
            FROM read_parquet(?)
            GROUP BY corpus_view, hierarchy_view, work_id
        )
        SELECT
            count(*) FILTER (
                WHERE generated_pair_count <> institution_count * (institution_count - 1) / 2
            ),
            count(*) FILTER (WHERE abs(fractional_weight_sum - 1.0) > 1e-10),
            count(*) FILTER (WHERE NOT ordered_pairs),
            max(abs(fractional_weight_sum - 1.0))
        FROM per_work
        """,
        [str(pairs)],
    ).fetchone()
    if row is None or int(row[0]) or int(row[1]) or int(row[2]):
        raise ValueError("subannual per-Work pair arithmetic failed")


def _definitions() -> dict[str, tuple[list[str], set[str]]]:
    institution_required = {
        "publication_year",
        "corpus_view",
        "hierarchy_view",
        "scope",
        "institution_id",
        "work_count",
        "fractional_work_count",
    }
    edge_required = {
        "publication_year",
        "corpus_view",
        "hierarchy_view",
        "scope",
        "source_id",
        "target_id",
        "full_count",
        "fractional_count",
    }
    return {
        "institution_outputs_month": (
            ["publication_month", "corpus_view", "hierarchy_view", "institution_id"],
            institution_required | {"publication_month"},
        ),
        "institution_outputs_quarter": (
            ["publication_quarter", "corpus_view", "hierarchy_view", "institution_id"],
            institution_required | {"publication_quarter"},
        ),
        "collaboration_edges_month": (
            ["publication_month", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            edge_required | {"publication_month"},
        ),
        "collaboration_edges_quarter": (
            ["publication_quarter", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            edge_required | {"publication_quarter"},
        ),
        "subannual_reconciliation": (
            ["dimension", "temporal_grain", "publication_year", "corpus_view", "hierarchy_view"],
            {
                "dimension",
                "temporal_grain",
                "publication_year",
                "full_count_difference",
                "fractional_count_difference",
                "reconciliation_passed",
            },
        ),
        "subannual_sparsity": (
            [
                "dimension",
                "temporal_grain",
                "corpus_view",
                "hierarchy_view",
                "macro_region",
                "activity_tier",
            ],
            {
                "dimension",
                "temporal_grain",
                "possible_period_count",
                "active_period_count",
                "zero_period_count",
                "zero_rate",
            },
        ),
    }


def _validate_outputs(temporary: dict[str, Path]) -> None:
    definitions = _definitions()
    for name, path in temporary.items():
        key, required = definitions[name]
        parquet_metrics(
            path,
            primary_key=key,
            required_columns=required,
            year_column="publication_year" if name != "subannual_sparsity" else None,
        )
    connection = duckdb.connect()
    try:
        reconciliation = connection.execute(
            """
            SELECT
                count(*) FILTER (WHERE NOT reconciliation_passed),
                max(abs(full_count_difference)),
                max(abs(fractional_count_difference))
            FROM read_parquet(?)
            """,
            [str(temporary["subannual_reconciliation"])],
        ).fetchone()
        sparsity = connection.execute(
            """
            SELECT count(*) FILTER (
                WHERE possible_period_count <> active_period_count + zero_period_count
                   OR abs(zero_rate - zero_period_count::DOUBLE
                       / nullif(possible_period_count, 0)) > 1e-12
                   OR active_period_count > possible_period_count
            )
            FROM read_parquet(?)
            """,
            [str(temporary["subannual_sparsity"])],
        ).fetchone()
        period_errors = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM read_parquet(?)
               WHERE publication_year <> cast(substr(publication_month, 1, 4) AS INTEGER)),
              (SELECT count(*) FROM read_parquet(?)
               WHERE publication_year <> cast(substr(publication_quarter, 1, 4) AS INTEGER)),
              (SELECT count(*) FROM read_parquet(?)
               WHERE publication_year <> cast(substr(publication_month, 1, 4) AS INTEGER)
                  OR source_id >= target_id),
              (SELECT count(*) FROM read_parquet(?)
               WHERE publication_year <> cast(substr(publication_quarter, 1, 4) AS INTEGER)
                  OR source_id >= target_id)
            """,
            [
                str(temporary["institution_outputs_month"]),
                str(temporary["institution_outputs_quarter"]),
                str(temporary["collaboration_edges_month"]),
                str(temporary["collaboration_edges_quarter"]),
            ],
        ).fetchone()
        institution_grain = _grain_reconciliation_error(
            connection,
            temporary["institution_outputs_month"],
            temporary["institution_outputs_quarter"],
            "institution_id",
            [
                "work_count",
                "fractional_work_count",
                "collaborative_work_count",
                "single_institution_work_count",
                "international_work_count",
                "cross_region_work_count",
            ],
        )
        edge_grain = _grain_reconciliation_error(
            connection,
            temporary["collaboration_edges_month"],
            temporary["collaboration_edges_quarter"],
            "source_id, target_id",
            [
                "full_count",
                "fractional_count",
                "distinct_work_count",
                "large_consortium_work_count",
                "excluded_threshold_work_count",
            ],
        )
    finally:
        connection.close()
    if reconciliation is None or int(reconciliation[0]):
        raise ValueError("subannual annual-source reconciliation failed")
    if (
        float(reconciliation[1] or 0.0) > _AGGREGATE_TOLERANCE
        or float(reconciliation[2] or 0.0) > _AGGREGATE_TOLERANCE
    ):
        raise ValueError("subannual full/fractional totals failed reconciliation")
    if sparsity is None or int(sparsity[0]):
        raise ValueError("subannual sparsity denominators failed reconciliation")
    if period_errors is None or any(int(value) for value in period_errors):
        raise ValueError("subannual period keys or canonical edge ordering are invalid")
    if institution_grain > _AGGREGATE_TOLERANCE or edge_grain > _AGGREGATE_TOLERANCE:
        raise ValueError("monthly facts do not reconcile with quarterly facts")


def _grain_reconciliation_error(
    connection: duckdb.DuckDBPyConnection,
    monthly: Path,
    quarterly: Path,
    entity_keys: str,
    metrics: list[str],
) -> float:
    monthly_metrics = ", ".join(f"sum({metric}) AS {metric}" for metric in metrics)
    comparisons = " + ".join(
        f"abs(coalesce(m.{metric}, 0) - coalesce(q.{metric}, 0))" for metric in metrics
    )
    keys = [part.strip() for part in entity_keys.split(",")]
    group_keys = ", ".join(["publication_quarter", "corpus_view", "hierarchy_view", *keys])
    using_keys = ", ".join(["publication_quarter", "corpus_view", "hierarchy_view", *keys])
    row = connection.execute(
        f"""
        WITH m AS (
            SELECT
                substr(publication_month, 1, 4) || '-Q' ||
                    cast(ceil(cast(substr(publication_month, 6, 2) AS INTEGER) / 3.0)
                        AS INTEGER) AS publication_quarter,
                corpus_view,
                hierarchy_view,
                {entity_keys},
                {monthly_metrics}
            FROM read_parquet(?)
            GROUP BY {group_keys}
        ), q AS (
            SELECT publication_quarter, corpus_view, hierarchy_view, {entity_keys},
                   {", ".join(metrics)}
            FROM read_parquet(?)
        )
        SELECT max({comparisons})
        FROM m FULL OUTER JOIN q USING ({using_keys})
        """,
        [str(monthly), str(quarterly)],
    ).fetchone()
    return float(row[0] or 0.0) if row is not None else float("inf")


def _promote_outputs(outputs: dict[str, Path], temporary: dict[str, Path]) -> None:
    backups = {
        name: destination.with_name(f".{destination.name}.rollback.tmp")
        for name, destination in outputs.items()
    }
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
    """Restore any prior generation left by an interrupted grouped promotion."""
    for name, backup in backups.items():
        if not backup.exists():
            continue
        outputs[name].unlink(missing_ok=True)
        os.replace(backup, outputs[name])


def _read_overall_sparsity(path: Path) -> list[dict[str, Any]]:
    connection = duckdb.connect()
    try:
        columns = [
            str(row[0])
            for row in connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
            ).fetchall()
        ]
        rows = connection.execute(
            """
            SELECT * FROM read_parquet(?)
            WHERE macro_region = 'all' AND activity_tier = 'all'
            ORDER BY dimension, temporal_grain, corpus_view, hierarchy_view
            """,
            [str(path)],
        ).fetchall()
    finally:
        connection.close()
    return [dict(zip(columns, row, strict=True)) for row in rows]


def write_subannual_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    school_decision_path: str | Path,
    command: str,
) -> None:
    config_hashes = {
        "project": config_file_hash(project_config_path),
        "school_decision": config_file_hash(school_decision_path),
    }
    source_manifests = [
        ".agent/manifests/work_publication_dates.json",
        ".agent/manifests/work_institutions.json",
        ".agent/manifests/school_decision_contract.json",
    ]
    source_versions = {
        "subannual_fact_policy": _STAGE_VERSION,
        "entity_scope": _SCOPE,
    }
    write_json_artifact(
        path=summary_path,
        dataset_name="subannual_temporal_summary",
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
        primary_key, required = _definitions()[dataset_name]
        write_parquet_manifest(
            path=path,
            dataset_name=dataset_name,
            primary_key=primary_key,
            required_columns=required,
            year_column=("publication_year" if dataset_name != "subannual_sparsity" else None),
            run_id=run_id,
            config_hashes=config_hashes,
            source_manifests=source_manifests,
            source_versions=source_versions,
            command=command,
        )


def _month_index(value: str) -> int:
    return int(value[:4]) * 12 + int(value[5:7]) - 1


def _month_to_quarter(value: str) -> str:
    return f"{value[:4]}-Q{(int(value[5:7]) - 1) // 3 + 1}"


def _quarter_index(value: str) -> int:
    return int(value[:4]) * 4 + int(value[6]) - 1


def _copy(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    output: Path,
    parameters: list[str] | None = None,
) -> None:
    connection.execute(
        f"COPY ({query}) TO '{_literal(output)}' "
        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)",
        parameters or [],
    )


def _configure(connection: duckdb.DuckDBPyConnection, memory_limit: str, threads: int) -> None:
    connection.execute("SET memory_limit = ?", [memory_limit])
    connection.execute("SET threads = ?", [threads])
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET temp_directory = 'data/interim/duckdb-subannual'")


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
