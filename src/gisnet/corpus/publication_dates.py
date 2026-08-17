"""Publication-date facts and coverage QA for subannual analytical eligibility."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "publication-date-qa-2026-08-17-v1"
_DATE_STATUSES = (
    "exact_valid",
    "missing",
    "malformed",
    "year_conflict",
    "outside_supported_range",
)

_COVERAGE_COUNTS = """
    count(DISTINCT work_id)::BIGINT AS annual_work_count,
    count(DISTINCT work_id) FILTER (
        WHERE has_exact_publication_date
    )::BIGINT AS has_exact_publication_date_work_count,
    count(DISTINCT work_id) FILTER (
        WHERE subannual_date_eligible
    )::BIGINT AS subannual_date_eligible_work_count,
    count(DISTINCT work_id) FILTER (
        WHERE NOT subannual_date_eligible
    )::BIGINT AS annual_only_work_count,
    count(DISTINCT work_id) FILTER (
        WHERE date_quality_status = 'exact_valid'
    )::BIGINT AS exact_valid_work_count,
    count(DISTINCT work_id) FILTER (
        WHERE date_quality_status = 'missing'
    )::BIGINT AS missing_work_count,
    count(DISTINCT work_id) FILTER (
        WHERE date_quality_status = 'malformed'
    )::BIGINT AS malformed_work_count,
    count(DISTINCT work_id) FILTER (
        WHERE date_quality_status = 'year_conflict'
    )::BIGINT AS year_conflict_work_count,
    count(DISTINCT work_id) FILTER (
        WHERE date_quality_status = 'outside_supported_range'
    )::BIGINT AS outside_supported_range_work_count,
    count(DISTINCT work_id) FILTER (
        WHERE subannual_date_eligible AND month(publication_date) = 1
          AND day(publication_date) = 1
    )::BIGINT AS january_first_work_count,
    count(DISTINCT work_id) FILTER (
        WHERE subannual_date_eligible AND day(publication_date) = 1
    )::BIGINT AS first_day_of_month_work_count
