"""Build complete school profiles without conflating analytical evidence layers."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "school-profiles-2026-08-28-v1"
_SUPPORTED_WINDOWS = (12, 24, 36)
_PROFILE_PRIMARY_KEY = ["canonical_school_id", "corpus_view", "window_months"]
_TOPIC_PRIMARY_KEY = [
    "canonical_school_id",
    "corpus_view",
    "window_months",
    "topic_family",
]
_PROFILE_REQUIRED_COLUMNS = {
    "canonical_school_id",
    "display_name",
    "country_code",
    "country_name",
    "macro_region",
    "subregion",
    "institution_category",
    "identity_status",
    "identity_resolution_confidence",
    "identity_quality_flags",
    "corpus_view",
    "hierarchy_view",
    "window_start",
    "window_end",
    "window_months",
    "observed_month_count",
    "eligible_month_count",
    "coverage_ratio",
    "is_complete_window",
    "profile_support_status",
    "full_work_count",
    "work_count",
    "fractional_work_count",
    "recent_12m_work_count",
    "recent_24m_work_count",
    "recent_36m_work_count",
    "international_collaboration_share",
    "cross_region_collaboration_share",
    "partner_institution_count",
    "partner_country_count",
    "fractional_collaboration_strength",
    "repeat_partner_count",
    "repeat_partner_ratio",
    "effective_partner_count",
    "top_partner_ids",
    "top_partner_names",
    "top_partner_fractional_counts",
    "topic_family_count",
    "top_topic_family",
    "top_topic_family_share",
    "topic_profile_support_status",
    "rolling_12m_activity_change",
    "rolling_12m_fractional_activity_change",
    "momentum_support_status",
    "annual_graph_year",
    "annual_graph_boundary",
    "annual_network_support_status",
    "degree",
    "pagerank",
    "betweenness",
    "betweenness_method",
    "bridge_score",
    "community_id",
    "community_continuity_id",
    "community_status",
    "citation_flow_year",
    "citation_flow_boundary",
    "citation_flow_support_status",
    "citation_flow_in_full",
    "citation_flow_in_fractional",
    "citation_flow_fractional_in_strength",
    "citation_flow_out_full",
    "citation_flow_out_fractional",
    "topic_similarity_year",
    "topic_similarity_boundary",
    "topic_similarity_support_status",
    "topic_similarity_neighbor_count",
    "topic_similarity_maximum",
    "topic_similarity_mean",
    "topic_similarity_top_neighbor_ids",
    "date_coverage_ratio",
    "date_coverage_status",
    "date_coverage_basis",
    "quality_flags",
    "publication_time_interpretation",
}
_TOPIC_REQUIRED_COLUMNS = {
    "canonical_school_id",
    "display_name",
    "country_code",
    "macro_region",
    "corpus_view",
    "hierarchy_view",
    "window_start",
    "window_end",
    "window_months",
    "topic_family",
    "topic_weight",
    "contributing_work_count",
    "topic_family_share",
    "global_baseline_share",
    "specialization_lift_global",
    "macro_region_baseline_share",
    "specialization_lift_macro_region",
    "country_baseline_share",
    "specialization_lift_country",
    "topic_rank",
    "provisional_topic_registry",
    "topic_profile_support_status",
}


def build_school_profiles(
    school_index_path: str | Path,
    identities_path: str | Path,
    institution_rolling_path: str | Path,
    partner_index_path: str | Path,
    nodes_year_path: str | Path,
    citation_edges_year_path: str | Path,
    topic_vectors_year_path: str | Path,
    topic_similarity_edges_year_path: str | Path,
    work_institutions_path: str | Path,
    work_publication_dates_path: str | Path,
    work_topics_path: str | Path,
    *,
    profiles_path: str | Path,
    topic_profiles_path: str | Path,
    communities_path: str | Path | None = None,
    community_continuity_path: str | Path | None = None,
    corpus_views: tuple[str, ...] = ("strict", "broad"),
    window_months: tuple[int, ...] = _SUPPORTED_WINDOWS,
    top_partner_count: int = 10,
    top_similarity_count: int = 10,
    memory_limit: str = "4GB",
) -> dict[str, Any]:
    """Build latest selectable-window profiles for every eligible school.

    Rolling co-authorship and Topic profiles use exact publication months. Annual graph,
    citation-flow, and Topic-similarity evidence remains explicitly annual and independently
    labelled. The function intentionally refuses a stale organization rollup after any school
    collapse because centrality and cosine similarity are not composable.
    """
    if not corpus_views or not set(corpus_views).issubset({"strict", "broad"}):
        raise ValueError("corpus_views must contain strict and/or broad")
    if not window_months or not set(window_months).issubset(set(_SUPPORTED_WINDOWS)):
        raise ValueError("window_months must contain only 12, 24, or 36")
    if top_partner_count <= 0 or top_similarity_count <= 0:
        raise ValueError("top-partner and Topic-similarity retention counts must be positive")

    sources = {
        "school_index": Path(school_index_path),
        "school_identities": Path(identities_path),
        "institution_outputs_rolling": Path(institution_rolling_path),
        "school_partner_index": Path(partner_index_path),
        "nodes_year": Path(nodes_year_path),
        "citation_edges_year": Path(citation_edges_year_path),
        "institution_topic_vectors_year": Path(topic_vectors_year_path),
        "topic_similarity_edges_year": Path(topic_similarity_edges_year_path),
        "work_institutions": Path(work_institutions_path),
        "work_publication_dates": Path(work_publication_dates_path),
        "work_topics": Path(work_topics_path),
    }
    optional_sources = {
        "communities_year": Path(communities_path) if communities_path is not None else None,
        "community_continuity_year": (
            Path(community_continuity_path) if community_continuity_path is not None else None
        ),
    }
    for name, source in sources.items():
        if not source.is_file():
            raise ValueError(f"school profile input does not exist ({name}): {source}")
    for name, optional_source in optional_sources.items():
        if optional_source is not None and not optional_source.is_file():
            raise ValueError(f"school profile input does not exist ({name}): {optional_source}")

    _validate_input_columns(sources)
    _validate_identity_equivalence(sources["school_index"], sources["school_identities"])

    destinations = {
        "school_profiles": Path(profiles_path),
        "school_topic_profiles": Path(topic_profiles_path),
    }
    temporary = {name: path.with_suffix(".parquet.tmp") for name, path in destinations.items()}
    for path in [*destinations.values(), *temporary.values()]:
        path.parent.mkdir(parents=True, exist_ok=True)
    for path in temporary.values():
        path.unlink(missing_ok=True)

    connection = duckdb.connect()
    try:
        connection.execute(f"SET memory_limit='{_sql_string(memory_limit)}'")
        connection.execute("SET threads=1")
        _create_endpoints(connection, sources, corpus_views, window_months)
        endpoint_rows = connection.execute(
            """
            SELECT corpus_view, window_months, window_start, window_end
            FROM profile_endpoints
            ORDER BY corpus_view, window_months
            """
        ).fetchall()
        if len(endpoint_rows) != len(corpus_views) * len(window_months):
            raise ValueError("rolling facts do not contain every requested corpus/window endpoint")

        _create_recent_and_momentum_tables(connection, sources)
        _create_partner_table(connection, sources, top_partner_count)
        _create_annual_context_tables(
            connection,
            sources,
            optional_sources,
            top_similarity_count,
        )
        _create_topic_profiles(connection, sources)
        connection.execute(
            f"""
            COPY (
                SELECT * FROM topic_profiles
                ORDER BY corpus_view, window_months, canonical_school_id, topic_rank,
                         topic_family
            ) TO '{_literal(temporary["school_topic_profiles"])}'
              (FORMAT PARQUET, COMPRESSION ZSTD)
            """
        )
        _create_profile_output(connection, sources, temporary["school_profiles"])
        _validate_generated_outputs(
            connection,
            temporary,
            sources["school_index"],
            len(endpoint_rows),
        )
    except BaseException:
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()

    for name in ("school_profiles", "school_topic_profiles"):
        os.replace(temporary[name], destinations[name])

    metrics = {
        "school_profiles": parquet_metrics(
            destinations["school_profiles"],
            primary_key=_PROFILE_PRIMARY_KEY,
            required_columns=_PROFILE_REQUIRED_COLUMNS,
        ),
        "school_topic_profiles": parquet_metrics(
            destinations["school_topic_profiles"],
            primary_key=_TOPIC_PRIMARY_KEY,
            required_columns=_TOPIC_REQUIRED_COLUMNS,
        ),
    }
    counts = _coverage_counts(
        destinations["school_profiles"], destinations["school_topic_profiles"]
    )
    input_hashes = {name: file_sha256(path) for name, path in sources.items()}
    input_hashes.update(
        {name: file_sha256(path) for name, path in optional_sources.items() if path is not None}
    )
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "profile_policy": (
            "One latest exact rolling row per eligible school, corpus, and requested width; "
            "no-activity and unavailable annual evidence remain explicit rather than imputed."
        ),
        "layer_policy": (
            "Co-authorship, annual citation flow, and annual Topic similarity remain separate "
            "evidence layers and are never merged into a scientific score."
        ),
        "topic_weight_policy": (
            "Topic score divided by the distinct primary-scope school count on each Work, then "
            "aggregated by provisional method family within the exact rolling window."
        ),
        "annual_context_policy": (
            "Annual graph, citation-flow, community, and Topic-similarity values use the latest "
            "complete annual graph year not later than the rolling endpoint."
        ),
        "corpus_views": list(corpus_views),
        "window_months": list(window_months),
        "endpoints": [
            {
                "corpus_view": str(row[0]),
                "window_months": int(row[1]),
                "window_start": str(row[2]),
                "window_end": str(row[3]),
            }
            for row in endpoint_rows
        ],
        "profile_row_count": metrics["school_profiles"]["row_count"],
        "topic_profile_row_count": metrics["school_topic_profiles"]["row_count"],
        **counts,
        "row_counts": {name: value["row_count"] for name, value in metrics.items()},
        "checksums_sha256": {name: value["checksum_sha256"] for name, value in metrics.items()},
        "outputs": {name: str(path) for name, path in destinations.items()},
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "inputs": input_hashes,
                "corpus_views": list(corpus_views),
                "window_months": list(window_months),
                "top_partner_count": top_partner_count,
                "top_similarity_count": top_similarity_count,
            }
        ),
        "generated_at_utc": _timestamp(),
    }


def write_school_profile_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    school_decision_path: str | Path,
    topic_registry_path: str | Path,
    command: str,
) -> None:
    """Write the profile summary and both validated dataset manifests."""
    config_hashes = {
        "project": config_file_hash(project_config_path),
        "school_decision": config_file_hash(school_decision_path),
        "topic_registry": config_file_hash(topic_registry_path),
    }
    source_manifests = [
        ".agent/manifests/school_index.json",
        ".agent/manifests/school_identities.json",
        ".agent/manifests/institution_outputs_rolling.json",
        ".agent/manifests/school_partner_index.json",
        ".agent/manifests/nodes_year.json",
        ".agent/manifests/communities_year.json",
        ".agent/manifests/community_continuity_year.json",
        ".agent/manifests/citation_edges_year.json",
        ".agent/manifests/institution_topic_vectors_year.json",
        ".agent/manifests/topic_similarity_edges_year.json",
        ".agent/manifests/work_institutions.json",
        ".agent/manifests/work_publication_dates.json",
        ".agent/manifests/work_topics.json",
    ]
    source_versions = {"school_profile_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="school_profile_summary",
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
        path=summary["outputs"]["school_profiles"],
        dataset_name="school_profiles",
        primary_key=_PROFILE_PRIMARY_KEY,
        required_columns=_PROFILE_REQUIRED_COLUMNS,
        year_column=None,
        run_id=run_id,
        config_hashes=config_hashes,
        source_manifests=source_manifests,
        source_versions=source_versions,
        command=command,
    )
    write_parquet_manifest(
        path=summary["outputs"]["school_topic_profiles"],
        dataset_name="school_topic_profiles",
        primary_key=_TOPIC_PRIMARY_KEY,
        required_columns=_TOPIC_REQUIRED_COLUMNS,
        year_column=None,
        run_id=run_id,
        config_hashes=config_hashes,
        source_manifests=source_manifests,
        source_versions=source_versions,
        command=command,
    )


def _create_endpoints(
    connection: duckdb.DuckDBPyConnection,
    sources: dict[str, Path],
    corpus_views: tuple[str, ...],
    window_months: tuple[int, ...],
) -> None:
    rolling = sources["institution_outputs_rolling"]
    scope_filter = "AND scope = 'primary_research'" if "scope" in _columns(rolling) else ""
    connection.execute(
        f"""
        CREATE TEMP TABLE profile_endpoints AS
        SELECT corpus_view, window_months, window_start, window_end,
               observed_month_count, eligible_month_count, coverage_ratio,
               is_complete_window
        FROM read_parquet('{_literal(rolling)}')
        WHERE hierarchy_view = 'organization'
          AND corpus_view IN ({_quoted_values(corpus_views)})
          AND window_months IN ({_integer_values(window_months)})
          {scope_filter}
        QUALIFY row_number() OVER (
            PARTITION BY corpus_view, window_months ORDER BY window_end DESC
        ) = 1
        """
    )


def _create_recent_and_momentum_tables(
    connection: duckdb.DuckDBPyConnection,
    sources: dict[str, Path],
) -> None:
    rolling = sources["institution_outputs_rolling"]
    scope_filter = "AND r.scope = 'primary_research'" if "scope" in _columns(rolling) else ""
    connection.execute(
        f"""
        CREATE TEMP TABLE selected_rolling AS
        SELECT r.*
        FROM read_parquet('{_literal(rolling)}') r
        JOIN profile_endpoints e USING (corpus_view, window_months, window_end)
        WHERE r.hierarchy_view = 'organization' {scope_filter}
        """
    )
    connection.execute(
        """
        CREATE TEMP TABLE recent_counts AS
        SELECT institution_id, corpus_view,
               max(work_count) FILTER (WHERE window_months = 12)::BIGINT
                   AS recent_12m_work_count,
               max(work_count) FILTER (WHERE window_months = 24)::BIGINT
                   AS recent_24m_work_count,
               max(work_count) FILTER (WHERE window_months = 36)::BIGINT
                   AS recent_36m_work_count
        FROM selected_rolling
        GROUP BY institution_id, corpus_view
        """
    )
    endpoint_scope = "AND scope = 'primary_research'" if "scope" in _columns(rolling) else ""
    connection.execute(
        f"""
        CREATE TEMP TABLE momentum AS
        WITH endpoint AS (
            SELECT corpus_view, max(window_end) AS current_end
            FROM read_parquet('{_literal(rolling)}')
            WHERE hierarchy_view = 'organization' AND window_months = 12
              AND corpus_view IN (SELECT DISTINCT corpus_view FROM profile_endpoints)
              {endpoint_scope}
            GROUP BY corpus_view
        ), values_by_school AS (
            SELECT r.institution_id, r.corpus_view, e.current_end,
                   max(r.work_count) FILTER (WHERE r.window_end = e.current_end)
                       AS current_work_count,
                   max(r.fractional_work_count) FILTER (WHERE r.window_end = e.current_end)
                       AS current_fractional_work_count,
                   max(r.work_count) FILTER (
                       WHERE r.window_end = strftime(
                           strptime(e.current_end || '-01', '%Y-%m-%d') - interval 12 month,
                           '%Y-%m'
                       )
                   ) AS prior_work_count,
                   max(r.fractional_work_count) FILTER (
                       WHERE r.window_end = strftime(
                           strptime(e.current_end || '-01', '%Y-%m-%d') - interval 12 month,
                           '%Y-%m'
                       )
                   ) AS prior_fractional_work_count
            FROM read_parquet('{_literal(rolling)}') r
            JOIN endpoint e USING (corpus_view)
            WHERE r.hierarchy_view = 'organization' AND r.window_months = 12
              {scope_filter}
            GROUP BY r.institution_id, r.corpus_view, e.current_end
        )
        SELECT *,
               CASE WHEN prior_work_count > 0
                    THEN (current_work_count - prior_work_count)::DOUBLE / prior_work_count
               END AS rolling_12m_activity_change,
               CASE WHEN prior_fractional_work_count > 0
                    THEN (current_fractional_work_count - prior_fractional_work_count)
                         / prior_fractional_work_count
               END AS rolling_12m_fractional_activity_change,
               CASE
                   WHEN current_work_count IS NULL THEN 'no_current_12m_activity'
                   WHEN prior_work_count IS NULL THEN 'prior_window_not_observed'
                   WHEN prior_work_count = 0 THEN 'prior_window_zero'
                   ELSE 'supported'
               END AS momentum_support_status
        FROM values_by_school
        """
    )


def _create_partner_table(
    connection: duckdb.DuckDBPyConnection,
    sources: dict[str, Path],
    top_partner_count: int,
) -> None:
    connection.execute(
        f"""
        CREATE TEMP TABLE profile_partners AS
        SELECT p.school_id, p.corpus_view, p.window_months, p.window_end,
               list(p.partner_id ORDER BY p.partner_rank, p.partner_id)
                   AS top_partner_ids,
               list(p.partner_name ORDER BY p.partner_rank, p.partner_id)
                   AS top_partner_names,
               list(p.fractional_count ORDER BY p.partner_rank, p.partner_id)
                   AS top_partner_fractional_counts
        FROM read_parquet('{_literal(sources["school_partner_index"])}') p
        JOIN profile_endpoints e USING (corpus_view, window_months, window_end)
        WHERE p.partner_rank <= {top_partner_count}
        GROUP BY p.school_id, p.corpus_view, p.window_months, p.window_end
        """
    )


def _create_annual_context_tables(
    connection: duckdb.DuckDBPyConnection,
    sources: dict[str, Path],
    optional_sources: dict[str, Path | None],
    top_similarity_count: int,
) -> None:
    nodes = sources["nodes_year"]
    node_scope = "AND analytical_scope = 'primary'" if "analytical_scope" in _columns(nodes) else ""
    connection.execute(
        f"""
        CREATE TEMP TABLE annual_context AS
        WITH limits AS (
            SELECT corpus_view, max(year)::INTEGER AS max_year
            FROM read_parquet('{_literal(nodes)}')
            WHERE hierarchy_view = 'organization' {node_scope}
            GROUP BY corpus_view
        )
        SELECT e.*,
               least(cast(substr(e.window_end, 1, 4) AS INTEGER), l.max_year)::INTEGER
                   AS annual_graph_year
        FROM profile_endpoints e
        JOIN limits l USING (corpus_view)
        """
    )

    node_columns = _columns(nodes)
    communities = optional_sources["communities_year"]
    continuity = optional_sources["community_continuity_year"]
    community_join = ""
    community_expression = "n.community_id" if "community_id" in node_columns else "NULL"
    community_status_expression = (
        "n.community_status" if "community_status" in node_columns else "NULL"
    )
    if communities is not None:
        community_join = f"""
            LEFT JOIN read_parquet('{_literal(communities)}') c
              ON c.year = n.year AND c.corpus_view = n.corpus_view
             AND c.hierarchy_view = n.hierarchy_view
             AND c.institution_id = n.institution_id
        """
        if "community_id" in node_columns:
            community_expression = "coalesce(n.community_id, c.community_id)"
        else:
            community_expression = "c.community_id"
        community_status_expression = "c.status"
    continuity_join = ""
    continuity_expression = "NULL"
    if continuity is not None:
        continuity_join = f"""
            LEFT JOIN read_parquet('{_literal(continuity)}') cc
              ON cc.year = n.year AND cc.corpus_view = n.corpus_view
             AND cc.hierarchy_view = n.hierarchy_view
             AND cc.annual_community_id = {community_expression}
        """
        continuity_expression = "cc.continuity_id"
    connection.execute(
        f"""
        CREATE TEMP TABLE annual_nodes AS
        SELECT n.year::INTEGER AS year, n.corpus_view, n.institution_id,
               n.degree::BIGINT AS degree, n.pagerank::DOUBLE AS pagerank,
               n.betweenness::DOUBLE AS betweenness,
               n.betweenness_method,
               n.bridge_score::DOUBLE AS bridge_score,
               cast({community_expression} AS VARCHAR) AS community_id,
               cast({continuity_expression} AS VARCHAR) AS community_continuity_id,
               cast({community_status_expression} AS VARCHAR) AS community_status
        FROM read_parquet('{_literal(nodes)}') n
        {community_join}
        {continuity_join}
        WHERE n.hierarchy_view = 'organization' {node_scope}
        """
    )

    citations = sources["citation_edges_year"]
    connection.execute(
        f"""
        CREATE TEMP TABLE citation_metrics AS
        WITH relevant AS (
            SELECT c.*
            FROM read_parquet('{_literal(citations)}') c
            JOIN (SELECT DISTINCT corpus_view, annual_graph_year FROM annual_context) y
              ON c.corpus_view = y.corpus_view AND c.year = y.annual_graph_year
            WHERE c.hierarchy_view = 'organization'
        ), directed AS (
            SELECT year, corpus_view, source_id AS institution_id,
                   0::BIGINT AS incoming_full, 0::DOUBLE AS incoming_fractional,
                   full_count::BIGINT AS outgoing_full,
                   fractional_count::DOUBLE AS outgoing_fractional
            FROM relevant
            UNION ALL
            SELECT year, corpus_view, target_id AS institution_id,
                   full_count::BIGINT, fractional_count::DOUBLE,
                   0::BIGINT, 0::DOUBLE
            FROM relevant
        )
        SELECT year::INTEGER AS year, corpus_view, institution_id,
               sum(incoming_full)::BIGINT AS citation_flow_in_full,
               sum(incoming_fractional)::DOUBLE AS citation_flow_in_fractional,
               sum(outgoing_full)::BIGINT AS citation_flow_out_full,
               sum(outgoing_fractional)::DOUBLE AS citation_flow_out_fractional
        FROM directed
        GROUP BY year, corpus_view, institution_id
        """
    )

    vectors = sources["institution_topic_vectors_year"]
    connection.execute(
        f"""
        CREATE TEMP TABLE vector_support AS
        SELECT v.year::INTEGER AS year, v.corpus_view, v.institution_id,
               count(DISTINCT v.topic_id)::BIGINT AS vector_topic_count,
               bool_or(v.is_similarity_core) AS is_similarity_core
        FROM read_parquet('{_literal(vectors)}') v
        JOIN (SELECT DISTINCT corpus_view, annual_graph_year FROM annual_context) y
          ON v.corpus_view = y.corpus_view AND v.year = y.annual_graph_year
        WHERE v.hierarchy_view = 'organization'
        GROUP BY v.year, v.corpus_view, v.institution_id
        """
    )
    similarities = sources["topic_similarity_edges_year"]
    connection.execute(
        f"""
        CREATE TEMP TABLE similarity_metrics AS
        WITH relevant AS (
            SELECT s.*
            FROM read_parquet('{_literal(similarities)}') s
            JOIN (SELECT DISTINCT corpus_view, annual_graph_year FROM annual_context) y
              ON s.corpus_view = y.corpus_view AND s.year = y.annual_graph_year
            WHERE s.hierarchy_view = 'organization'
        ), directed AS (
            SELECT year, corpus_view, source_id AS institution_id,
                   target_id AS neighbor_id, cosine_similarity
            FROM relevant
            UNION ALL
            SELECT year, corpus_view, target_id AS institution_id,
                   source_id AS neighbor_id, cosine_similarity
            FROM relevant
        ), ranked AS (
            SELECT *, row_number() OVER (
                PARTITION BY year, corpus_view, institution_id
                ORDER BY cosine_similarity DESC, neighbor_id
            ) AS neighbor_rank
            FROM directed
        )
        SELECT year::INTEGER AS year, corpus_view, institution_id,
               count(*)::BIGINT AS topic_similarity_neighbor_count,
               max(cosine_similarity)::DOUBLE AS topic_similarity_maximum,
               avg(cosine_similarity)::DOUBLE AS topic_similarity_mean,
               list(neighbor_id ORDER BY neighbor_rank) FILTER (
                   WHERE neighbor_rank <= {top_similarity_count}
               ) AS topic_similarity_top_neighbor_ids
        FROM ranked
        GROUP BY year, corpus_view, institution_id
        """
    )


def _create_topic_profiles(
    connection: duckdb.DuckDBPyConnection,
    sources: dict[str, Path],
) -> None:
    memberships = sources["work_institutions"]
    identities = sources["school_identities"]
    dates = sources["work_publication_dates"]
    topics = sources["work_topics"]
    schools = sources["school_index"]
    connection.execute(
        f"""
        CREATE TEMP TABLE topic_profiles AS
        WITH mapped AS (
            SELECT wi.work_id, wi.publication_year, i.canonical_school_id,
                   bool_or(wi.strict_primary) AS strict_primary,
                   bool_or(wi.broad_primary) AS broad_primary
            FROM read_parquet('{_literal(memberships)}') wi
            JOIN read_parquet('{_literal(identities)}') i USING (institution_id)
            JOIN read_parquet('{_literal(schools)}') s
              ON i.canonical_school_id = s.canonical_school_id
            WHERE wi.hierarchy_view = 'organization' AND wi.is_primary_research_scope
            GROUP BY wi.work_id, wi.publication_year, i.canonical_school_id
        ), eligible AS (
            SELECT e.corpus_view, e.window_start, e.window_end, e.window_months,
                   m.work_id, m.canonical_school_id,
                   1.0 / count(*) OVER (
                       PARTITION BY e.corpus_view, e.window_months, m.work_id
                   ) AS institution_fraction
            FROM mapped m
            JOIN read_parquet('{_literal(dates)}') d USING (work_id, publication_year)
            JOIN profile_endpoints e
              ON d.publication_month BETWEEN e.window_start AND e.window_end
             AND ((e.corpus_view = 'strict' AND m.strict_primary)
                  OR (e.corpus_view = 'broad' AND m.broad_primary))
            WHERE d.subannual_date_eligible
        ), weights AS (
            SELECT e.corpus_view, e.window_start, e.window_end, e.window_months,
                   e.canonical_school_id, t.method_family AS topic_family,
                   sum(t.topic_score * e.institution_fraction)::DOUBLE AS topic_weight,
                   count(DISTINCT e.work_id)::BIGINT AS contributing_work_count
            FROM eligible e
            JOIN read_parquet('{_literal(topics)}') t USING (work_id)
            WHERE t.topic_score IS NOT NULL AND t.topic_score > 0
              AND t.method_family IS NOT NULL AND trim(t.method_family) <> ''
              AND ((e.corpus_view = 'strict' AND t.corpus_membership = 'strict')
                   OR (e.corpus_view = 'broad'
                       AND t.corpus_membership IN ('strict', 'broad_only')))
            GROUP BY e.corpus_view, e.window_start, e.window_end, e.window_months,
                     e.canonical_school_id, t.method_family
        ), labelled AS (
            SELECT w.*, s.display_name, s.country_code, s.macro_region
            FROM weights w
            JOIN read_parquet('{_literal(schools)}') s USING (canonical_school_id)
        ), totals AS (
            SELECT *, sum(topic_weight) OVER (
                       PARTITION BY canonical_school_id, corpus_view, window_months
                   ) AS school_topic_weight,
                   sum(topic_weight) OVER (PARTITION BY corpus_view, window_months)
                       AS global_topic_weight,
                   sum(topic_weight) OVER (
                       PARTITION BY corpus_view, window_months, macro_region
                   ) AS region_topic_weight,
                   sum(topic_weight) OVER (
                       PARTITION BY corpus_view, window_months, country_code
                   ) AS country_topic_weight,
                   sum(topic_weight) OVER (
                       PARTITION BY corpus_view, window_months, topic_family
                   ) AS global_family_weight,
                   sum(topic_weight) OVER (
                       PARTITION BY corpus_view, window_months, macro_region, topic_family
                   ) AS region_family_weight,
                   sum(topic_weight) OVER (
                       PARTITION BY corpus_view, window_months, country_code, topic_family
                   ) AS country_family_weight
            FROM labelled
        ), shares AS (
            SELECT *,
                   topic_weight / school_topic_weight AS topic_family_share,
                   global_family_weight / global_topic_weight AS global_baseline_share,
                   region_family_weight / region_topic_weight AS macro_region_baseline_share,
                   country_family_weight / country_topic_weight AS country_baseline_share
            FROM totals
        )
        SELECT canonical_school_id, display_name, country_code, macro_region,
               corpus_view, 'school' AS hierarchy_view,
               window_start, window_end, window_months::INTEGER AS window_months,
               topic_family, topic_weight, contributing_work_count,
               topic_family_share,
               global_baseline_share,
               topic_family_share / nullif(global_baseline_share, 0)
                   AS specialization_lift_global,
               macro_region_baseline_share,
               topic_family_share / nullif(macro_region_baseline_share, 0)
                   AS specialization_lift_macro_region,
               country_baseline_share,
               topic_family_share / nullif(country_baseline_share, 0)
                   AS specialization_lift_country,
               row_number() OVER (
                   PARTITION BY canonical_school_id, corpus_view, window_months
                   ORDER BY topic_weight DESC, topic_family
               )::INTEGER AS topic_rank,
               true AS provisional_topic_registry,
               'supported_provisional_registry' AS topic_profile_support_status
        FROM shares
        """
    )
    connection.execute(
        """
        CREATE TEMP TABLE topic_summary AS
        SELECT canonical_school_id, corpus_view, window_months,
               count(*)::INTEGER AS topic_family_count,
               max(topic_family) FILTER (WHERE topic_rank = 1) AS top_topic_family,
               max(topic_family_share) FILTER (WHERE topic_rank = 1)
                   AS top_topic_family_share
        FROM topic_profiles
        GROUP BY canonical_school_id, corpus_view, window_months
        """
    )


def _create_profile_output(
    connection: duckdb.DuckDBPyConnection,
    sources: dict[str, Path],
    temporary_path: Path,
) -> None:
    schools = sources["school_index"]
    strict_availability = (
        "s.strict_work_count = 0" if "strict_work_count" in _columns(schools) else "false"
    )
    date_coverage_basis = (
        "r.date_coverage_basis"
        if "date_coverage_basis" in _columns(sources["institution_outputs_rolling"])
        else "'exact publication-date coverage for the selected rolling window'"
    )
    connection.execute(
        f"""
        COPY (
            SELECT
                s.canonical_school_id,
                s.display_name,
                s.country_code,
                s.country_name,
                s.macro_region,
                s.subregion,
                s.institution_category,
                s.identity_status,
                s.identity_resolution_confidence,
                coalesce(s.identity_quality_flags, []::VARCHAR[]) AS identity_quality_flags,
                e.corpus_view,
                'school' AS hierarchy_view,
                e.window_start,
                e.window_end,
                e.window_months::INTEGER AS window_months,
                e.observed_month_count::INTEGER AS observed_month_count,
                e.eligible_month_count::INTEGER AS eligible_month_count,
                e.coverage_ratio::DOUBLE AS coverage_ratio,
                e.is_complete_window,
                CASE
                    WHEN e.corpus_view = 'strict' AND {strict_availability}
                        THEN 'not_observed_in_corpus'
                    WHEN r.institution_id IS NULL OR r.work_count = 0
                        THEN 'no_recent_activity'
                    ELSE 'supported'
                END AS profile_support_status,
                coalesce(r.work_count, 0)::BIGINT AS full_work_count,
                coalesce(r.work_count, 0)::BIGINT AS work_count,
                coalesce(r.fractional_work_count, 0)::DOUBLE AS fractional_work_count,
                coalesce(rc.recent_12m_work_count, 0)::BIGINT AS recent_12m_work_count,
                coalesce(rc.recent_24m_work_count, 0)::BIGINT AS recent_24m_work_count,
                coalesce(rc.recent_36m_work_count, 0)::BIGINT AS recent_36m_work_count,
                r.international_collaboration_share::DOUBLE
                    AS international_collaboration_share,
                r.cross_region_collaboration_share::DOUBLE
                    AS cross_region_collaboration_share,
                coalesce(r.partner_institution_count, 0)::BIGINT
                    AS partner_institution_count,
                coalesce(r.partner_country_count, 0)::BIGINT AS partner_country_count,
                coalesce(r.fractional_collaboration_strength, 0)::DOUBLE
                    AS fractional_collaboration_strength,
                coalesce(r.repeat_partner_count, 0)::BIGINT AS repeat_partner_count,
                r.repeat_partner_ratio::DOUBLE AS repeat_partner_ratio,
                coalesce(r.effective_partner_count, 0)::DOUBLE AS effective_partner_count,
                coalesce(p.top_partner_ids, []::VARCHAR[]) AS top_partner_ids,
                coalesce(p.top_partner_names, []::VARCHAR[]) AS top_partner_names,
                coalesce(p.top_partner_fractional_counts, []::DOUBLE[])
                    AS top_partner_fractional_counts,
                coalesce(ts.topic_family_count, 0)::INTEGER AS topic_family_count,
                ts.top_topic_family,
                ts.top_topic_family_share::DOUBLE AS top_topic_family_share,
                CASE WHEN ts.topic_family_count IS NULL THEN 'no_recent_topic_activity'
                     ELSE 'supported_provisional_registry'
                END AS topic_profile_support_status,
                m.rolling_12m_activity_change::DOUBLE AS rolling_12m_activity_change,
                m.rolling_12m_fractional_activity_change::DOUBLE
                    AS rolling_12m_fractional_activity_change,
                coalesce(m.momentum_support_status, 'no_current_12m_activity')
                    AS momentum_support_status,
                ac.annual_graph_year::INTEGER AS annual_graph_year,
                'complete-year organization-view primary-scope coauthorship graph'
                    AS annual_graph_boundary,
                CASE WHEN n.institution_id IS NULL
                     THEN 'not_observed_in_annual_graph_' || ac.annual_graph_year
                     ELSE 'available_complete_year'
                END AS annual_network_support_status,
                n.degree::BIGINT AS degree,
                n.pagerank::DOUBLE AS pagerank,
                n.betweenness::DOUBLE AS betweenness,
                n.betweenness_method,
                n.bridge_score::DOUBLE AS bridge_score,
                n.community_id,
                n.community_continuity_id,
                CASE WHEN n.institution_id IS NULL THEN 'not_observed'
                     WHEN n.community_id IS NULL THEN coalesce(n.community_status, 'not_assigned')
                     ELSE coalesce(n.community_status, 'available')
                END AS community_status,
                ac.annual_graph_year::INTEGER AS citation_flow_year,
                'complete-year directed closed-corpus citation flow; not coauthorship'
                    AS citation_flow_boundary,
                CASE WHEN c.institution_id IS NULL
                     THEN 'not_observed_in_annual_citation_flow'
                     ELSE 'available_closed_corpus_annual'
                END AS citation_flow_support_status,
                coalesce(c.citation_flow_in_full, 0)::BIGINT AS citation_flow_in_full,
                coalesce(c.citation_flow_in_fractional, 0)::DOUBLE
                    AS citation_flow_in_fractional,
                coalesce(c.citation_flow_in_fractional, 0)::DOUBLE
                    AS citation_flow_fractional_in_strength,
                coalesce(c.citation_flow_out_full, 0)::BIGINT AS citation_flow_out_full,
                coalesce(c.citation_flow_out_fractional, 0)::DOUBLE
                    AS citation_flow_out_fractional,
                ac.annual_graph_year::INTEGER AS topic_similarity_year,
                'complete-year provisional-Topic core cosine proximity; not collaboration'
                    AS topic_similarity_boundary,
                CASE
                    WHEN v.institution_id IS NULL THEN 'not_observed_in_annual_topic_vector'
                    WHEN NOT v.is_similarity_core THEN 'outside_topic_similarity_core'
                    WHEN sm.institution_id IS NULL THEN 'core_without_retained_similarity_edge'
                    ELSE 'available_annual_core_limited'
                END AS topic_similarity_support_status,
                coalesce(sm.topic_similarity_neighbor_count, 0)::BIGINT
                    AS topic_similarity_neighbor_count,
                sm.topic_similarity_maximum::DOUBLE AS topic_similarity_maximum,
                sm.topic_similarity_mean::DOUBLE AS topic_similarity_mean,
                coalesce(sm.topic_similarity_top_neighbor_ids, []::VARCHAR[])
                    AS topic_similarity_top_neighbor_ids,
                r.date_coverage_ratio::DOUBLE AS date_coverage_ratio,
                coalesce(r.date_coverage_status, 'not_observed_in_selected_window')
                    AS date_coverage_status,
                coalesce(
                    {date_coverage_basis},
                    'no exact-date-eligible Work for the school in the selected rolling window'
                ) AS date_coverage_basis,
                list_sort(list_distinct(list_concat(
                    coalesce(s.identity_quality_flags, []::VARCHAR[]),
                    CASE WHEN r.institution_id IS NULL OR r.work_count = 0
                         THEN ['no_recent_activity']::VARCHAR[] ELSE []::VARCHAR[] END,
                    CASE WHEN NOT e.is_complete_window
                         THEN ['incomplete_window']::VARCHAR[] ELSE []::VARCHAR[] END,
                    CASE WHEN n.institution_id IS NULL
                         THEN ['annual_network_not_observed']::VARCHAR[] ELSE []::VARCHAR[] END,
                    CASE WHEN ts.topic_family_count IS NULL
                         THEN ['topic_profile_not_observed']::VARCHAR[] ELSE []::VARCHAR[] END,
                    CASE WHEN v.institution_id IS NULL OR NOT coalesce(v.is_similarity_core, false)
                         THEN ['topic_similarity_core_limited']::VARCHAR[] ELSE []::VARCHAR[] END
                ))) AS quality_flags,
                'Publication month is bibliographic observation time, not collaboration start time.'
                    AS publication_time_interpretation
            FROM read_parquet('{_literal(schools)}') s
            CROSS JOIN annual_context ac
            JOIN profile_endpoints e USING (
                corpus_view, window_months, window_start, window_end,
                observed_month_count, eligible_month_count, coverage_ratio, is_complete_window
            )
            LEFT JOIN selected_rolling r
              ON r.institution_id = s.canonical_school_id
             AND r.corpus_view = e.corpus_view
             AND r.window_months = e.window_months
             AND r.window_end = e.window_end
            LEFT JOIN recent_counts rc
              ON rc.institution_id = s.canonical_school_id
             AND rc.corpus_view = e.corpus_view
            LEFT JOIN momentum m
              ON m.institution_id = s.canonical_school_id
             AND m.corpus_view = e.corpus_view
            LEFT JOIN profile_partners p
              ON p.school_id = s.canonical_school_id
             AND p.corpus_view = e.corpus_view
             AND p.window_months = e.window_months
             AND p.window_end = e.window_end
            LEFT JOIN topic_summary ts
              ON ts.canonical_school_id = s.canonical_school_id
             AND ts.corpus_view = e.corpus_view
             AND ts.window_months = e.window_months
            LEFT JOIN annual_nodes n
              ON n.institution_id = s.canonical_school_id
             AND n.corpus_view = e.corpus_view
             AND n.year = ac.annual_graph_year
            LEFT JOIN citation_metrics c
              ON c.institution_id = s.canonical_school_id
             AND c.corpus_view = e.corpus_view
             AND c.year = ac.annual_graph_year
            LEFT JOIN vector_support v
              ON v.institution_id = s.canonical_school_id
             AND v.corpus_view = e.corpus_view
             AND v.year = ac.annual_graph_year
            LEFT JOIN similarity_metrics sm
              ON sm.institution_id = s.canonical_school_id
             AND sm.corpus_view = e.corpus_view
             AND sm.year = ac.annual_graph_year
            WHERE ac.corpus_view = e.corpus_view
              AND ac.window_months = e.window_months
              AND ac.window_end = e.window_end
            ORDER BY e.corpus_view, e.window_months, s.canonical_school_id
        ) TO '{_literal(temporary_path)}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )


