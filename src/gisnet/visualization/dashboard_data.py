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

_STAGE_VERSION = "public-dashboard-bundle-2026-08-28-v7"
_GEOGRAPHIC_FLOW_VERSION = "geographic-flow-explorer-2026-08-28-v2"
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
    }
    required = copied_sources | {"institution_hierarchy", "institutions", "complete_nodes"}
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
        ],
        command=command,
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
                degrees(atan2(mean_z, sqrt(mean_x * mean_x + mean_y * mean_y))) AS latitude,
                degrees(atan2(mean_y, mean_x)) AS longitude,
                supporting_institution_count,
                source_coordinate_count,
                coordinate_source,
                'unweighted spherical mean of distinct sourced organization coordinates'
                    AS anchor_method,
                '{_GEOGRAPHIC_FLOW_VERSION}' AS anchor_policy_version,
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