"""


def build_publication_date_qa(
    works_path: str | Path,
    work_corpus_path: str | Path,
    work_institutions_path: str | Path,
    work_topics_path: str | Path,
    version_diagnostics_path: str | Path,
    *,
    work_dates_path: str | Path,
    corpus_coverage_path: str | Path,
    year_coverage_path: str | Path,
    institution_coverage_path: str | Path,
    topic_family_coverage_path: str | Path,
    start_year: int,
    end_year: int,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build exact-date facts and recoverable coverage numerators and denominators."""
    if start_year > end_year:
        raise ValueError("publication-date supported start year must not exceed end year")
    if threads < 1:
        raise ValueError("threads must be positive")
    inputs = {
        "works": Path(works_path),
        "work_corpus": Path(work_corpus_path),
        "work_institutions": Path(work_institutions_path),
        "work_topics": Path(work_topics_path),
        "version_diagnostics": Path(version_diagnostics_path),
    }
    for source in inputs.values():
        if not source.is_file():
            raise ValueError(f"publication-date QA input does not exist: {source}")
    outputs = {
        "work_publication_dates": Path(work_dates_path),
        "publication_date_coverage_corpus": Path(corpus_coverage_path),
        "publication_date_coverage_year": Path(year_coverage_path),
        "publication_date_coverage_institution": Path(institution_coverage_path),
        "publication_date_coverage_topic_family": Path(topic_family_coverage_path),
    }
    temporary = {name: path.with_suffix(".parquet.tmp") for name, path in outputs.items()}
    for path in outputs.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    for path in temporary.values():
        path.unlink(missing_ok=True)

    connection = duckdb.connect()
    try:
        _configure(connection, memory_limit, threads)
        connection.execute(
            """
            CREATE TEMP TABLE date_facts AS
            WITH raw AS (
                SELECT
                    work_id,
                    publication_year,
                    publication_date AS publication_date_raw,
                    regexp_full_match(
                        trim(publication_date),
                        '[0-9]{4}-[0-9]{2}-[0-9]{2}'
                    ) AS has_exact_shape,
                    CASE
                        WHEN publication_date IS NOT NULL
                         AND regexp_full_match(
                             trim(publication_date),
                             '[0-9]{4}-[0-9]{2}-[0-9]{2}'
                         )
                        THEN try_strptime(trim(publication_date), '%Y-%m-%d')::DATE
                        ELSE NULL
                    END AS parsed_date
                FROM read_parquet(?)
            ), classified AS (
                SELECT
                    *,
                    has_exact_shape AND parsed_date IS NOT NULL
                        AS has_exact_publication_date,
                    CASE
                        WHEN publication_date_raw IS NULL THEN 'missing'
                        WHEN NOT coalesce(has_exact_shape, false) OR parsed_date IS NULL
                            THEN 'malformed'
                        WHEN year(parsed_date) <> publication_year THEN 'year_conflict'
                        WHEN year(parsed_date) NOT BETWEEN ? AND ?
                            THEN 'outside_supported_range'
                        ELSE 'exact_valid'
                    END AS date_quality_status
                FROM raw
            )
            SELECT
                work_id,
                publication_year,
                publication_date_raw,
                CASE WHEN date_quality_status = 'exact_valid' THEN parsed_date END
                    AS publication_date,
                CASE WHEN date_quality_status = 'exact_valid'
                     THEN strftime(parsed_date, '%Y-%m') END AS publication_month,
                CASE WHEN date_quality_status = 'exact_valid'
                     THEN concat(
                         strftime(parsed_date, '%Y'),
                         '-Q',
                         cast(floor((month(parsed_date) - 1) / 3) + 1 AS INTEGER)
                     ) END AS publication_quarter,
                has_exact_publication_date,
                date_quality_status = 'exact_valid' AS subannual_date_eligible,
                date_quality_status
            FROM classified
            """,
            [str(inputs["works"]), start_year, end_year],
        )
        _copy(
            connection,
            "SELECT * FROM date_facts ORDER BY work_id",
            temporary["work_publication_dates"],
        )
        connection.execute(
            """
            CREATE TEMP TABLE corpus_memberships AS
            SELECT work_id, 'normalized_all' AS corpus_view,
                   'all_normalized_works' AS version_policy
            FROM read_parquet(?)
            UNION ALL
            SELECT work_id, 'strict' AS corpus_view,
                   'primary_representative' AS version_policy
            FROM read_parquet(?) WHERE strict_primary
            UNION ALL
            SELECT work_id, 'broad' AS corpus_view,
                   'primary_representative' AS version_policy
            FROM read_parquet(?) WHERE broad_primary
            """,
            [
                str(inputs["work_corpus"]),
                str(inputs["work_corpus"]),
                str(inputs["work_corpus"]),
            ],
        )
        _copy(
            connection,
            _coverage_query(
                """
                SELECT m.corpus_view, m.version_policy, d.*
                FROM corpus_memberships m
                INNER JOIN date_facts d USING (work_id)
                """,
                ["corpus_view", "version_policy"],
            ),
            temporary["publication_date_coverage_corpus"],
        )
        _copy(
            connection,
            _coverage_query(
                """
                SELECT m.corpus_view, m.version_policy, d.*
                FROM corpus_memberships m
                INNER JOIN date_facts d USING (work_id)
                """,
                ["corpus_view", "version_policy", "publication_year"],
            ),
            temporary["publication_date_coverage_year"],
        )
        _copy(
            connection,
            _coverage_query(
                """
                SELECT
                    CASE WHEN wi.strict_primary THEN 'strict' END AS strict_corpus_view,
                    CASE WHEN wi.broad_primary THEN 'broad' END AS broad_corpus_view,
                    wi.hierarchy_view,
                    wi.institution_id,
                    wi.display_name,
                    wi.country_code,
                    wi.macro_region,
                    wi.is_primary_research_scope,
                    d.*
                FROM read_parquet(?) wi
                INNER JOIN date_facts d USING (work_id)
                """,
                [
                    "corpus_view",
                    "hierarchy_view",
                    "institution_id",
                    "display_name",
                    "country_code",
                    "macro_region",
                    "is_primary_research_scope",
                ],
                projection="""
                    SELECT strict_corpus_view AS corpus_view, * EXCLUDE (
                        strict_corpus_view, broad_corpus_view
                    ) FROM source_rows WHERE strict_corpus_view IS NOT NULL
                    UNION ALL
                    SELECT broad_corpus_view AS corpus_view, * EXCLUDE (
                        strict_corpus_view, broad_corpus_view
                    ) FROM source_rows WHERE broad_corpus_view IS NOT NULL
                """,
            ),
            temporary["publication_date_coverage_institution"],
            [str(inputs["work_institutions"])],
        )
        _copy(
            connection,
            _coverage_query(
                """
                SELECT DISTINCT
                    CASE
                        WHEN c.strict_primary AND t.corpus_membership = 'strict'
                        THEN 'strict'
                    END AS strict_corpus_view,
                    CASE
                        WHEN c.broad_primary
                         AND t.corpus_membership IN ('strict', 'broad_only')
                        THEN 'broad'
                    END AS broad_corpus_view,
                    t.method_family,
                    d.*
                FROM read_parquet(?) t
                INNER JOIN read_parquet(?) c USING (work_id)
                INNER JOIN date_facts d USING (work_id)
                WHERE t.method_family IS NOT NULL
                """,
                ["corpus_view", "method_family"],
                projection="""
                    SELECT strict_corpus_view AS corpus_view, * EXCLUDE (
                        strict_corpus_view, broad_corpus_view
                    ) FROM source_rows WHERE strict_corpus_view IS NOT NULL
                    UNION ALL
                    SELECT broad_corpus_view AS corpus_view, * EXCLUDE (
                        strict_corpus_view, broad_corpus_view
                    ) FROM source_rows WHERE broad_corpus_view IS NOT NULL
                """,
            ),
            temporary["publication_date_coverage_topic_family"],
            [str(inputs["work_topics"]), str(inputs["work_corpus"])],
        )
        version_policy = _version_policy_diagnostics(connection, inputs)
    except BaseException:
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()

    definitions: dict[str, tuple[list[str], set[str], str | None]] = {
        "work_publication_dates": (
            ["work_id"],
            {
                "work_id",
                "publication_year",
                "publication_date_raw",
                "publication_date",
                "publication_month",
                "publication_quarter",
                "has_exact_publication_date",
                "subannual_date_eligible",
                "date_quality_status",
            },
            "publication_year",
        ),
        "publication_date_coverage_corpus": (
            ["corpus_view"],
            _coverage_required({"corpus_view", "version_policy"}),
            None,
        ),
        "publication_date_coverage_year": (
            ["corpus_view", "publication_year"],
            _coverage_required({"corpus_view", "version_policy", "publication_year"}),
            "publication_year",
        ),
        "publication_date_coverage_institution": (
            ["corpus_view", "hierarchy_view", "institution_id"],
            _coverage_required({"corpus_view", "hierarchy_view", "institution_id", "display_name"}),
            None,
        ),
        "publication_date_coverage_topic_family": (
            ["corpus_view", "method_family"],
            _coverage_required({"corpus_view", "method_family"}),
            None,
        ),
    }
    for name, path in temporary.items():
        primary_key, required, year_column = definitions[name]
        parquet_metrics(
            path,
            primary_key=primary_key,
            required_columns=required,
            year_column=year_column,
            memory_limit=memory_limit,
        )
    _validate_temporal_outputs(
        temporary,
        works_path=inputs["works"],
        memory_limit=memory_limit,
    )
    _promote_outputs(outputs, temporary)

    metrics = {
        name: parquet_metrics(
            path,
            primary_key=definitions[name][0],
            required_columns=definitions[name][1],
            year_column=definitions[name][2],
            memory_limit=memory_limit,
        )
        for name, path in outputs.items()
    }
    corpus_rows = _read_corpus_coverage(outputs["publication_date_coverage_corpus"])
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "input_hashes": {name: file_sha256(path) for name, path in inputs.items()},
                "supported_years": [start_year, end_year],
                "date_statuses": list(_DATE_STATUSES),
            }
        ),
        "supported_years": [start_year, end_year],
        "date_statuses": list(_DATE_STATUSES),
        "publication_time_semantics": (
            "Bibliographic publication-time metadata; not collaboration, research, project, "
            "or author-mobility start time."
        ),
        "source_precision_limitation": (
            "The normalized source supplies a full date string but no independent precision flag; "
            "January-first values are retained and measured rather than heuristically discarded."
        ),
        "work_fact_row_count": int(metrics["work_publication_dates"]["row_count"]),
        "corpus_coverage": corpus_rows,
        "version_family_policy": version_policy,
        "outputs": {name: str(path) for name, path in outputs.items()},
        "output_checksums": {
            name: str(output_metrics["checksum_sha256"]) for name, output_metrics in metrics.items()
        },
        "generated_at_utc": _timestamp(),
    }