def _validate_input_columns(sources: dict[str, Path]) -> None:
    requirements = {
        "school_index": {
            "canonical_school_id",
            "display_name",
            "country_code",
            "country_name",
            "macro_region",
            "subregion",
            "institution_category",
            "identity_status",
            "identity_resolution_confidence",
            "identity_quality_flags",
        },
        "school_identities": {"institution_id", "canonical_school_id"},
        "institution_outputs_rolling": {
            "window_start",
            "window_end",
            "window_months",
            "observed_month_count",
            "eligible_month_count",
            "coverage_ratio",
            "is_complete_window",
            "corpus_view",
            "hierarchy_view",
            "institution_id",
            "work_count",
            "fractional_work_count",
            "international_collaboration_share",
            "cross_region_collaboration_share",
            "partner_institution_count",
            "partner_country_count",
            "fractional_collaboration_strength",
            "repeat_partner_count",
            "repeat_partner_ratio",
            "effective_partner_count",
            "date_coverage_ratio",
            "date_coverage_status",
        },
        "school_partner_index": {
            "window_end",
            "window_months",
            "corpus_view",
            "school_id",
            "partner_id",
            "partner_name",
            "fractional_count",
            "partner_rank",
        },
        "nodes_year": {
            "year",
            "corpus_view",
            "hierarchy_view",
            "institution_id",
            "degree",
            "pagerank",
            "betweenness",
            "betweenness_method",
            "bridge_score",
        },
        "citation_edges_year": {
            "year",
            "corpus_view",
            "hierarchy_view",
            "source_id",
            "target_id",
            "full_count",
            "fractional_count",
        },
        "institution_topic_vectors_year": {
            "year",
            "corpus_view",
            "hierarchy_view",
            "institution_id",
            "topic_id",
            "is_similarity_core",
        },
        "topic_similarity_edges_year": {
            "year",
            "corpus_view",
            "hierarchy_view",
            "source_id",
            "target_id",
            "cosine_similarity",
        },
        "work_institutions": {
            "work_id",
            "publication_year",
            "hierarchy_view",
            "institution_id",
            "is_primary_research_scope",
            "strict_primary",
            "broad_primary",
        },
        "work_publication_dates": {
            "work_id",
            "publication_year",
            "publication_month",
            "subannual_date_eligible",
        },
        "work_topics": {
            "work_id",
            "topic_id",
            "topic_score",
            "corpus_membership",
            "method_family",
        },
    }
    for name, required in requirements.items():
        missing = required - _columns(sources[name])
        if missing:
            raise ValueError(f"{name} is missing required columns: {sorted(missing)}")


