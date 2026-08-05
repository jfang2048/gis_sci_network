"""Build machine-readable and Markdown dictionaries for every public release table."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.artifacts import current_git_commit, utc_timestamp, write_json_artifact
from gisnet.atomic import atomic_write_json, atomic_write_text
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256
from gisnet.manifest import DatasetManifest

_STAGE_VERSION = "public-data-dictionary-2026-08-05-v1"

TABLES: dict[str, dict[str, Any]] = {
    "community_continuity": {
        "primary_key": ["year", "corpus_view", "hierarchy_view", "annual_community_id"],
        "source_manifest": ".agent/manifests/community_continuity_year.json",
        "description": "Annual community labels linked to stable longitudinal continuity IDs.",
        "known_issue": "Matches below Jaccard 0.25 are retained but explicitly uncertain.",
    },
    "community_transitions": {
        "primary_key": [
            "transition_year",
            "corpus_view",
            "hierarchy_view",
            "previous_community_key",
            "current_community_key",
        ],
        "source_manifest": ".agent/manifests/community_transitions_year.json",
        "description": "Adjacent-year overlap assignments and split/merge/birth/death events.",
        "known_issue": (
            "Minor positive overlaps are retained separately from event-threshold links."
        ),
    },
    "graph_metrics": {
        "primary_key": ["year", "corpus_view", "hierarchy_view"],
        "source_manifest": ".agent/manifests/graph_metrics_year.json",
        "description": "Annual graph-level topology, connectivity, mixing, and turnover metrics.",
        "known_issue": "Betweenness uses the disclosed cutoff approximation for large graphs.",
    },
    "map_coverage": {
        "primary_key": ["year", "corpus_view", "hierarchy_view"],
        "source_manifest": ".agent/manifests/map_coverage_year.json",
        "description": "Annual sourced-coordinate coverage and default map display limits.",
        "known_issue": "Coordinate coverage is sparse; missing coordinates are never imputed.",
    },
    "map_edges": {
        "primary_key": ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
        "source_manifest": ".agent/manifests/map_edges_year.json",
        "description": (
            "Top display-ranked collaboration edges whose endpoints have sourced coordinates."
        ),
        "known_issue": "This thresholded map extract is not the complete annual edge table.",
    },
    "map_nodes": {
        "primary_key": ["year", "corpus_view", "hierarchy_view", "institution_id"],
        "source_manifest": ".agent/manifests/map_nodes_year.json",
        "description": "Annual institution metrics for nodes with source-provided coordinates.",
        "known_issue": "Absence means unavailable sourced coordinates, not an absent institution.",
    },
    "matrix": {
        "primary_key": [
            "year",
            "corpus_view",
            "hierarchy_view",
            "geographic_level",
            "source_geography",
            "target_geography",
        ],
        "source_manifest": ".agent/manifests/collaboration_matrix_year.json",
        "description": "Sparse macro-region, subregion, and country collaboration matrix cells.",
        "known_issue": "An absent row is missing/no observed flow, never an imputed zero.",
    },
    "network_accessibility": {
        "primary_key": ["year", "corpus_view", "hierarchy_view"],
        "source_manifest": ".agent/manifests/network_accessibility_year.json",
        "description": "Plain-language annual summaries and visible network thresholds.",
        "known_issue": "Counts describe the fixed-layout public view, not every raw affiliation.",
    },
    "network_edges": {
        "primary_key": ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
        "source_manifest": ".agent/manifests/network_view_edges_year.json",
        "description": (
            "Top fixed-layout core edges with weights, persistence, details, and coordinates."
        ),
        "known_issue": "Limited to the top 1,000 edges per view by a non-primary display score.",
    },
    "network_nodes": {
        "primary_key": ["year", "corpus_view", "hierarchy_view", "institution_id"],
        "source_manifest": ".agent/manifests/network_view_nodes_year.json",
        "description": "Fixed-coordinate annual core-node metrics and primary communities.",
        "known_issue": "The public visualization core is thresholded to 500 aggregate nodes.",
    },
    "sensitivity": {
        "primary_key": ["comparison_id"],
        "source_manifest": ".agent/manifests/sensitivity_matrix.json",
        "description": "Required alternative-definition comparisons and change flags.",
        "known_issue": "One reviewed-registry comparison is explicitly unavailable.",
    },
    "topics": {
        "primary_key": ["year", "corpus_view", "hierarchy_view", "topic_family"],
        "source_manifest": ".agent/manifests/network_view_edges_year.json",
        "description": "Topic-family aggregates derived from the visible fixed-layout edge core.",
        "known_issue": "Topic decisions are provisional and the table covers visible edges only.",
    },
    "trends": {
        "primary_key": [
            "year",
            "corpus_view",
            "hierarchy_view",
            "source_region",
            "target_region",
        ],
        "source_manifest": ".agent/manifests/trend_series_year.json",
        "description": (
            "Annual macro-region collaboration trend series for complete calendar years."
        ),
        "known_issue": "The last included year is 2025; partial 2026 observations are excluded.",
    },
}

EXACT_DESCRIPTIONS: dict[str, str] = {
    "id": "Stable institution identifier for an undirected edge endpoint.",
    "name": "Institution display name for an undirected edge endpoint.",
    "region": "Frozen macro-region label for an undirected edge endpoint.",
    "country": "Frozen country name for an undirected edge endpoint.",
    "category": "Configured analytical institution category for an edge endpoint.",
    "institution_type": "Configured analytical institution type for an edge endpoint.",
    "year": "Complete publication calendar year.",
    "corpus_view": "GIS corpus definition: strict or broad.",
    "hierarchy_view": "Institution identity view: organization or documented umbrella.",
    "institution_id": "Stable source institution identifier used as the node key.",
    "display_name": "Source-provided or canonically selected institution display name.",
    "ror_id": "Source-linked Research Organization Registry identifier, when available.",
    "country_code": "Source country code associated with the institution.",
    "country_name": "Frozen country or territory display name.",
    "macro_region": "Frozen UN M49-style macro-region analytical grouping.",
    "subregion": "Frozen UN M49-style subregion analytical grouping.",
    "institution_category": "Configured analytical category for the source institution type.",
    "analytical_scope": "Whether the row is in focal or retained contextual geographic scope.",
    "latitude": "Source-provided institution latitude; never imputed.",
    "longitude": "Source-provided institution longitude; never imputed.",
    "work_count": "Distinct primary-corpus Works affiliated with the institution.",
    "fractional_work_count": "Institutional Work output under the stored fractional allocation.",
    "collaborative_work_count": "Distinct Works containing more than one institution.",
    "single_institution_work_count": "Distinct Works containing only this institution.",
    "international_work_count": "Distinct Works with institutions from multiple countries.",
    "cross_region_work_count": "Distinct Works with institutions from multiple macro-regions.",
    "international_collaboration_share": "International Works divided by collaborative Works.",
    "cross_region_collaboration_share": "Cross-region Works divided by collaborative Works.",
    "degree": "Number of distinct institutional partners in the annual graph.",
    "full_strength": "Sum of incident full-count collaboration edge weights.",
    "fractional_strength": "Sum of incident fractional collaboration edge weights.",
    "betweenness": "Stored weighted betweenness centrality under the disclosed method.",
    "betweenness_method": "Exact or cutoff weighted shortest-path method used for betweenness.",
    "betweenness_sample_size": "Number of graph vertices included by the betweenness method.",
    "betweenness_cutoff": "Maximum path length for approximated betweenness; null when exact.",
    "betweenness_seed": "Deterministic random seed recorded for the centrality computation.",
    "pagerank": "Weighted PageRank centrality normalized within the annual graph.",
    "bridge_score": "Documented cross-community/cross-region bridging indicator.",
    "partner_country_count": "Number of distinct partner countries.",
    "partner_region_count": "Number of distinct partner macro-regions.",
    "full_count": "Full-count collaboration weight: one per institution pair per Work.",
    "fractional_count": "Fractional weight: one divided by the number of pairs on each Work.",
    "distinct_work_count": "Number of distinct source Work identifiers contributing to the row.",
    "large_consortium_work_count": "Contributing Works at or above the consortium warning size.",
    "excluded_threshold_work_count": "Contributing Works excluded by the configured size policy.",
    "maximum_consortium_size": "Largest distinct-institution count among contributing Works.",
    "topic_families": "Sorted distinct configured Topic families observed on contributing Works.",
    "work_ids_sample": "Deterministic bounded sample of contributing source Work identifiers.",
    "distinct_topic_family_count": "Number of distinct Topic families contributing to the row.",
    "active_years_3y": "Active years in the fixed-denominator trailing three-year window.",
    "active_years_5y": "Active years in the fixed-denominator trailing five-year window.",
    "normalized_intensity": "Fractional weight divided by geometric-mean institutional output.",
    "persistence_3y": "Active-year share in the trailing three-year window.",
    "persistence_5y": "Active-year share in the trailing five-year window.",
    "persistence_3y_incomplete_window": "True before a complete three-year history is available.",
    "persistence_5y_incomplete_window": "True before a complete five-year history is available.",
    "visualization_score": "Non-primary composite used only to rank edges for display.",
    "visualization_score_is_primary": "Always false; guards against scientific interpretation.",
    "visualization_score_method": "Stored description of the display-ranking calculation.",
    "default_edge_rank": "One-based rank under the default edge display policy.",
    "default_edge_limit": "Documented maximum edges displayed by default per view.",
    "default_node_rank": "One-based rank under the default node display policy.",
    "default_node_limit": "Documented maximum nodes displayed by default per view.",
    "default_threshold_method": "Stored description of the default display threshold.",
    "coordinate_policy": "Statement that coordinates are source-provided and never imputed.",
    "coordinate_encoding": "Stored description of the fixed node-coordinate encoding.",
    "node_size_encoding": "Stored description of the default node-size field.",
    "node_color_encoding": "Stored description of the default node-color field.",
    "edge_width_encoding": "Stored description of the default edge-width field.",
    "edge_color_encoding": "Stored description of the default edge-color field.",
    "x": "Seeded aggregate-layout horizontal coordinate reused across years.",
    "y": "Seeded aggregate-layout vertical coordinate reused across years.",
    "core_rank": "Rank in the full-period aggregate visualization core.",
    "community_id": "Stable annual primary-resolution community label; isolates are explicit.",
    "community_size": "Number of nodes assigned to the labelled annual community.",
    "geographic_level": "Matrix level: macro_region, subregion, or country.",
    "source_geography": "Stable first endpoint label of the undirected geographic cell.",
    "target_geography": "Stable second endpoint label of the undirected geographic cell.",
    "distinct_institution_pair_count": "Distinct source institution pairs contributing to the row.",
    "normalized_share": "Fractional cell weight divided by the applicable annual total.",
    "source_order": "Stable display order for the source geography.",
    "target_order": "Stable display order for the target geography.",
    "cell_status": "Explicit observed/missing semantic label for the matrix cell.",
    "absent_cell_semantics": "Statement defining an absent sparse matrix row.",
    "region_pair": "Stable unordered macro-region pair display label.",
    "macro_region_pair": "Stable unordered source/target macro-region pair label.",
    "is_intra_region": "True when both geographic endpoints are the same macro-region.",
    "year_status": "Complete-year status label.",
    "units_note": "Human-readable statement of stored counting units.",
    "comparison_id": "Stable sensitivity-comparison identifier.",
    "comparison": "Human-readable sensitivity question.",
    "metric": "Metric compared between the baseline and alternative.",
    "baseline_label": "Label for the primary/baseline analytical choice.",
    "alternative_label": "Label for the alternative analytical choice.",
    "baseline_value": "Measured metric value under the baseline choice.",
    "alternative_value": "Measured metric value under the alternative choice.",
    "absolute_difference": "Absolute alternative-minus-baseline metric difference.",
    "absolute_relative_change": "Absolute difference divided by the baseline magnitude.",
    "major_change": "Whether change meets the documented major-change rule.",
    "major_change_rule": "Stored threshold rule used to set major_change.",
    "status": "Availability/completion state of the sensitivity comparison.",
    "primary_result_overwritten": "Always false; alternatives never replace primary results.",
    "topic_family": "Configured Topic-family label.",
    "visible_edge_count": "Number of visible fixed-layout edges in the aggregate.",
    "edge_work_count_sum": "Sum of edge-level distinct Work counts; not globally deduplicated.",
    "coverage_note": "Statement delimiting the public Topic aggregate coverage.",
    "node_count": "Number of nodes in the annual graph or public view.",
    "edge_count": "Number of undirected edges in the annual graph or public view.",
    "density": "Observed edges divided by possible undirected edges.",
    "mean_degree": "Arithmetic mean annual node degree.",
    "mean_full_strength": "Arithmetic mean annual full-count node strength.",
    "mean_fractional_strength": "Arithmetic mean annual fractional node strength.",
    "connected_component_count": "Number of connected components in the annual graph.",
    "largest_connected_component_share": "Share of nodes in the largest connected component.",
    "modularity": "Primary-resolution Leiden partition modularity.",
    "modularity_resolution": "Leiden resolution associated with stored modularity.",
    "macro_region_assortativity": "Categorical assortativity by macro-region.",
    "country_assortativity": "Categorical assortativity by country.",
    "cross_region_edge_share": "Share of annual edges joining different macro-regions.",
    "cross_region_fractional_weight_share": "Share of weight on cross-region edges.",
    "new_edge_count": "Edges present this year but absent in the preceding year.",
    "continuing_edge_count": "Edges present in both this and the preceding year.",
    "disappearing_edge_count": "Prior-year edges absent in the current year.",
    "random_seed": "Deterministic random seed for the graph computation.",
    "total_node_count": "All annual nodes before coordinate filtering.",
    "coordinate_node_count": "Annual nodes with source-provided coordinates.",
    "total_edge_count": "All annual edges before coordinate filtering.",
    "coordinate_edge_count": "Annual edges whose two endpoints have sourced coordinates.",
    "selected_edge_count": "Coordinate-complete edges retained by the default display limit.",
    "missing_coordinate_node_count": "Annual nodes without sourced coordinates.",
    "missing_coordinate_edge_count": "Annual edges lacking one or both endpoint coordinates.",
    "node_coordinate_coverage_share": "Coordinate-complete nodes divided by all annual nodes.",
    "isolated_node_count": "Nodes with annual degree zero.",
    "cross_region_edge_count": "Edges joining institutions in different macro-regions.",
    "visible_minimum_fractional_weight": "Lowest fractional weight among displayed edges.",
    "summary_text": "Generated plain-language accessibility summary for the annual view.",
    "top_institution": "Display name of the highest fractional-strength node in the view.",
    "top_fractional_strength": "Fractional strength of the reported top institution.",
    "annual_community_id": "Annual Leiden label; not assumed stable between calendar years.",
    "continuity_id": "Deterministic longitudinal ID inherited through selected annual matches.",
    "previous_community_id": "Matched prior-year annual community label, when selected.",
    "current_community_id": "Current-year annual community label, when present.",
    "overlap_intersection_count": "Institutions shared by selected prior/current communities.",
    "overlap_union_count": "Distinct institutions in the selected prior/current community union.",
    "jaccard_overlap": "Intersection divided by union for an adjacent-year community pair.",
    "match_status": "First-year, continued, uncertain-match, or birth continuity state.",
    "low_overlap_uncertain": "True when a selected match is below the confidence threshold.",
    "assignment_algorithm": "Documented deterministic one-to-one community assignment rule.",
    "transition_year": "Current year in an adjacent-year community comparison.",
    "previous_year": "Previous year in an adjacent-year community comparison.",
    "previous_community_key": "Non-null primary-key surrogate for prior community or birth.",
    "current_community_key": (
        "Non-null primary-key surrogate for current community or disappearance."
    ),
    "previous_continuity_id": "Continuity ID attached to the prior annual community, when present.",
    "current_continuity_id": (
        "Continuity ID attached to the current annual community, when present."
    ),
    "intersection_count": "Institutions shared by the adjacent-year community pair.",
    "union_count": "Distinct institutions in the adjacent-year community pair union.",
    "assignment_selected": "Whether the pair was selected by one-to-one continuity assignment.",
    "previous_overlap_degree": "Meaningful current-year overlaps from the prior community.",
    "current_overlap_degree": "Meaningful prior-year overlaps into the current community.",
    "event_type": "Continuation, split, merge, birth, disappearance, complex, or minor overlap.",
    "confident_match_threshold": "Jaccard threshold below which selected matches are uncertain.",
    "event_overlap_threshold": "Jaccard threshold used to classify split and merge links.",
}


def build_public_data_dictionary(
    *,
    data_directory: str | Path,
    metadata_path: str | Path,
    output_json: str | Path,
    output_markdown: str | Path,
) -> dict[str, Any]:
    """Inspect every released table and atomically write complete dictionaries."""
    root = Path(data_directory)
    metadata = _load_json(Path(metadata_path))
    table_index = metadata.get("tables")
    if not isinstance(table_index, dict) or set(table_index) != set(TABLES):
        raise ValueError("public table index does not match the dictionary contract")
    tables: list[dict[str, Any]] = []
    total_columns = 0
    for table_name in sorted(TABLES):
        contract = TABLES[table_name]
        public_info = table_index[table_name]
        if not isinstance(public_info, dict):
            raise ValueError(f"invalid public metadata entry: {table_name}")
        path = root / f"{table_name}.parquet"
        checksum = file_sha256(path)
        if checksum != public_info.get("sha256"):
            raise ValueError(f"public table checksum changed: {path}")
        table = pq.read_table(path)
        if table.num_rows != public_info.get("row_count"):
            raise ValueError(f"public table row count changed: {path}")
        primary_key = list(contract["primary_key"])
        _validate_primary_key(table, primary_key, table_name)
        columns = []
        for field in table.schema:
            null_count = table[field.name].null_count
            columns.append(
                {
                    "name": field.name,
                    "type": str(field.type),
                    "description": _describe_column(field.name),
                    "nullable_in_release": null_count > 0,
                    "null_count": null_count,
                    "null_semantics": _null_semantics(field.name, null_count),
                }
            )
        total_columns += len(columns)
        source_manifest_path = Path(str(contract["source_manifest"]))
        source_manifest = _load_json(source_manifest_path)
        tables.append(
            {
                "table": table_name,
                "path": str(path),
                "description": contract["description"],
                "row_count": table.num_rows,
                "column_count": len(columns),
                "primary_key": primary_key,
                "sha256": checksum,
                "source_manifest": str(source_manifest_path),
                "source_manifests": source_manifest.get("source_manifests", []),
                "configuration_hashes": source_manifest.get("config_hashes", {}),
                "source_versions": source_manifest.get("source_versions", {}),
                "code_commit": source_manifest.get("git_commit"),
                "transformation_command": source_manifest.get("command"),
                "known_data_quality_issue": contract["known_issue"],
                "columns": columns,
            }
        )
    payload = {
        "schema_version": 1,
        "dictionary_version": _STAGE_VERSION,
        "data_version": metadata.get("data_version"),
        "methods_version": metadata.get("methods_version"),
        "table_count": len(tables),
        "column_entry_count": total_columns,
        "privacy_policy": "No API key, raw response, or private local path is included.",
        "tables": tables,
    }
    _validate_dictionary(payload)
    atomic_write_json(output_json, payload)
    markdown = _render_markdown(payload)
    atomic_write_text(output_markdown, markdown)
    _validate_privacy(Path(output_json).read_text(encoding="utf-8") + markdown)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "metadata_sha256": file_sha256(metadata_path),
                "table_hashes": {row["table"]: row["sha256"] for row in tables},
            }
        ),
        "released_table_count": len(tables),
        "documented_table_count": len(tables),
        "column_entry_count": total_columns,
        "tables_with_primary_keys": sum(bool(row["primary_key"]) for row in tables),
        "tables_with_source_manifests": sum(bool(row["source_manifest"]) for row in tables),
        "tables_with_known_issue_notes": sum(
            bool(row["known_data_quality_issue"]) for row in tables
        ),
        "private_path_or_key_count": 0,
        "dictionary_sha256": file_sha256(output_json),
        "report_sha256": file_sha256(output_markdown),
        "outputs": {
            "machine_readable_dictionary": str(output_json),
            "provenance_report": str(output_markdown),
        },
        "generated_at_utc": utc_timestamp(),
    }


def write_data_dictionary_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    dictionary_path: str | Path,
    report_path: str | Path,
    run_id: str,
    project_path: str | Path,
    command: str,
) -> None:
    config_hashes = {"project": config_file_hash(project_path)}
    source_manifests = sorted({str(value["source_manifest"]) for value in TABLES.values()})
    for dataset_name, artifact_path in (
        ("public_data_dictionary", dictionary_path),
        ("data_provenance_report", report_path),
    ):
        DatasetManifest(
            dataset_name=dataset_name,
            created_at_utc=utc_timestamp(),
            run_id=run_id,
            git_commit=current_git_commit(),
            config_hashes=config_hashes,
            source_manifests=source_manifests,
            source_versions={"data_dictionary": _STAGE_VERSION},
            row_count=1,
            column_count=1,
            primary_key=["checksum_sha256"],
            null_counts={"checksum_sha256": 0},
            checksum_sha256=file_sha256(artifact_path),
            command=command,
        ).write(f".agent/manifests/{dataset_name}.json")
    write_json_artifact(
        path=summary_path,
        dataset_name="data_dictionary_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions={"data_dictionary": _STAGE_VERSION},
        source_manifests=source_manifests,
        command=command,
    )


def _describe_column(name: str) -> str:
    exact = EXACT_DESCRIPTIONS.get(name)
    if exact is not None:
        return exact
    for endpoint in ("source", "target"):
        prefix = f"{endpoint}_"
        if name.startswith(prefix):
            base = name.removeprefix(prefix)
            base_description = EXACT_DESCRIPTIONS.get(base)
            if base_description is not None:
                lowered = f"{base_description[0].lower()}{base_description[1:]}"
                return f"{endpoint.title()} endpoint value: {lowered}"
    raise ValueError(f"released column lacks a curated definition: {name}")


def _null_semantics(name: str, null_count: int) -> str:
    if null_count == 0:
        return "Not null in this public release."
    if any(value in name for value in ("latitude", "longitude")):
        return "Null means no source-provided coordinate is available; no value is imputed."
    if name == "ror_id":
        return "Null means no source-linked ROR identifier is available."
    if "persistence" in name:
        return "Null means the edge/window value is undefined or the edge is absent."
    if any(value in name for value in ("assortativity", "modularity", "betweenness")):
        return "Null means the graph statistic is undefined for that annual graph."
    return "Null means the source or derived value is unavailable; it is not coerced to zero."


def _validate_primary_key(table: Any, primary_key: list[str], table_name: str) -> None:
    missing = set(primary_key).difference(table.column_names)
    if missing:
        raise ValueError(f"{table_name} lacks primary-key columns: {sorted(missing)}")
    keys = zip(*(table[column].to_pylist() for column in primary_key), strict=True)
    unique = set(keys)
    if len(unique) != table.num_rows:
        raise ValueError(f"{table_name} primary key is not unique")


def _validate_dictionary(payload: dict[str, Any]) -> None:
    tables = payload["tables"]
    if len(tables) != len(TABLES):
        raise ValueError("not every released table is documented")
    for table in tables:
        if len(table["columns"]) != table["column_count"]:
            raise ValueError(f"not every column is documented: {table['table']}")
        if not table["source_manifest"] or not table["primary_key"]:
            raise ValueError(f"provenance or primary key missing: {table['table']}")


def _validate_privacy(text: str) -> None:
    lowered = text.casefold()
    forbidden = ("openalex_api_key=", "/home/", "github_pat_", "ghp_", ".env")
    found = [value for value in forbidden if value in lowered]
    if found:
        raise ValueError(f"data dictionary contains forbidden private values: {found}")


def _render_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Public Data Dictionary and Provenance",
        "",
        f"Data version: `{payload['data_version']}`",
        f"Methods version: `{payload['methods_version']}`",
        f"Released tables: {payload['table_count']}",
        f"Documented table-column entries: {payload['column_entry_count']}",
        "",
        "Nulls are never silently converted to zero unless a page explicitly states a zero-fill",
        "display rule. Source and transformation paths below are repository-relative.",
    ]
    for table in payload["tables"]:
        lines.extend(
            [
                "",
                f"## `{table['table']}`",
                "",
                str(table["description"]),
                "",
                f"- Path: `{table['path']}`",
                f"- Rows: {table['row_count']:,}",
                f"- Primary key: `{', '.join(table['primary_key'])}`",
                f"- SHA-256: `{table['sha256']}`",
                f"- Direct source manifest: `{table['source_manifest']}`",
                f"- Source manifests: `{', '.join(table['source_manifests']) or 'none'}`",
                "- Configuration hashes: "
                f"`{json.dumps(table['configuration_hashes'], sort_keys=True)}`",
                f"- Source versions: `{json.dumps(table['source_versions'], sort_keys=True)}`",
                f"- Code commit: `{table['code_commit']}`",
                f"- Transformation: `{table['transformation_command']}`",
                f"- Known issue: {table['known_data_quality_issue']}",
                "",
                "| Column | Arrow type | Description | Null semantics | Null count |",
                "|---|---|---|---|---:|",
            ]
        )
        for column in table["columns"]:
            lines.append(
                f"| `{column['name']}` | `{column['type']}` | {column['description']} | "
                f"{column['null_semantics']} | {column['null_count']:,} |"
            )
    lines.extend(
        [
            "",
            "## Privacy and release boundary",
            "",
            "The dictionary covers only the compact public aggregate/thresholded tables in",
            "`dashboard/data/`. Raw API pages, cache contents, credentials, and private local",
            "paths are outside the release boundary.",
            "",
        ]
    )
    return "\n".join(lines)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value