def write_publication_date_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    school_decision_path: str | Path,
    command: str,
) -> None:
    """Write the QA summary and manifests after all final Parquet outputs validate."""
    config_hashes = {
        "project": config_file_hash(project_config_path),
        "school_decision": config_file_hash(school_decision_path),
    }
    source_manifests = [
        ".agent/manifests/works.json",
        ".agent/manifests/work_corpus.json",
        ".agent/manifests/work_institutions.json",
        ".agent/manifests/work_topics.json",
        ".agent/manifests/work_version_diagnostics.json",
    ]
    source_versions = {"publication_date_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="publication_date_qa_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions=source_versions,
        source_manifests=source_manifests,
        command=command,
    )
    definitions: dict[str, tuple[list[str], set[str], str | None]] = {
        "work_publication_dates": (
            ["work_id"],
            {
                "work_id",
                "publication_year",
                "publication_date_raw",
                "publication_date",
                "publication_month",
                "publication_quarter",
                "has_exact_publication_date",
                "subannual_date_eligible",
                "date_quality_status",
            },
            "publication_year",
        ),
        "publication_date_coverage_corpus": (
            ["corpus_view"],
            _coverage_required({"corpus_view", "version_policy"}),
            None,
        ),
        "publication_date_coverage_year": (
            ["corpus_view", "publication_year"],
            _coverage_required({"corpus_view", "version_policy", "publication_year"}),
            "publication_year",
        ),
        "publication_date_coverage_institution": (
            ["corpus_view", "hierarchy_view", "institution_id"],
            _coverage_required({"corpus_view", "hierarchy_view", "institution_id", "display_name"}),
            None,
        ),
        "publication_date_coverage_topic_family": (
            ["corpus_view", "method_family"],
            _coverage_required({"corpus_view", "method_family"}),
            None,
        ),
    }
    for dataset_name, path in summary["outputs"].items():
        primary_key, required, year_column = definitions[dataset_name]
        write_parquet_manifest(
            path=path,
            dataset_name=dataset_name,
            primary_key=primary_key,
            required_columns=required,
            year_column=year_column,
            run_id=run_id,
            config_hashes=config_hashes,
            source_manifests=source_manifests,
            source_versions=source_versions,
            command=command,
        )