def _validate_identity_equivalence(schools_path: Path, identities_path: Path) -> None:
    identities = pq.read_table(identities_path, columns=["institution_id", "canonical_school_id"])
    if (
        identities.column("institution_id").to_pylist()
        != identities.column("canonical_school_id").to_pylist()
    ):
        raise ValueError(
            "school identities differ from organizations; rebuild annual and rolling profile "
            "inputs from canonical Work memberships before computing centrality or similarity"
        )
    school_ids = set(
        str(value)
        for value in pq.read_table(schools_path, columns=["canonical_school_id"])
        .column("canonical_school_id")
        .to_pylist()
    )
    identity_ids = set(str(value) for value in identities.column("canonical_school_id").to_pylist())
    if not school_ids.issubset(identity_ids):
        raise ValueError("school index contains canonical IDs absent from the identity mapping")


def _validate_generated_outputs(
    connection: duckdb.DuckDBPyConnection,
    temporary: dict[str, Path],
    school_index_path: Path,
    endpoint_count: int,
) -> None:
    profile_metrics = parquet_metrics(
        temporary["school_profiles"],
        primary_key=_PROFILE_PRIMARY_KEY,
        required_columns=_PROFILE_REQUIRED_COLUMNS,
    )
    topic_metrics = parquet_metrics(
        temporary["school_topic_profiles"],
        primary_key=_TOPIC_PRIMARY_KEY,
        required_columns=_TOPIC_REQUIRED_COLUMNS,
    )
    school_count_row = connection.execute(
        f"SELECT count(*) FROM read_parquet('{_literal(school_index_path)}')"
    ).fetchone()
    if school_count_row is None:
        raise ValueError("school index count query returned no result")
    expected_profiles = int(school_count_row[0]) * endpoint_count
    if profile_metrics["row_count"] != expected_profiles:
        raise ValueError(
            f"profile completeness failed: expected {expected_profiles}, "
            f"found {profile_metrics['row_count']}"
        )
    invalid = connection.execute(
        f"""
        SELECT
            count(*) FILTER (WHERE work_count < 0 OR fractional_work_count < 0),
            count(*) FILTER (
                WHERE international_collaboration_share NOT BETWEEN 0 AND 1
                   OR cross_region_collaboration_share NOT BETWEEN 0 AND 1
                   OR repeat_partner_ratio NOT BETWEEN 0 AND 1
                   OR bridge_score NOT BETWEEN 0 AND 1
                   OR topic_similarity_maximum NOT BETWEEN 0 AND 1
            )
        FROM read_parquet('{_literal(temporary["school_profiles"])}')
        """
    ).fetchone()
    if invalid is None or any(int(value) for value in invalid):
        raise ValueError(f"school profile range validation failed: {invalid}")
    topic_invalid = connection.execute(
        f"""
        WITH shares AS (
            SELECT canonical_school_id, corpus_view, window_months,
                   sum(topic_family_share) AS total_share
            FROM read_parquet('{_literal(temporary["school_topic_profiles"])}')
            GROUP BY canonical_school_id, corpus_view, window_months
        )
        SELECT
            count(*) FILTER (
                WHERE topic_weight <= 0 OR topic_family_share NOT BETWEEN 0 AND 1
                   OR specialization_lift_global < 0
                   OR specialization_lift_macro_region < 0
                   OR specialization_lift_country < 0
            ),
            (SELECT count(*) FROM shares WHERE abs(total_share - 1.0) > 1e-9)
        FROM read_parquet('{_literal(temporary["school_topic_profiles"])}')
        """
    ).fetchone()
    if topic_invalid is None or any(int(value) for value in topic_invalid):
        raise ValueError(f"school Topic-profile validation failed: {topic_invalid}")
    if topic_metrics["row_count"] == 0:
        raise ValueError("school Topic-profile output is empty")
    prohibited = {"quality_score", "university_quality_score", "user_defined_fit_score"}
    if prohibited.intersection(_columns(temporary["school_profiles"])):
        raise ValueError("scientific profile output contains a prohibited combined score")


