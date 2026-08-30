"""Compact public dashboard bundle derived only from processed datasets."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics

_STAGE_VERSION = "public-dashboard-bundle-2026-08-30-v11"
_GEOGRAPHIC_FLOW_VERSION = "geographic-flow-explorer-2026-08-28-v2"
_GEOGRAPHIC_ANCHOR_VERSION = "geographic-display-anchors-2026-08-29-v3"
_SCHOOL_EGO_VERSION = "school-ego-map-2026-08-29-v1"
_SCHOOL_PROFILE_VERSION = "school-profile-ui-2026-08-29-v1"
_SCHOOL_COMPARISON_VERSION = "school-comparison-ui-2026-08-30-v1"
_SCIENTIFIC_LAYER_VERSION = "separate-scientific-layers-ui-2026-08-30-v1"
_SCHOOL_EGO_TOP_K = 50
_SCIENTIFIC_LAYER_EDGE_LIMIT = 1000
_OPENALEX_LICENSE = "CC0 1.0 Universal"
_OPENALEX_LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
_OPENALEX_SOURCE_URL = "https://openalex.org/"


def build_dashboard_bundle(
    *,
    sources: dict[str, str | Path],
    output_directory: str | Path,
    metadata_path: str | Path,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build a compact, validated snapshot with no API calls or private paths."""
    copied_sources = {
        "trends",
        "matrix",
        "map_nodes",
        "map_edges",
        "map_coverage",
        "network_nodes",
        "network_edges",
        "network_accessibility",
        "graph_metrics",
        "sensitivity",
        "community_continuity",
        "community_transitions",
        "citation_coverage",
        "topic_similarity_coverage",
        "layer_summary",
    }
    required = copied_sources | {
        "institution_hierarchy",
        "institutions",
        "complete_nodes",
        "school_index",
        "school_partners",
        "annual_edges",
        "quarter_edges",
        "month_edges",
        "quarter_outputs",
        "school_profiles",
        "school_topic_profiles",
        "citation_edges",
        "topic_similarity_edges",
    }
    missing = required.difference(sources)
    if missing:
        raise ValueError(f"dashboard bundle lacks sources: {sorted(missing)}")
    paths = {name: Path(value) for name, value in sources.items()}
    for path in paths.values():
        if not path.is_file():
            raise ValueError(f"dashboard source does not exist: {path}")
    output = Path(output_directory)
    output.mkdir(parents=True, exist_ok=True)
    destinations = {name: output / f"{name}.parquet" for name in copied_sources}
    destinations["topics"] = output / "topics.parquet"
    destinations["institution_identities"] = output / "institution_identities.parquet"
    destinations["filter_dimensions"] = output / "filter_dimensions.parquet"
    destinations["geography_dimensions"] = output / "geography_dimensions.parquet"
    destinations["geography_anchors"] = output / "geography_anchors.parquet"
    destinations["geography_outputs"] = output / "geography_outputs.parquet"
    destinations["school_index"] = output / "school_index.parquet"
    destinations["school_ego_partners"] = output / "school_ego_partners.parquet"
    destinations["school_profiles"] = output / "school_profiles.parquet"
    destinations["school_topic_profiles"] = output / "school_topic_profiles.parquet"
    destinations["citation_edges"] = output / "citation_edges.parquet"
    destinations["topic_similarity_edges"] = output / "topic_similarity_edges.parquet"
    temporary = {name: path.with_suffix(".parquet.tmp") for name, path in destinations.items()}
    metadata = Path(metadata_path)
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata_temporary = metadata.with_suffix(".json.tmp")
    for path in [*temporary.values(), metadata_temporary]:
        path.unlink(missing_ok=True)
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        for name in sorted(copied_sources):
            connection.execute(
                f"""
                COPY (SELECT * FROM read_parquet(?))
                TO '{_literal(temporary[name])}'
                (FORMAT PARQUET, COMPRESSION ZSTD)
                """,
                [str(paths[name])],
            )
        _write_public_citation_edges(
            connection,
            source=paths["citation_edges"],
            destination=temporary["citation_edges"],
            edge_limit_per_view=_SCIENTIFIC_LAYER_EDGE_LIMIT,
        )
        _write_public_topic_similarity_edges(
            connection,
            source=paths["topic_similarity_edges"],
            destination=temporary["topic_similarity_edges"],
            edge_limit_per_view=_SCIENTIFIC_LAYER_EDGE_LIMIT,
        )
        _write_filter_dimensions(
            connection, paths["complete_nodes"], temporary["filter_dimensions"]
        )
        _write_geography_dimensions(
            connection, paths["complete_nodes"], temporary["geography_dimensions"]
        )
        _write_geography_anchors(
            connection,
            complete_nodes_path=paths["complete_nodes"],
            institutions_path=paths["institutions"],
            destination=temporary["geography_anchors"],
            source_dataset_sha256=file_sha256(paths["institutions"]),
        )
        _write_geography_outputs(
            connection,
            complete_nodes_path=paths["complete_nodes"],
            destination=temporary["geography_outputs"],
        )
        _write_school_dashboard_index(
            connection,
            school_index_path=paths["school_index"],
            rolling_partner_path=paths["school_partners"],
            network_nodes_path=paths["network_nodes"],
            destination=temporary["school_index"],
        )
        _write_school_ego_partners(
            connection,
            school_index_path=paths["school_index"],
            rolling_partner_path=paths["school_partners"],
            annual_edges_path=paths["annual_edges"],
            quarter_edges_path=paths["quarter_edges"],
            month_edges_path=paths["month_edges"],
            quarter_outputs_path=paths["quarter_outputs"],
            destination=temporary["school_ego_partners"],
            top_k=_SCHOOL_EGO_TOP_K,
        )
        _write_school_profile_table(
            connection,
            source=paths["school_profiles"],
            destination=temporary["school_profiles"],
        )
        _write_school_profile_table(
            connection,
            source=paths["school_topic_profiles"],
            destination=temporary["school_topic_profiles"],
        )
        connection.execute(
            f"""
            COPY (
                SELECT
                    year,
                    corpus_view,
                    hierarchy_view,
                    topic_family,
                    count(*)::BIGINT AS visible_edge_count,
                    sum(full_count)::BIGINT AS full_count,
                    sum(fractional_count) AS fractional_count,
                    sum(distinct_work_count)::BIGINT AS edge_work_count_sum,
                    'top fixed-layout core edges only' AS coverage_note
                FROM (
                    SELECT *, unnest(topic_families) AS topic_family
                    FROM read_parquet(?)
                )
                GROUP BY year, corpus_view, hierarchy_view, topic_family
                ORDER BY year, corpus_view, hierarchy_view, topic_family
            ) TO '{_literal(temporary["topics"])}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [str(paths["network_edges"])],
        )
        connection.execute(
            f"""
            COPY (
                SELECT
                    hierarchy.institution_id AS organization_id,
                    organization.display_name AS organization_name,
                    hierarchy.canonical_institution_id AS umbrella_id,
                    umbrella.display_name AS umbrella_name,
                    hierarchy.is_collapsed
                FROM read_parquet(?) AS hierarchy
                JOIN read_parquet(?) AS organization
                  ON hierarchy.institution_id = organization.institution_id
                JOIN read_parquet(?) AS umbrella
                  ON hierarchy.canonical_institution_id = umbrella.institution_id
                WHERE hierarchy.hierarchy_view = 'umbrella'
                ORDER BY organization_name, organization_id
            ) TO '{_literal(temporary["institution_identities"])}'
            (FORMAT PARQUET, COMPRESSION ZSTD)
            """,
            [
                str(paths["institution_hierarchy"]),
                str(paths["institutions"]),
                str(paths["institutions"]),
            ],
        )
        collapse_count = connection.execute(
            """
            SELECT count(*)
            FROM read_parquet(?)
            WHERE hierarchy_view = 'umbrella' AND is_collapsed
            """,
            [str(paths["institution_hierarchy"])],
        ).fetchone()
        if collapse_count is None:
            raise ValueError("institution hierarchy collapse count was unavailable")
        active_collapse_count = int(collapse_count[0])
        missing_anchors = connection.execute(
            """
            WITH flow_geographies AS (
                SELECT DISTINCT geographic_level, source_geography AS geography
                FROM read_parquet(?)
                UNION
                SELECT DISTINCT geographic_level, target_geography AS geography
                FROM read_parquet(?)
            )
            SELECT count(*)
            FROM flow_geographies
            LEFT JOIN read_parquet(?) anchors USING (geographic_level, geography)
            WHERE anchors.geography IS NULL
            """,
            [
                str(paths["matrix"]),
                str(paths["matrix"]),
                str(temporary["geography_anchors"]),
            ],
        ).fetchone()
        if missing_anchors is None or int(missing_anchors[0]):
            raise ValueError("one or more geographic flow endpoints lack a sourced anchor")
        missing_denominators = connection.execute(
            """
            WITH flow_geographies AS (
                SELECT DISTINCT
                    year, corpus_view, hierarchy_view, geographic_level,
                    source_geography AS geography
                FROM read_parquet(?)
                UNION
                SELECT DISTINCT
                    year, corpus_view, hierarchy_view, geographic_level,
                    target_geography AS geography
                FROM read_parquet(?)
            )
            SELECT count(*)
            FROM flow_geographies
            LEFT JOIN read_parquet(?) outputs USING (
                year, corpus_view, hierarchy_view, geographic_level, geography
            )
            WHERE outputs.geography IS NULL OR outputs.full_work_count <= 0
            """,
            [str(paths["matrix"]), str(paths["matrix"]), str(temporary["geography_outputs"])],
        ).fetchone()
        if missing_denominators is None or int(missing_denominators[0]):
            raise ValueError("one or more geographic flow endpoints lack a positive denominator")
    except BaseException:
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()

    definitions: dict[str, tuple[list[str], set[str], str | None]] = {
        "trends": (
            ["year", "corpus_view", "hierarchy_view", "source_region", "target_region"],
            {"year", "region_pair", "full_count", "fractional_count"},
            "year",
        ),
        "matrix": (
            [
                "year",
                "corpus_view",
                "hierarchy_view",
                "geographic_level",
                "source_geography",
                "target_geography",
            ],
            {"year", "geographic_level", "normalized_share", "cell_status"},
            "year",
        ),
        "map_nodes": (
            ["year", "corpus_view", "hierarchy_view", "institution_id"],
            {"year", "institution_id", "latitude", "longitude"},
            "year",
        ),
        "map_edges": (
            ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {"year", "source_latitude", "target_latitude", "default_edge_rank"},
            "year",
        ),
        "map_coverage": (
            ["year", "corpus_view", "hierarchy_view"],
            {"year", "coordinate_node_count", "missing_coordinate_node_count"},
            "year",
        ),
        "network_nodes": (
            ["year", "corpus_view", "hierarchy_view", "institution_id"],
            {"year", "institution_id", "x", "y", "community_id"},
            "year",
        ),
        "network_edges": (
            ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {"year", "source_x", "target_x", "fractional_count"},
            "year",
        ),
        "network_accessibility": (
            ["year", "corpus_view", "hierarchy_view"],
            {"year", "summary_text", "coordinate_policy"},
            "year",
        ),
        "graph_metrics": (
            ["year", "corpus_view", "hierarchy_view"],
            {"year", "node_count", "edge_count", "density", "modularity"},
            "year",
        ),
        "sensitivity": (
            ["comparison_id"],
            {"comparison_id", "comparison", "status", "major_change"},
            None,
        ),
        "community_continuity": (
            ["year", "corpus_view", "hierarchy_view", "annual_community_id"],
            {"year", "annual_community_id", "continuity_id", "match_status"},
            "year",
        ),
        "community_transitions": (
            [
                "transition_year",
                "corpus_view",
                "hierarchy_view",
                "previous_community_key",
                "current_community_key",
            ],
            {"transition_year", "event_type", "assignment_selected", "jaccard_overlap"},
            "transition_year",
        ),
        "citation_edges": (
            ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {
                "year",
                "source_id",
                "target_id",
                "full_count",
                "fractional_count",
                "citation_direction",
                "layer_semantics",
                "public_edge_rank",
                "public_edge_limit",
                "public_selection_policy",
            },
            "year",
        ),
        "citation_coverage": (
            ["year", "corpus_view", "hierarchy_view"],
            {
                "year",
                "reference_count",
                "institution_resolved_reference_count",
                "institution_resolved_share",
                "coverage_denominator",
                "citation_direction",
                "layer_semantics",
            },
            "year",
        ),
        "topic_similarity_edges": (
            ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {
                "year",
                "source_id",
                "target_id",
                "cosine_similarity",
                "maximum_institutions_per_view",
                "top_k",
                "minimum_similarity",
                "edge_selection_policy",
                "layer_semantics",
                "public_edge_rank",
                "public_edge_limit",
                "public_selection_policy",
            },
            "year",
        ),
        "topic_similarity_coverage": (
            ["year", "corpus_view", "hierarchy_view"],
            {
                "year",
                "vector_coverage_share",
                "core_coverage_share",
                "selected_core_institution_count",
                "selected_similarity_edge_count",
                "maximum_institutions_per_view",
                "top_k",
                "minimum_similarity",
                "edge_selection_policy",
                "layer_semantics",
            },
            "year",
        ),
        "layer_summary": (
            ["year", "corpus_view", "hierarchy_view", "layer"],
            {
                "year",
                "layer",
                "directionality",
                "edge_count",
                "weight_semantics",
                "coverage_scope",
                "composite_weight_defined",
                "comparison_boundary",
            },
            "year",
        ),
        "institution_identities": (
            ["organization_id"],
            {
                "organization_id",
                "organization_name",
                "umbrella_id",
                "umbrella_name",
                "is_collapsed",
            },
            None,
        ),
        "filter_dimensions": (
            ["year", "corpus_view", "hierarchy_view", "dimension", "value"],
            {"year", "corpus_view", "hierarchy_view", "dimension", "value"},
            "year",
        ),
        "geography_dimensions": (
            ["country_code"],
            {"country_code", "country_name", "macro_region", "subregion"},
            None,
        ),
        "geography_anchors": (
            ["geographic_level", "geography"],
            {
                "geographic_level",
                "geography",
                "display_name",
                "macro_region",
                "latitude",
                "longitude",
                "anchor_method",
                "coordinate_source",
                "coordinate_license",
                "source_dataset_sha256",
            },
            None,
        ),
        "geography_outputs": (
            [
                "year",
                "corpus_view",
                "hierarchy_view",
                "geographic_level",
                "geography",
            ],
            {
                "year",
                "corpus_view",
                "hierarchy_view",
                "geographic_level",
                "geography",
                "full_work_count",
                "fractional_work_count",
            },
            "year",
        ),
        "school_index": (
            ["school_id"],
            {
                "school_id",
                "display_name",
                "country_code",
                "macro_region",
                "institution_category",
                "latitude",
                "longitude",
                "has_coordinates",
                "in_prior_visualization_core",
                "has_retained_ego_partners",
            },
            None,
        ),
        "school_ego_partners": (
            ["period_key", "corpus_view", "school_id", "partner_id"],
            {
                "time_basis",
                "period_key",
                "period_label",
                "corpus_view",
                "school_id",
                "school_name",
                "partner_id",
                "partner_name",
                "fractional_count",
                "normalized_intensity",
                "persistence",
                "partner_rank",
                "school_latitude",
                "school_longitude",
                "partner_latitude",
                "partner_longitude",
                "source_partner_index",
            },
            None,
        ),
        "school_profiles": (
            [
                "school_id",
                "corpus_view",
                "hierarchy_view",
                "window_start",
                "window_end",
                "window_months",
            ],
            {
                "school_id",
                "display_name",
                "country_name",
                "corpus_view",
                "window_start",
                "window_end",
                "window_months",
                "coverage_ratio",
                "profile_support_status",
                "full_work_count",
                "topic_profile_support_status",
                "annual_network_support_status",
                "citation_flow_support_status",
                "topic_similarity_support_status",
                "date_coverage_status",
                "quality_flags",
            },
            None,
        ),
        "school_topic_profiles": (
            [
                "school_id",
                "corpus_view",
                "hierarchy_view",
                "window_start",
                "window_end",
                "window_months",
                "topic_family",
            ],
            {
                "school_id",
                "corpus_view",
                "window_start",
                "window_end",
                "window_months",
                "topic_family",
                "topic_family_share",
                "topic_rank",
                "provisional_topic_registry",
                "topic_profile_support_status",
            },
            None,
        ),
        "topics": (
            ["year", "corpus_view", "hierarchy_view", "topic_family"],
            {"year", "topic_family", "full_count", "fractional_count"},
            "year",
        ),
    }
    metrics: dict[str, dict[str, Any]] = {}
    for name, path in temporary.items():
        primary_key, required_columns, year_column = definitions[name]
        metrics[name] = parquet_metrics(
            path,
            primary_key=primary_key,
            required_columns=required_columns,
            year_column=year_column,
        )
    hashes = {name: metrics[name]["checksum_sha256"] for name in sorted(metrics)}
    metadata_connection = duckdb.connect()
    try:
        ego_period_rows = metadata_connection.execute(
            """
            SELECT DISTINCT
                time_basis, period_key, period_label, period_start, period_end,
                window_months, persistence_unit, persistence_denominator,
                persistence_definition
            FROM read_parquet(?)
            ORDER BY
                CASE period_key
                    WHEN 'rolling_24m' THEN 1
                    WHEN 'rolling_12m' THEN 2
                    WHEN 'rolling_36m' THEN 3
                    ELSE CASE time_basis WHEN 'quarterly' THEN 4 ELSE 5 END
                END,
                period_key
            """,
            [str(temporary["school_ego_partners"])],
        ).fetchall()
        outside_core_ego_schools = metadata_connection.execute(
            """
            SELECT count(*)
            FROM read_parquet(?)
            WHERE has_retained_ego_partners AND NOT in_prior_visualization_core
            """,
            [str(temporary["school_index"])],
        ).fetchone()
        ego_coordinate_coverage = metadata_connection.execute(
            """
            SELECT count(*) AS row_count,
                   count(*) FILTER (
                       WHERE school_latitude IS NOT NULL AND school_longitude IS NOT NULL
                         AND partner_latitude IS NOT NULL AND partner_longitude IS NOT NULL
                   ) AS mapped_row_count
            FROM read_parquet(?)
            """,
            [str(temporary["school_ego_partners"])],
        ).fetchone()
        profile_window_rows = metadata_connection.execute(
            """
            SELECT window_months, min(window_start), max(window_end), count(*)
            FROM read_parquet(?)
            GROUP BY window_months
            ORDER BY window_months
            """,
            [str(temporary["school_profiles"])],
        ).fetchall()
        citation_policy_rows = metadata_connection.execute(
            """
            SELECT DISTINCT citation_direction, coverage_denominator, layer_semantics
            FROM read_parquet(?)
            ORDER BY ALL
            """,
            [str(temporary["citation_coverage"])],
        ).fetchall()
        topic_policy_rows = metadata_connection.execute(
            """
            SELECT DISTINCT maximum_institutions_per_view, top_k, minimum_similarity,
                            edge_selection_policy, layer_semantics
            FROM read_parquet(?)
            ORDER BY ALL
            """,
            [str(temporary["topic_similarity_coverage"])],
        ).fetchall()
        composite_layer_rows = metadata_connection.execute(
            """
            SELECT count(*) FROM read_parquet(?) WHERE composite_weight_defined
            """,
            [str(temporary["layer_summary"])],
        ).fetchone()
    finally:
        metadata_connection.close()
    if outside_core_ego_schools is None or ego_coordinate_coverage is None:
        raise ValueError("School Ego Map dashboard metadata could not be derived")
    if len(citation_policy_rows) != 1:
        raise ValueError("citation-flow dashboard policy is inconsistent across annual views")
    if len(topic_policy_rows) != 1:
        raise ValueError("Topic-proximity dashboard policy is inconsistent across annual views")
    if composite_layer_rows is None or int(composite_layer_rows[0]):
        raise ValueError("a composite scientific layer weight is forbidden")
    ego_periods = [
        {
            "time_basis": str(row[0]),
            "period_key": str(row[1]),
            "period_label": str(row[2]),
            "period_start": str(row[3]),
            "period_end": str(row[4]),
            "window_months": int(row[5]),
            "persistence_unit": str(row[6]),
            "persistence_denominator": int(row[7]),
            "persistence_definition": str(row[8]),
        }
        for row in ego_period_rows
    ]
    payload = {
        "schema_version": 1,
        "data_version": "gisnet-0.1.0-2026-08-28",
        "methods_version": _STAGE_VERSION,
        "generated_at_utc": _timestamp(),
        "source_policy": "processed aggregate datasets only; no API requests during viewing",
        "public_snapshot": True,
        "active_umbrella_collapse_count": active_collapse_count,
        "corpus_human_review_complete": False,
        "corpus_scientific_status": "blocked_pending_human_corpus_review",
        "filter_dimension_scope": "complete annual network nodes, including missing coordinates",
        "geographic_flow_explorer": {
            "policy_version": _GEOGRAPHIC_FLOW_VERSION,
            "supported_levels": ["macro_region", "subregion", "country"],
            "supported_metrics": ["volume", "partner_share", "normalized_intensity"],
            "time_window": "inclusive complete calendar years from 2010 through 2025",
            "partner_share_definition": (
                "selected endpoint weight divided by all selected endpoint weight attached to "
                "the source geography; an internal flow contributes two source endpoints"
            ),
            "normalized_intensity_definition": (
                "fractional collaboration weight divided by the geometric mean of source and "
                "target full institutional Work-count denominators under the same scope"
            ),
            "display_filter_order": (
                "apply minimum selected collaboration weight and minimum partner share, then "
                "rank cross-geography flows by selected metric with target label and stable "
                "geography ID tie-breaks; internal flow does not consume a Top N arc slot"
            ),
            "line_width_range_px": [0.8, 8.0],
            "line_width_definitions": {
                "volume": (
                    "width_px = min(8.0, 0.8 + 2.25 * log10(1 + selected collaboration weight))"
                ),
                "partner_share": ("width_px = 0.8 + 7.2 * sqrt(min(partner share, 1.0))"),
                "normalized_intensity": (
                    "width_px = 0.8 + 7.2 * sqrt(min(normalized intensity, 1.0)); values "
                    "above 1 saturate at 8.0 px"
                ),
            },
            "arc_geometry": (
                "32-point spherical great-circle interpolation between sourced display anchors"
            ),
            "color_semantics": (
                "arc and partner-marker color encode target macro-region; selected source uses "
                "its stable macro-region color and a distinct diamond outline"
            ),
            "anchor_method": (
                "unweighted spherical mean of distinct organization coordinates supplied by "
                "OpenAlex; display anchor only, not a geographic centroid"
            ),
            "coordinate_source": "OpenAlex institution metadata",
            "coordinate_source_url": _OPENALEX_SOURCE_URL,
            "coordinate_license": _OPENALEX_LICENSE,
            "coordinate_license_url": _OPENALEX_LICENSE_URL,
            "license_verified_at_utc": "2026-08-28T00:00:00Z",
            "source_manifest": ".agent/manifests/institutions.json",
            "source_dataset_sha256": file_sha256(paths["institutions"]),
        },
        "school_ego_map": {
            "policy_version": _SCHOOL_EGO_VERSION,
            "identity_view": "school",
            "source_policy": (
                "render only per-school retained partner rows; rolling rows come from the "
                "GISNET-128 index and latest complete quarter/annual rows extend that index "
                "from validated exact temporal facts; global map/network thresholds are unused"
            ),
            "retained_partner_limit_per_school_period": _SCHOOL_EGO_TOP_K,
            "ranking_definition": (
                "fractional_count descending, full_count descending, stable partner ID; the UI "
                "reranks the retained rows by the selected metric with name and ID tie-breaks"
            ),
            "supported_levels": ["institution", "country", "macro_region"],
            "supported_metrics": [
                "fractional_volume",
                "normalized_intensity",
                "persistence",
            ],
            "geography_aggregation_boundary": (
                "country and macro-region values summarize only the retained institution partners; "
                "fractional volume is summed while intensity and persistence are fractional-"
                "volume-weighted means"
            ),
            "coordinate_policy": (
                "school and institution points use source-provided school-index coordinates; "
                "country and macro-region points use the versioned sourced display anchors; "
                "missing coordinates remain in explicit unmapped companion rows"
            ),
            "query_policy": "DuckDB Parquet predicate pushdown by school ID, corpus, and period",
            "periods": ego_periods,
            "outside_prior_visualization_core_school_count": int(outside_core_ego_schools[0]),
            "partner_row_count": int(ego_coordinate_coverage[0]),
            "mapped_partner_row_count": int(ego_coordinate_coverage[1]),
            "source_manifests": [
                ".agent/manifests/school_index.json",
                ".agent/manifests/school_partner_index.json",
                ".agent/manifests/edges_metrics_year.json",
                ".agent/manifests/collaboration_edges_quarter.json",
                ".agent/manifests/collaboration_edges_month.json",
                ".agent/manifests/institution_outputs_quarter.json",
            ],
        },
        "school_profile": {
            "policy_version": _SCHOOL_PROFILE_VERSION,
            "identity_view": "school",
            "default_rolling_window_months": 24,
            "supported_rolling_windows": [
                {
                    "window_months": int(row[0]),
                    "earliest_window_start": str(row[1]),
                    "latest_window_end": str(row[2]),
                    "profile_row_count": int(row[3]),
                }
                for row in profile_window_rows
            ],
            "section_order": [
                "identity_geography",
                "recent_activity_trend",
                "topic_profile",
                "institutional_partners",
                "partner_geography",
                "annual_network_position",
                "citation_influence",
                "research_neighbor_institutions",
                "date_data_quality",
            ],
            "time_policy": (
                "recent activity uses source-stored rolling 12-, 24-, and 36-month horizons; "
                "network, citation-flow, and research-proximity context use separately labelled "
                "latest complete annual evidence"
            ),
            "evidence_boundary": (
                "Topic similarity is research proximity and never collaboration; citation flow "
                "is a directed knowledge-flow proxy and never co-authorship or research quality"
            ),
            "missing_data_policy": (
                "unsupported, empty, incomplete, and low-coverage evidence remains explicit; "
                "no value is imputed"
            ),
            "low_date_coverage_display_threshold": 0.8,
            "low_date_coverage_threshold_semantics": (
                "dashboard diagnostic warning only; not a scientific inclusion or exclusion rule"
            ),
            "query_policy": (
                "DuckDB Parquet predicate pushdown by stable school ID, corpus, school hierarchy, "
                "and rolling-window length"
            ),
            "profile_row_count": int(metrics["school_profiles"]["row_count"]),
            "topic_profile_row_count": int(metrics["school_topic_profiles"]["row_count"]),
            "source_manifests": [
                ".agent/manifests/school_profiles.json",
                ".agent/manifests/school_topic_profiles.json",
            ],
        },
        "school_comparison": {
            "policy_version": _SCHOOL_COMPARISON_VERSION,
            "identity_view": "school",
            "minimum_school_count": 2,
            "maximum_school_count": 4,
            "default_rolling_window_months": 24,
            "supported_rolling_window_months": [int(row[0]) for row in profile_window_rows],
            "displayed_topic_family_limit": 6,
            "dimensions": [
                "recent_output",
                "rolling_activity_trend",
                "topic_distribution",
                "international_collaboration_share",
                "partner_diversity",
                "regional_orientation",
                "annual_network_centrality",
                "directed_citation_flow",
            ],
            "source_policy": (
                "reuse the exact School Profile and Topic-profile rows selected by stable school "
                "ID, corpus, school hierarchy, and rolling-window length"
            ),
            "scale_policy": (
                "all schools share one axis within a metric; different-unit centrality panels "
                "use separately disclosed axes; share metrics use a fixed zero-to-one scale"
            ),
            "denominator_policy": (
                "activity uses included institutional Works; collaboration-orientation shares "
                "divide classified collaborative Works by included institutional Works; Topic "
                "shares divide assigned Topic weight by all assigned Topic weight"
            ),
            "representation_policy": (
                "ranked bars, common-scale line and grouped-bar views, and exact aligned tables; "
                "no radar chart, composite score, or hidden per-school normalization"
            ),
            "missing_data_policy": (
                "missing profile and school-Topic observations remain explicit and are never "
                "imputed as zero"
            ),
            "interpretation_boundary": (
                "separate descriptive dimensions only; no university ranking, admissions "
                "recommendation, institutional-quality score, or universal-best-school claim"
            ),
            "profile_row_count": int(metrics["school_profiles"]["row_count"]),
            "topic_profile_row_count": int(metrics["school_topic_profiles"]["row_count"]),
            "source_manifests": [
                ".agent/manifests/school_profiles.json",
                ".agent/manifests/school_topic_profiles.json",
            ],
        },
        "scientific_layers": {
            "policy_version": _SCIENTIFIC_LAYER_VERSION,
            "layers_remain_separate": True,
            "composite_scientific_edge_weight_defined": False,
            "comparison_boundary": (
                "co-authorship, directed citation flow, and Topic-profile proximity retain "
                "incomparable units, independent coverage, and separate interpretations"
            ),
            "coauthorship": {
                "label": "Publication collaboration",
                "directionality": "undirected",
                "semantics": "two institutions co-occur on an included scholarly Work",
                "public_core_institution_limit": 500,
                "public_edge_limit_per_view": 1000,
                "source_manifest": ".agent/manifests/network_view_edges_year.json",
            },
            "citation_flow": {
                "label": "Directed knowledge-flow proxy",
                "directionality": str(citation_policy_rows[0][0]),
                "coverage_denominator": str(citation_policy_rows[0][1]),
                "semantics": str(citation_policy_rows[0][2]),
                "public_edge_limit_per_view": _SCIENTIFIC_LAYER_EDGE_LIMIT,
                "public_selection_policy": (
                    "fractional count descending, full count descending, source stable ID, "
                    "target stable ID"
                ),
                "self_flows_preserved": True,
                "negative_lag_evidence_preserved": True,
                "source_manifests": [
                    ".agent/manifests/citation_edges_year.json",
                    ".agent/manifests/citation_flow_coverage_year.json",
                ],
            },
            "topic_proximity": {
                "label": "Topic-profile research proximity",
                "directionality": "undirected",
                "maximum_institutions_per_view": int(topic_policy_rows[0][0]),
                "top_k": int(topic_policy_rows[0][1]),
                "minimum_similarity": float(topic_policy_rows[0][2]),
                "edge_selection_policy": str(topic_policy_rows[0][3]),
                "semantics": str(topic_policy_rows[0][4]),
                "public_edge_limit_per_view": _SCIENTIFIC_LAYER_EDGE_LIMIT,
                "public_selection_policy": (
                    "cosine similarity descending, shared Topic count descending, source "
                    "stable ID, target stable ID"
                ),
                "provisional_topic_registry": True,
                "source_manifests": [
                    ".agent/manifests/topic_similarity_edges_year.json",
                    ".agent/manifests/topic_similarity_coverage_year.json",
                ],
            },
            "layer_summary_source_manifest": (".agent/manifests/multiplex_layer_summary_year.json"),
        },
        "tables": {
            name: {
                "path": f"dashboard/data/{destinations[name].name}",
                "row_count": int(metrics[name]["row_count"]),
                "sha256": hashes[name],
            }
            for name in sorted(destinations)
        },
        "known_limitations": [
            "Institution coordinate coverage is reported per view; coordinates are never invented.",
            "Geographic anchors are research-institution coordinate means for display, not "
            "geometric or political centroids.",
            "Network and Topic pages use a thresholded 500-node core and top 1,000 edges per view.",
            "The Topic registry is provisional and has not received human review.",
            "The corpus-boundary annotation sample is unlabelled and awaits human review.",
            "2025 is the last complete calendar year; no partial 2026 data are included.",
            "Visualization score is non-primary and used only to rank visible edges.",
            "Community continuity matches below Jaccard 0.25 are explicitly uncertain.",
            "Citation-flow and Topic-proximity edge pages retain only the top 1,000 exact edges "
            "per annual corpus/hierarchy view; complete-layer counts and coverage remain visible.",
        ],
    }
    _validate_public_metadata(payload)
    metadata_temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    for name, path in temporary.items():
        os.replace(path, destinations[name])
    os.replace(metadata_temporary, metadata)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "source_sha256": {name: file_sha256(path) for name, path in sorted(paths.items())},
            }
        ),
        "table_count": len(destinations),
        "row_count": sum(int(value["row_count"]) for value in metrics.values()),
        "table_hashes": hashes,
        "public_snapshot": True,
        "api_requests_during_viewing": False,
        "data_version": payload["data_version"],
        "methods_version": payload["methods_version"],
        "outputs": {
            "dashboard_metadata": str(metadata),
            "dashboard_data_directory": str(output),
        },
        "generated_at_utc": _timestamp(),
    }


def _validate_public_metadata(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload).lower()
    forbidden = ("openalex_api_key", "api_key=", "/home/", ".env")
    found = [value for value in forbidden if value in serialized]
    if found:
        raise ValueError(f"dashboard metadata contains forbidden private values: {found}")


def write_dashboard_artifact(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    write_json_artifact(
        path=summary_path,
        dataset_name="dashboard_bundle_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes={"project": config_file_hash(project_config_path)},
        source_versions={"dashboard_bundle_policy": _STAGE_VERSION},
        source_manifests=[
            ".agent/manifests/trend_series_year.json",
            ".agent/manifests/collaboration_matrix_year.json",
            ".agent/manifests/map_nodes_year.json",
            ".agent/manifests/map_edges_year.json",
            ".agent/manifests/map_coverage_year.json",
            ".agent/manifests/network_view_nodes_year.json",
            ".agent/manifests/network_view_edges_year.json",
            ".agent/manifests/network_accessibility_year.json",
            ".agent/manifests/graph_metrics_year.json",
            ".agent/manifests/sensitivity_matrix.json",
            ".agent/manifests/community_continuity_year.json",
            ".agent/manifests/community_transitions_year.json",
            ".agent/manifests/institution_hierarchy.json",
            ".agent/manifests/institutions.json",
            ".agent/manifests/nodes_year.json",
            ".agent/manifests/school_index.json",
            ".agent/manifests/school_partner_index.json",
            ".agent/manifests/edges_metrics_year.json",
            ".agent/manifests/collaboration_edges_quarter.json",
            ".agent/manifests/collaboration_edges_month.json",
            ".agent/manifests/institution_outputs_quarter.json",
            ".agent/manifests/school_profiles.json",
            ".agent/manifests/school_topic_profiles.json",
            ".agent/manifests/citation_edges_year.json",
            ".agent/manifests/citation_flow_coverage_year.json",
            ".agent/manifests/topic_similarity_edges_year.json",
            ".agent/manifests/topic_similarity_coverage_year.json",
            ".agent/manifests/multiplex_layer_summary_year.json",
        ],
        command=command,
    )


def _write_school_profile_table(
    connection: duckdb.DuckDBPyConnection,
    *,
    source: Path,
    destination: Path,
) -> None:
    """Publish a profile table with the dashboard's stable ``school_id`` field name."""
    connection.execute(
        f"""
        COPY (
            SELECT canonical_school_id AS school_id, * EXCLUDE (canonical_school_id)
            FROM read_parquet(?)
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """,
        [str(source)],
    )


def _write_public_citation_edges(
    connection: duckdb.DuckDBPyConnection,
    *,
    source: Path,
    destination: Path,
    edge_limit_per_view: int,
) -> None:
    """Publish a compact, direction-preserving citation subset with explicit rank policy."""
    if edge_limit_per_view <= 0:
        raise ValueError("citation-flow public edge limit must be positive")
    connection.execute(
        f"""
        COPY (
            WITH ranked AS (
                SELECT *, row_number() OVER (
                    PARTITION BY year, corpus_view, hierarchy_view
                    ORDER BY fractional_count DESC, full_count DESC, source_id, target_id
                )::INTEGER AS public_edge_rank
                FROM read_parquet(?)
            )
            SELECT *,
                   {edge_limit_per_view}::INTEGER AS public_edge_limit,
                   'top directed edges by fractional count descending, full count descending, '
                   || 'source stable ID, target stable ID' AS public_selection_policy
            FROM ranked
            WHERE public_edge_rank <= {edge_limit_per_view}
            ORDER BY year, corpus_view, hierarchy_view, public_edge_rank, source_id, target_id
        ) TO '{_literal(destination)}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """,
        [str(source)],
    )


def _write_public_topic_similarity_edges(
    connection: duckdb.DuckDBPyConnection,
    *,
    source: Path,
    destination: Path,
    edge_limit_per_view: int,
) -> None:
    """Publish top exact Topic-proximity rows without changing source core thresholds."""
    if edge_limit_per_view <= 0:
        raise ValueError("Topic-proximity public edge limit must be positive")
    connection.execute(
        f"""
        COPY (
            WITH ranked AS (
                SELECT *, row_number() OVER (
                    PARTITION BY year, corpus_view, hierarchy_view
                    ORDER BY cosine_similarity DESC, shared_topic_count DESC, source_id, target_id
                )::INTEGER AS public_edge_rank
                FROM read_parquet(?)
            )
            SELECT *,
                   {edge_limit_per_view}::INTEGER AS public_edge_limit,
                   'top research-proximity edges by cosine similarity descending, shared Topic '
                   || 'count descending, source stable ID, target stable ID'
                       AS public_selection_policy
            FROM ranked
            WHERE public_edge_rank <= {edge_limit_per_view}
            ORDER BY year, corpus_view, hierarchy_view, public_edge_rank, source_id, target_id
        ) TO '{_literal(destination)}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """,
        [str(source)],
    )


def _write_school_dashboard_index(
    connection: duckdb.DuckDBPyConnection,
    *,
    school_index_path: Path,
    rolling_partner_path: Path,
    network_nodes_path: Path,
    destination: Path,
) -> None:
    """Write the complete searchable school index independently of visualization thresholds."""
    connection.execute(
        f"""
        COPY (
            SELECT
                s.canonical_school_id AS school_id,
                s.institution_id,
                s.display_name,
                s.alternative_names,
                s.search_names,
                s.has_ambiguous_name_match,
                s.country_code,
                s.country_name,
                s.macro_region,
                s.subregion,
                s.institution_category,
                s.analytical_scope,
                s.openalex_id,
                s.ror_id,
                s.latitude,
                s.longitude,
                s.coordinate_source,
                s.has_coordinates,
                s.first_observed_date,
                s.last_observed_date,
                s.latest_supported_month,
                s.broad_work_count,
                s.strict_work_count,
                s.recent_24m_work_count,
                s.topic_families,
                s.date_coverage_ratio,
                s.identity_status,
                s.identity_resolution_confidence,
                s.identity_quality_flags,
                s.eligibility_status,
                s.support_status,
                EXISTS (
                    SELECT 1 FROM read_parquet('{_literal(network_nodes_path)}') n
                    WHERE n.institution_id = s.canonical_school_id
                ) AS in_prior_visualization_core,
                EXISTS (
                    SELECT 1 FROM read_parquet('{_literal(rolling_partner_path)}') p
                    WHERE p.school_id = s.canonical_school_id
                ) AS has_retained_ego_partners
            FROM read_parquet('{_literal(school_index_path)}') s
            ORDER BY lower(s.display_name), s.canonical_school_id
        ) TO '{_literal(destination)}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )


def _write_school_ego_partners(
    connection: duckdb.DuckDBPyConnection,
    *,
    school_index_path: Path,
    rolling_partner_path: Path,
    annual_edges_path: Path,
    quarter_edges_path: Path,
    month_edges_path: Path,
    quarter_outputs_path: Path,
    destination: Path,
    top_k: int,
) -> None:
    """Write one predicate-friendly top-partner index across supported latest periods."""
    if top_k <= 0:
        raise ValueError("School Ego Map top_k must be positive")
    school_path = _literal(school_index_path)
    rolling_path = _literal(rolling_partner_path)
    annual_path = _literal(annual_edges_path)
    quarter_path = _literal(quarter_edges_path)
    month_path = _literal(month_edges_path)
    quarter_output_path = _literal(quarter_outputs_path)
    output_path = _literal(destination)
    connection.execute(
        f"""
        COPY (
            WITH schools AS (
                SELECT canonical_school_id AS school_id, display_name, country_code, country_name,
                       macro_region, subregion, latitude, longitude, coordinate_source
                FROM read_parquet('{school_path}')
                WHERE support_status = 'supported'
            ), rolling AS (
                SELECT
                    'rolling' AS time_basis,
                    'rolling_' || r.window_months || 'm' AS period_key,
                    'Rolling ' || r.window_months || ' months · ' || r.window_start || ' to '
                        || r.window_end AS period_label,
                    r.window_start AS period_start,
                    r.window_end AS period_end,
                    r.window_months,
                    'month' AS persistence_unit,
                    r.window_months AS persistence_denominator,
                    'active publication months divided by rolling window months'
                        AS persistence_definition,
                    r.corpus_view,
                    'school' AS hierarchy_view,
                    r.school_id,
                    s.display_name AS school_name,
                    s.country_code AS school_country,
                    s.country_name AS school_country_name,
                    s.macro_region AS school_macro_region,
                    s.subregion AS school_subregion,
                    s.latitude AS school_latitude,
                    s.longitude AS school_longitude,
                    s.coordinate_source AS school_coordinate_source,
                    r.partner_id,
                    p.display_name AS partner_name,
                    p.country_code AS partner_country,
                    p.country_name AS partner_country_name,
                    p.macro_region AS partner_macro_region,
                    p.subregion AS partner_subregion,
                    p.latitude AS partner_latitude,
                    p.longitude AS partner_longitude,
                    p.coordinate_source AS partner_coordinate_source,
                    r.full_count,
                    r.fractional_count,
                    r.distinct_work_count,
                    r.source_work_count,
                    r.target_work_count,
                    r.normalized_intensity,
                    r.active_month_count AS active_period_count,
                    r.edge_persistence AS persistence,
                    r.partner_rank,
                    r.coverage_ratio,
                    r.is_complete_window AS is_complete_period,
                    r.is_complete_window AS persistence_is_complete,
                    'GISNET-128 school_partner_index' AS source_partner_index,
                    r.support_status
                FROM read_parquet('{rolling_path}') r
                JOIN schools s ON r.school_id = s.school_id
                JOIN schools p ON r.partner_id = p.school_id
            ), annual_latest AS (
                SELECT corpus_view, max(year)::INTEGER AS period_year
                FROM read_parquet('{annual_path}')
                WHERE hierarchy_view = 'organization'
                GROUP BY corpus_view
            ), annual_base AS (
                SELECT e.*
                FROM read_parquet('{annual_path}') e
                JOIN annual_latest l USING (corpus_view)
                WHERE e.hierarchy_view = 'organization' AND e.year = l.period_year
            ), annual_directed AS (
                SELECT corpus_view, year, source_id AS school_id, target_id AS partner_id,
                       full_count, fractional_count, distinct_work_count,
                       source_work_count, target_work_count, normalized_intensity,
                       active_years_5y AS active_period_count, persistence_5y AS persistence,
                       NOT persistence_5y_incomplete_window AS persistence_is_complete
                FROM annual_base
                UNION ALL
                SELECT corpus_view, year, target_id AS school_id, source_id AS partner_id,
                       full_count, fractional_count, distinct_work_count,
                       target_work_count AS source_work_count,
                       source_work_count AS target_work_count, normalized_intensity,
                       active_years_5y AS active_period_count, persistence_5y AS persistence,
                       NOT persistence_5y_incomplete_window AS persistence_is_complete
                FROM annual_base
            ), annual_labelled AS (
                SELECT d.*, s.display_name AS school_name,
                       s.country_code AS school_country, s.country_name AS school_country_name,
                       s.macro_region AS school_macro_region, s.subregion AS school_subregion,
                       s.latitude AS school_latitude, s.longitude AS school_longitude,
                       s.coordinate_source AS school_coordinate_source,
                       p.display_name AS partner_name, p.country_code AS partner_country,
                       p.country_name AS partner_country_name,
                       p.macro_region AS partner_macro_region, p.subregion AS partner_subregion,
                       p.latitude AS partner_latitude, p.longitude AS partner_longitude,
                       p.coordinate_source AS partner_coordinate_source
                FROM annual_directed d
                JOIN schools s ON d.school_id = s.school_id
                JOIN schools p ON d.partner_id = p.school_id
            ), annual_ranked AS (
                SELECT *, row_number() OVER (
                    PARTITION BY corpus_view, school_id
                    ORDER BY fractional_count DESC, full_count DESC, partner_id
                )::INTEGER AS partner_rank
                FROM annual_labelled
            ), annual AS (
                SELECT
                    'annual' AS time_basis,
                    'annual_' || year AS period_key,
                    'Complete year ' || year AS period_label,
                    year || '-01' AS period_start,
                    year || '-12' AS period_end,
                    12::INTEGER AS window_months,
                    'year' AS persistence_unit,
                    5::INTEGER AS persistence_denominator,
                    'active publication years in the trailing five-year window divided by 5'
                        AS persistence_definition,
                    corpus_view,
                    'school' AS hierarchy_view,
                    school_id, school_name, school_country, school_country_name,
                    school_macro_region, school_subregion, school_latitude, school_longitude,
                    school_coordinate_source,
                    partner_id, partner_name, partner_country, partner_country_name,
                    partner_macro_region, partner_subregion, partner_latitude, partner_longitude,
                    partner_coordinate_source,
                    full_count, fractional_count, distinct_work_count,
                    source_work_count, target_work_count, normalized_intensity,
                    active_period_count, persistence, partner_rank,
                    1.0::DOUBLE AS coverage_ratio,
                    true AS is_complete_period,
                    persistence_is_complete,
                    'latest complete-year per-school extension from edges_metrics_year'
                        AS source_partner_index,
                    'supported' AS support_status
                FROM annual_ranked
                WHERE partner_rank <= {top_k}
            ), quarter_latest AS (
                SELECT corpus_view, max(publication_quarter) AS period_quarter
                FROM read_parquet('{quarter_path}')
                WHERE hierarchy_view = 'organization'
                GROUP BY corpus_view
            ), quarter_base AS (
                SELECT e.*
                FROM read_parquet('{quarter_path}') e
                JOIN quarter_latest l USING (corpus_view)
                WHERE e.hierarchy_view = 'organization'
                  AND e.publication_quarter = l.period_quarter
            ), quarter_activity AS (
                SELECT e.corpus_view, e.source_id, e.target_id,
                       count(*)::BIGINT AS active_period_count
                FROM read_parquet('{month_path}') e
                JOIN quarter_latest l ON e.corpus_view = l.corpus_view
                WHERE e.hierarchy_view = 'organization'
                  AND e.publication_year = cast(substr(l.period_quarter, 1, 4) AS INTEGER)
                  AND ceil(cast(substr(e.publication_month, 6, 2) AS DOUBLE) / 3.0)::INTEGER
                      = cast(right(l.period_quarter, 1) AS INTEGER)
                GROUP BY e.corpus_view, e.source_id, e.target_id
            ), quarter_outputs AS (
                SELECT o.*
                FROM read_parquet('{quarter_output_path}') o
                JOIN quarter_latest l ON o.corpus_view = l.corpus_view
                WHERE o.hierarchy_view = 'organization'
                  AND o.publication_quarter = l.period_quarter
            ), quarter_directed AS (
                SELECT e.corpus_view, e.publication_quarter,
                       e.source_id AS school_id, e.target_id AS partner_id,
                       e.full_count, e.fractional_count, e.distinct_work_count,
                       s.work_count AS source_work_count, t.work_count AS target_work_count,
                       e.fractional_count / sqrt(s.work_count * t.work_count)
                           AS normalized_intensity,
                       coalesce(a.active_period_count, 1)::BIGINT AS active_period_count
                FROM quarter_base e
                JOIN quarter_outputs s ON e.corpus_view = s.corpus_view
                    AND e.source_id = s.institution_id
                JOIN quarter_outputs t ON e.corpus_view = t.corpus_view
                    AND e.target_id = t.institution_id
                LEFT JOIN quarter_activity a ON e.corpus_view = a.corpus_view
                    AND e.source_id = a.source_id AND e.target_id = a.target_id
                UNION ALL
                SELECT e.corpus_view, e.publication_quarter,
                       e.target_id AS school_id, e.source_id AS partner_id,
                       e.full_count, e.fractional_count, e.distinct_work_count,
                       t.work_count AS source_work_count, s.work_count AS target_work_count,
                       e.fractional_count / sqrt(s.work_count * t.work_count)
                           AS normalized_intensity,
                       coalesce(a.active_period_count, 1)::BIGINT AS active_period_count
                FROM quarter_base e
                JOIN quarter_outputs s ON e.corpus_view = s.corpus_view
                    AND e.source_id = s.institution_id
                JOIN quarter_outputs t ON e.corpus_view = t.corpus_view
                    AND e.target_id = t.institution_id
                LEFT JOIN quarter_activity a ON e.corpus_view = a.corpus_view
                    AND e.source_id = a.source_id AND e.target_id = a.target_id
            ), quarter_labelled AS (
                SELECT d.*, s.display_name AS school_name,
                       s.country_code AS school_country, s.country_name AS school_country_name,
                       s.macro_region AS school_macro_region, s.subregion AS school_subregion,
                       s.latitude AS school_latitude, s.longitude AS school_longitude,
                       s.coordinate_source AS school_coordinate_source,
                       p.display_name AS partner_name, p.country_code AS partner_country,
                       p.country_name AS partner_country_name,
                       p.macro_region AS partner_macro_region, p.subregion AS partner_subregion,
                       p.latitude AS partner_latitude, p.longitude AS partner_longitude,
                       p.coordinate_source AS partner_coordinate_source
                FROM quarter_directed d
                JOIN schools s ON d.school_id = s.school_id
                JOIN schools p ON d.partner_id = p.school_id
            ), quarter_ranked AS (
                SELECT *, row_number() OVER (
                    PARTITION BY corpus_view, school_id
                    ORDER BY fractional_count DESC, full_count DESC, partner_id
                )::INTEGER AS partner_rank
                FROM quarter_labelled
            ), quarterly AS (
                SELECT
                    'quarterly' AS time_basis,
                    'quarter_' || publication_quarter AS period_key,
                    'Complete quarter ' || publication_quarter AS period_label,
                    substr(publication_quarter, 1, 4) || '-' || lpad(cast(
                        (cast(right(publication_quarter, 1) AS INTEGER) - 1) * 3 + 1
                        AS VARCHAR), 2, '0') AS period_start,
                    substr(publication_quarter, 1, 4) || '-' || lpad(cast(
                        cast(right(publication_quarter, 1) AS INTEGER) * 3
                        AS VARCHAR), 2, '0') AS period_end,
                    3::INTEGER AS window_months,
                    'month' AS persistence_unit,
                    3::INTEGER AS persistence_denominator,
                    'active publication months in the selected complete quarter divided by 3'
                        AS persistence_definition,
                    corpus_view,
                    'school' AS hierarchy_view,
                    school_id, school_name, school_country, school_country_name,
                    school_macro_region, school_subregion, school_latitude, school_longitude,
                    school_coordinate_source,
                    partner_id, partner_name, partner_country, partner_country_name,
                    partner_macro_region, partner_subregion, partner_latitude, partner_longitude,
                    partner_coordinate_source,
                    full_count, fractional_count, distinct_work_count,
                    source_work_count, target_work_count, normalized_intensity,
                    active_period_count,
                    active_period_count::DOUBLE / 3.0 AS persistence,
                    partner_rank,
                    1.0::DOUBLE AS coverage_ratio,
                    true AS is_complete_period,
                    true AS persistence_is_complete,
                    'latest complete-quarter per-school extension from exact subannual facts'
                        AS source_partner_index,
                    'supported' AS support_status
                FROM quarter_ranked
                WHERE partner_rank <= {top_k}
            )
            SELECT * FROM rolling
            UNION ALL SELECT * FROM quarterly
            UNION ALL SELECT * FROM annual
            ORDER BY period_key, corpus_view, school_id, partner_rank, partner_id
        ) TO '{output_path}' (FORMAT PARQUET, COMPRESSION ZSTD)
        """
    )


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _write_filter_dimensions(
    connection: duckdb.DuckDBPyConnection,
    complete_nodes_path: Path,
    destination: Path,
) -> None:
    """Write filter choices from all network nodes, never the coordinate-limited map subset."""
    connection.execute(
        f"""
        COPY (
            SELECT DISTINCT year, corpus_view, hierarchy_view, dimension, value
            FROM (
                SELECT year, corpus_view, hierarchy_view, 'country' AS dimension,
                       country_name AS value
                FROM read_parquet(?) WHERE country_name IS NOT NULL
                UNION ALL
                SELECT year, corpus_view, hierarchy_view, 'subregion', subregion
                FROM read_parquet(?) WHERE subregion IS NOT NULL
                UNION ALL
                SELECT year, corpus_view, hierarchy_view, 'institution_type',
                       institution_category
                FROM read_parquet(?) WHERE institution_category IS NOT NULL
            )
            ORDER BY year, corpus_view, hierarchy_view, dimension, value
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """,
        [str(complete_nodes_path)] * 3,
    )


def _write_geography_dimensions(
    connection: duckdb.DuckDBPyConnection,
    complete_nodes_path: Path,
    destination: Path,
) -> None:
    """Write stable country-code labels from complete nodes, independent of coordinates."""
    connection.execute(
        f"""
        COPY (
            SELECT DISTINCT country_code, country_name, macro_region, subregion
            FROM read_parquet(?)
            WHERE country_code IS NOT NULL
              AND country_name IS NOT NULL
              AND macro_region IS NOT NULL
              AND subregion IS NOT NULL
            ORDER BY country_code
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """,
        [str(complete_nodes_path)],
    )


def _write_geography_anchors(
    connection: duckdb.DuckDBPyConnection,
    *,
    complete_nodes_path: Path,
    institutions_path: Path,
    destination: Path,
    source_dataset_sha256: str,
) -> None:
    """Write versioned display anchors from distinct source-provided institution coordinates."""
    connection.execute(
        f"""
        COPY (
            WITH node_geography AS (
                SELECT DISTINCT
                    institution_id,
                    country_code,
                    country_name,
                    macro_region,
                    subregion,
                    latitude,
                    longitude
                FROM read_parquet(?)
                WHERE hierarchy_view = 'organization'
                  AND latitude IS NOT NULL
                  AND longitude IS NOT NULL
            ), sourced AS (
                SELECT node_geography.*, institutions.coordinate_source
                FROM node_geography
                INNER JOIN read_parquet(?) institutions USING (institution_id)
                WHERE institutions.coordinate_source IS NOT NULL
            ), expanded AS (
                SELECT
                    institution_id,
                    latitude,
                    longitude,
                    coordinate_source,
                    unnest([
                        {{'geographic_level': 'macro_region',
                          'geography': macro_region,
                          'display_name': macro_region,
                          'macro_region': macro_region}},
                        {{'geographic_level': 'subregion',
                          'geography': subregion,
                          'display_name': subregion,
                          'macro_region': macro_region}},
                        {{'geographic_level': 'country',
                          'geography': country_code,
                          'display_name': country_name,
                          'macro_region': macro_region}}
                    ]) AS anchor
                FROM sourced
            ), components AS (
                SELECT
                    anchor.geographic_level AS geographic_level,
                    anchor.geography AS geography,
                    min(anchor.display_name) AS display_name,
                    min(anchor.macro_region) AS macro_region,
                    avg(cos(radians(latitude)) * cos(radians(longitude))) AS mean_x,
                    avg(cos(radians(latitude)) * sin(radians(longitude))) AS mean_y,
                    avg(sin(radians(latitude))) AS mean_z,
                    count(DISTINCT institution_id)::BIGINT AS supporting_institution_count,
                    count(*)::BIGINT AS source_coordinate_count,
                    string_agg(
                        DISTINCT coordinate_source,
                        '|' ORDER BY coordinate_source
                    ) AS coordinate_source
                FROM expanded
                WHERE anchor.geography IS NOT NULL
                GROUP BY anchor.geographic_level, anchor.geography
            )
            SELECT
                geographic_level,
                geography,
                display_name,
                macro_region,
                round(
                    degrees(atan2(mean_z, sqrt(mean_x * mean_x + mean_y * mean_y))),
                    10
                ) AS latitude,
                round(degrees(atan2(mean_y, mean_x)), 10) AS longitude,
                supporting_institution_count,
                source_coordinate_count,
                coordinate_source,
                'unweighted spherical mean of distinct sourced organization coordinates, '
                    || 'rounded to 10 decimal degrees for deterministic serialization'
                    AS anchor_method,
                '{_GEOGRAPHIC_ANCHOR_VERSION}' AS anchor_policy_version,
                'OpenAlex institution metadata' AS coordinate_source_dataset,
                '{_OPENALEX_SOURCE_URL}' AS coordinate_source_url,
                '{_OPENALEX_LICENSE}' AS coordinate_license,
                '{_OPENALEX_LICENSE_URL}' AS coordinate_license_url,
                '.agent/manifests/institutions.json' AS source_manifest,
                '{source_dataset_sha256}' AS source_dataset_sha256
            FROM components
            ORDER BY geographic_level, geography
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """,
        [str(complete_nodes_path), str(institutions_path)],
    )


def _write_geography_outputs(
    connection: duckdb.DuckDBPyConnection,
    *,
    complete_nodes_path: Path,
    destination: Path,
) -> None:
    """Write exact geography-level output denominators for normalized flow intensity."""
    connection.execute(
        f"""
        COPY (
            WITH expanded AS (
                SELECT
                    year,
                    corpus_view,
                    hierarchy_view,
                    work_count,
                    fractional_work_count,
                    unnest([
                        {{'geographic_level': 'macro_region', 'geography': macro_region}},
                        {{'geographic_level': 'subregion', 'geography': subregion}},
                        {{'geographic_level': 'country', 'geography': country_code}}
                    ]) AS output
                FROM read_parquet(?)
            )
            SELECT
                year,
                corpus_view,
                hierarchy_view,
                output.geographic_level AS geographic_level,
                output.geography AS geography,
                sum(work_count)::BIGINT AS full_work_count,
                sum(fractional_work_count) AS fractional_work_count,
                'sum of institution Work counts under the identical annual graph scope'
                    AS denominator_definition
            FROM expanded
            WHERE output.geography IS NOT NULL
            GROUP BY year, corpus_view, hierarchy_view,
                     output.geographic_level, output.geography
            ORDER BY year, corpus_view, hierarchy_view, geographic_level, geography
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD)
        """,
        [str(complete_nodes_path)],
    )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