def _coverage_query(
    source_sql: str,
    group_columns: list[str],
    *,
    projection: str | None = None,
) -> str:
    group_sql = ", ".join(_quoted(column) for column in group_columns)
    order_sql = group_sql
    source_projection = projection or "SELECT * FROM source_rows"
    status_sum = " + ".join(f"{status}_work_count" for status in _DATE_STATUSES)
    return f"""
        WITH source_rows AS ({source_sql}), projected AS ({source_projection}), grouped AS (
            SELECT {group_sql}, {_COVERAGE_COUNTS}
            FROM projected
            GROUP BY {group_sql}
        )
        SELECT
            *,
            subannual_date_eligible_work_count::DOUBLE
                / nullif(annual_work_count, 0) AS date_coverage_ratio,
            january_first_work_count::DOUBLE
                / nullif(subannual_date_eligible_work_count, 0)
                AS january_first_source_date_share,
            first_day_of_month_work_count::DOUBLE
                / nullif(subannual_date_eligible_work_count, 0)
                AS first_day_of_month_source_date_share,
            annual_work_count
                - subannual_date_eligible_work_count
                - annual_only_work_count AS coverage_reconciliation_difference,
            annual_work_count - ({status_sum}) AS status_reconciliation_difference,
            annual_work_count = subannual_date_eligible_work_count + annual_only_work_count
                AND annual_work_count = ({status_sum}) AS reconciliation_passed
        FROM grouped
        ORDER BY {order_sql}
    """