def _coverage_counts(profiles_path: Path, topics_path: Path) -> dict[str, int]:
    connection = duckdb.connect()
    try:
        row = connection.execute(
            """
            SELECT
                count(DISTINCT canonical_school_id),
                count(*) FILTER (WHERE profile_support_status = 'supported'),
                count(*) FILTER (WHERE profile_support_status = 'no_recent_activity'),
                count(*) FILTER (WHERE profile_support_status = 'not_observed_in_corpus'),
                count(*) FILTER (WHERE annual_network_support_status = 'available_complete_year'),
                count(*) FILTER (
                    WHERE citation_flow_support_status = 'available_closed_corpus_annual'
                ),
                count(*) FILTER (
                    WHERE topic_similarity_support_status = 'available_annual_core_limited'
                )
            FROM read_parquet(?)
            """,
            [str(profiles_path)],
        ).fetchone()
        topic_row = connection.execute(
            "SELECT count(DISTINCT canonical_school_id) FROM read_parquet(?)", [str(topics_path)]
        ).fetchone()
    finally:
        connection.close()
    if row is None or topic_row is None:
        raise ValueError("school profile coverage query returned no result")
    return {
        "eligible_school_count": int(row[0]),
        "supported_profile_row_count": int(row[1]),
        "no_recent_activity_row_count": int(row[2]),
        "not_observed_in_corpus_row_count": int(row[3]),
        "annual_network_supported_row_count": int(row[4]),
        "citation_flow_supported_row_count": int(row[5]),
        "topic_similarity_supported_row_count": int(row[6]),
        "topic_profile_school_count": int(topic_row[0]),
    }


def _columns(path: Path) -> set[str]:
    return set(pq.ParquetFile(path).schema_arrow.names)


def _literal(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def _sql_string(value: str) -> str:
    return value.replace("'", "''")


def _quoted_values(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{_sql_string(value)}'" for value in values)


def _integer_values(values: tuple[int, ...]) -> str:
    return ", ".join(str(int(value)) for value in values)


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