def _coverage_required(dimensions: set[str]) -> set[str]:
    return dimensions | {
        "annual_work_count",
        "has_exact_publication_date_work_count",
        "subannual_date_eligible_work_count",
        "annual_only_work_count",
        "exact_valid_work_count",
        "missing_work_count",
        "malformed_work_count",
        "year_conflict_work_count",
        "outside_supported_range_work_count",
        "date_coverage_ratio",
        "coverage_reconciliation_difference",
        "status_reconciliation_difference",
        "reconciliation_passed",
    }


def _validate_temporal_outputs(
    paths: dict[str, Path],
    *,
    works_path: Path,
    memory_limit: str,
) -> None:
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = 1")
        facts = paths["work_publication_dates"]
        fact_invariants = connection.execute(
            """
            SELECT
                count(*),
                count(*) FILTER (
                    WHERE date_quality_status NOT IN (
                        'exact_valid', 'missing', 'malformed',
                        'year_conflict', 'outside_supported_range'
                    )
                ),
                count(*) FILTER (
                    WHERE subannual_date_eligible
                      AND (publication_date IS NULL OR publication_month IS NULL
                           OR publication_quarter IS NULL)
                ),
                count(*) FILTER (
                    WHERE NOT subannual_date_eligible
                      AND (publication_date IS NOT NULL OR publication_month IS NOT NULL
                           OR publication_quarter IS NOT NULL)
                ),
                count(*) FILTER (
                    WHERE publication_month IS NOT NULL
                      AND NOT regexp_full_match(publication_month, '[0-9]{4}-[0-9]{2}')
                ),
                count(*) FILTER (
                    WHERE publication_quarter IS NOT NULL
                      AND NOT regexp_full_match(publication_quarter, '[0-9]{4}-Q[1-4]')
                )
            FROM read_parquet(?)
            """,
            [str(facts)],
        ).fetchone()
        source_count = connection.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(works_path)]
        ).fetchone()
        raw_difference = connection.execute(
            """
            SELECT count(*)
            FROM read_parquet(?) w
            FULL OUTER JOIN read_parquet(?) d USING (work_id)
            WHERE w.work_id IS NULL OR d.work_id IS NULL
               OR w.publication_year IS DISTINCT FROM d.publication_year
               OR w.publication_date IS DISTINCT FROM d.publication_date_raw
            """,
            [str(works_path), str(facts)],
        ).fetchone()
        reconciliation_failures = 0
        for name, path in paths.items():
            if name == "work_publication_dates":
                continue
            row = connection.execute(
                "SELECT count(*) FROM read_parquet(?) WHERE NOT reconciliation_passed",
                [str(path)],
            ).fetchone()
            reconciliation_failures += int(row[0]) if row is not None else 1
    finally:
        connection.close()
    if fact_invariants is None or source_count is None or raw_difference is None:
        raise ValueError("publication-date validation query failed")
    if int(fact_invariants[0]) != int(source_count[0]):
        raise ValueError("publication-date facts do not preserve one row per normalized Work")
    if any(int(value) != 0 for value in fact_invariants[1:]):
        raise ValueError("publication-date fact eligibility or canonical-field invariant failed")
    if int(raw_difference[0]) != 0:
        raise ValueError("publication-date facts do not preserve source date literals")
    if reconciliation_failures:
        raise ValueError("publication-date coverage reconciliation failed")


def _version_policy_diagnostics(
    connection: duckdb.DuckDBPyConnection,
    inputs: dict[str, Path],
) -> dict[str, Any]:
    values = connection.execute(
        """
        WITH version_families AS (
            SELECT
                v.version_family_id,
                count(*) AS member_count,
                count(DISTINCT d.publication_date_raw) AS distinct_source_date_count,
                count(DISTINCT CASE WHEN d.has_exact_publication_date
                    THEN substr(d.publication_date_raw, 1, 7) END)
                    AS distinct_source_month_count,
                count(DISTINCT CASE WHEN d.has_exact_publication_date
                    THEN substr(d.publication_date_raw, 1, 4) END)
                    AS distinct_source_year_count
            FROM read_parquet(?) v
            INNER JOIN date_facts d USING (work_id)
            WHERE v.exact_doi_member_count > 1
            GROUP BY v.version_family_id
        ), corpus_counts AS (
            SELECT
                count(*) FILTER (WHERE strict_primary) AS strict_primary_count,
                count(*) FILTER (WHERE strict_all_versions_sensitivity)
                    AS strict_all_versions_count,
                count(*) FILTER (WHERE broad_primary) AS broad_primary_count,
                count(*) FILTER (WHERE broad_all_versions_sensitivity)
                    AS broad_all_versions_count,
                count(*) FILTER (
                    WHERE strict_primary AND d.subannual_date_eligible
                ) AS strict_primary_subannual_count,
                count(*) FILTER (
                    WHERE strict_all_versions_sensitivity AND d.subannual_date_eligible
                ) AS strict_all_versions_subannual_count,
                count(*) FILTER (
                    WHERE broad_primary AND d.subannual_date_eligible
                ) AS broad_primary_subannual_count,
                count(*) FILTER (
                    WHERE broad_all_versions_sensitivity AND d.subannual_date_eligible
                ) AS broad_all_versions_subannual_count
            FROM read_parquet(?) c
            INNER JOIN date_facts d USING (work_id)
        ), monthly_effect AS (
            SELECT
                d.publication_month,
                count(*) FILTER (WHERE c.strict_all_versions_sensitivity)
                    - count(*) FILTER (WHERE c.strict_primary) AS strict_difference,
                count(*) FILTER (WHERE c.broad_all_versions_sensitivity)
                    - count(*) FILTER (WHERE c.broad_primary) AS broad_difference
            FROM read_parquet(?) c
            INNER JOIN date_facts d USING (work_id)
            WHERE d.subannual_date_eligible
            GROUP BY d.publication_month
        ), version_counts AS (
            SELECT
                count(*) FILTER (WHERE exact_doi_member_count > 1)
                    AS exact_doi_multi_member_work_count,
                count(DISTINCT version_family_id) FILTER (WHERE exact_doi_member_count > 1)
                    AS exact_doi_multi_member_family_count,
                count(*) FILTER (
                    WHERE exact_doi_member_count > 1
                      AND NOT is_recommended_primary_representative
                ) AS nonrepresentative_exact_doi_work_count,
                count(*) FILTER (WHERE ambiguous_possible_family)
                    AS ambiguous_possible_family_work_count
            FROM read_parquet(?)
        )
        SELECT
            version_counts.*,
            (SELECT count(*) FROM version_families
                WHERE distinct_source_date_count > 1) AS multi_source_date_family_count,
            (SELECT count(*) FROM version_families
                WHERE distinct_source_month_count > 1) AS multi_source_month_family_count,
            (SELECT count(*) FROM version_families
                WHERE distinct_source_year_count > 1) AS multi_source_year_family_count,
            corpus_counts.*,
            (SELECT count(*) FROM monthly_effect WHERE strict_difference <> 0)
                AS strict_affected_month_count,
            (SELECT coalesce(max(strict_difference), 0) FROM monthly_effect)
                AS strict_max_monthly_difference,
            (SELECT count(*) FROM monthly_effect WHERE broad_difference <> 0)
                AS broad_affected_month_count,
            (SELECT coalesce(max(broad_difference), 0) FROM monthly_effect)
                AS broad_max_monthly_difference
        FROM version_counts, corpus_counts
        """,
        [
            str(inputs["version_diagnostics"]),
            str(inputs["work_corpus"]),
            str(inputs["work_corpus"]),
            str(inputs["version_diagnostics"]),
        ],
    ).fetchone()
    if values is None:
        raise ValueError("version-family publication-date diagnostic query failed")
    return {
        "policy": (
            "Primary Strict/Broad coverage uses the existing exact-DOI recommended "
            "representative; ambiguous title-only families remain separate."
        ),
        "exact_doi_multi_member_work_count": int(values[0]),
        "exact_doi_multi_member_family_count": int(values[1]),
        "nonrepresentative_exact_doi_work_count": int(values[2]),
        "ambiguous_possible_family_work_count": int(values[3]),
        "multi_source_date_family_count": int(values[4]),
        "multi_source_month_family_count": int(values[5]),
        "multi_source_year_family_count": int(values[6]),
        "strict_primary_work_count": int(values[7]),
        "strict_all_versions_sensitivity_work_count": int(values[8]),
        "strict_all_versions_minus_primary_work_count": int(values[8] - values[7]),
        "broad_primary_work_count": int(values[9]),
        "broad_all_versions_sensitivity_work_count": int(values[10]),
        "broad_all_versions_minus_primary_work_count": int(values[10] - values[9]),
        "strict_primary_subannual_eligible_work_count": int(values[11]),
        "strict_all_versions_subannual_eligible_work_count": int(values[12]),
        "strict_all_versions_minus_primary_subannual_eligible_work_count": int(
            values[12] - values[11]
        ),
        "broad_primary_subannual_eligible_work_count": int(values[13]),
        "broad_all_versions_subannual_eligible_work_count": int(values[14]),
        "broad_all_versions_minus_primary_subannual_eligible_work_count": int(
            values[14] - values[13]
        ),
        "strict_affected_publication_month_count": int(values[15]),
        "strict_max_monthly_work_count_difference": int(values[16]),
        "broad_affected_publication_month_count": int(values[17]),
        "broad_max_monthly_work_count_difference": int(values[18]),
    }


def _promote_outputs(outputs: dict[str, Path], temporary: dict[str, Path]) -> None:
    """Promote one generation and restore every prior output if a replacement fails."""
    backups = {
        name: destination.with_name(f".{destination.name}.rollback.tmp")
        for name, destination in outputs.items()
    }
    for backup in backups.values():
        backup.unlink(missing_ok=True)
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


def _read_corpus_coverage(path: Path) -> list[dict[str, Any]]:
    connection = duckdb.connect()
    try:
        columns = [
            str(row[0])
            for row in connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
            ).fetchall()
        ]
        rows = connection.execute(
            "SELECT * FROM read_parquet(?) ORDER BY corpus_view", [str(path)]
        ).fetchall()
    finally:
        connection.close()
    return [dict(zip(columns, row, strict=True)) for row in rows]


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


def _configure(connection: duckdb.DuckDBPyConnection, memory_limit: str, threads: int) -> None:
    connection.execute("SET memory_limit = ?", [memory_limit])
    connection.execute("SET threads = ?", [threads])
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET temp_directory = 'data/interim/duckdb-publication-dates'")


def _quoted(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
