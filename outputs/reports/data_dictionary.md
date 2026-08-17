# Public Data Dictionary and Provenance

Data version: `gisnet-0.1.0-2026-08-15`
Methods version: `public-dashboard-bundle-2026-08-15-v5`
Released tables: 16
Documented table-column entries: 319

Nulls are never silently converted to zero unless a page explicitly states a zero-fill
display rule. Source and transformation paths below are repository-relative.

## `community_continuity`

Annual community labels linked to stable longitudinal continuity IDs.

- Path: `dashboard/data/community_continuity.parquet`
- Rows: 11,930
- Primary key: `year, corpus_view, hierarchy_view, annual_community_id`
- SHA-256: `bd44d0c924a42cb79c2dcfea91759dc526d898bc5523f997a2e1285f2e40bf9f`
- Direct source manifest: `.agent/manifests/community_continuity_year.json`
- Source manifests: `.agent/manifests/communities_year.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"community_continuity": "jaccard-community-continuity-2026-08-05-v1"}`
- Code commit: `568bbdbf6b3f`
- Transformation: `python -m gisnet.cli match-communities --resume`
- Known issue: Matches below Jaccard 0.25 are retained but explicitly uncertain.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `year` | `int64` | Complete publication calendar year. | Not null in this public release. | 0 |
| `corpus_view` | `string` | GIS corpus definition: strict or broad. | Not null in this public release. | 0 |
| `hierarchy_view` | `string` | Institution identity view: organization or documented umbrella. | Not null in this public release. | 0 |
| `annual_community_id` | `string` | Annual Leiden label; not assumed stable between calendar years. | Not null in this public release. | 0 |
| `continuity_id` | `string` | Deterministic longitudinal ID inherited through selected annual matches. | Not null in this public release. | 0 |
| `community_size` | `int64` | Number of nodes assigned to the labelled annual community. | Not null in this public release. | 0 |
| `previous_community_id` | `string` | Matched prior-year annual community label, when selected. | Null means the source or derived value is unavailable; it is not coerced to zero. | 9,031 |
| `overlap_intersection_count` | `int64` | Institutions shared by selected prior/current communities. | Null means the source or derived value is unavailable; it is not coerced to zero. | 9,031 |
| `overlap_union_count` | `int64` | Distinct institutions in the selected prior/current community union. | Null means the source or derived value is unavailable; it is not coerced to zero. | 9,031 |
| `jaccard_overlap` | `double` | Intersection divided by union for an adjacent-year community pair. | Null means the source or derived value is unavailable; it is not coerced to zero. | 9,031 |
| `match_status` | `string` | First-year, continued, uncertain-match, or birth continuity state. | Not null in this public release. | 0 |
| `low_overlap_uncertain` | `bool` | True when a selected match is below the confidence threshold. | Not null in this public release. | 0 |
| `assignment_algorithm` | `string` | Documented deterministic one-to-one community assignment rule. | Not null in this public release. | 0 |

## `community_transitions`

Adjacent-year overlap assignments and split/merge/birth/death events.

- Path: `dashboard/data/community_transitions.parquet`
- Rows: 51,585
- Primary key: `transition_year, corpus_view, hierarchy_view, previous_community_key, current_community_key`
- SHA-256: `101ff09480e97791a5ab7b3f00d0a389b75e3f4109a0c568788ee27656ffe7ec`
- Direct source manifest: `.agent/manifests/community_transitions_year.json`
- Source manifests: `.agent/manifests/communities_year.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"community_continuity": "jaccard-community-continuity-2026-08-05-v1"}`
- Code commit: `568bbdbf6b3f`
- Transformation: `python -m gisnet.cli match-communities --resume`
- Known issue: Minor positive overlaps are retained separately from event-threshold links.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `transition_year` | `int64` | Current year in an adjacent-year community comparison. | Not null in this public release. | 0 |
| `previous_year` | `int64` | Previous year in an adjacent-year community comparison. | Not null in this public release. | 0 |
| `corpus_view` | `string` | GIS corpus definition: strict or broad. | Not null in this public release. | 0 |
| `hierarchy_view` | `string` | Institution identity view: organization or documented umbrella. | Not null in this public release. | 0 |
| `previous_community_id` | `string` | Matched prior-year annual community label, when selected. | Null means the source or derived value is unavailable; it is not coerced to zero. | 8,302 |
| `current_community_id` | `string` | Current-year annual community label, when present. | Null means the source or derived value is unavailable; it is not coerced to zero. | 8,317 |
| `previous_community_key` | `string` | Non-null primary-key surrogate for prior community or birth. | Not null in this public release. | 0 |
| `current_community_key` | `string` | Non-null primary-key surrogate for current community or disappearance. | Not null in this public release. | 0 |
| `previous_continuity_id` | `string` | Continuity ID attached to the prior annual community, when present. | Null means the source or derived value is unavailable; it is not coerced to zero. | 8,302 |
| `current_continuity_id` | `string` | Continuity ID attached to the current annual community, when present. | Null means the source or derived value is unavailable; it is not coerced to zero. | 8,317 |
| `intersection_count` | `int64` | Institutions shared by the adjacent-year community pair. | Not null in this public release. | 0 |
| `union_count` | `int64` | Distinct institutions in the adjacent-year community pair union. | Null means the source or derived value is unavailable; it is not coerced to zero. | 16,619 |
| `jaccard_overlap` | `double` | Intersection divided by union for an adjacent-year community pair. | Null means the source or derived value is unavailable; it is not coerced to zero. | 16,619 |
| `assignment_selected` | `bool` | Whether the pair was selected by one-to-one continuity assignment. | Not null in this public release. | 0 |
| `low_overlap_uncertain` | `bool` | True when a selected match is below the confidence threshold. | Not null in this public release. | 0 |
| `previous_overlap_degree` | `int64` | Meaningful current-year overlaps from the prior community. | Not null in this public release. | 0 |
| `current_overlap_degree` | `int64` | Meaningful prior-year overlaps into the current community. | Not null in this public release. | 0 |
| `event_type` | `string` | Continuation, split, merge, birth, disappearance, complex, or minor overlap. | Not null in this public release. | 0 |
| `assignment_algorithm` | `string` | Documented deterministic one-to-one community assignment rule. | Not null in this public release. | 0 |
| `confident_match_threshold` | `double` | Jaccard threshold below which selected matches are uncertain. | Not null in this public release. | 0 |
| `event_overlap_threshold` | `double` | Jaccard threshold used to classify split and merge links. | Not null in this public release. | 0 |

## `filter_dimensions`

Complete annual country, subregion, and institution-type dashboard choices.

- Path: `dashboard/data/filter_dimensions.parquet`
- Rows: 8,670
- Primary key: `year, corpus_view, hierarchy_view, dimension, value`
- SHA-256: `42783af95e23a275f62d98db599a7b8669a97f8a029b6024d426694a6abc82f1`
- Direct source manifest: `.agent/manifests/dashboard_bundle_summary.json`
- Source manifests: `.agent/manifests/trend_series_year.json, .agent/manifests/collaboration_matrix_year.json, .agent/manifests/map_nodes_year.json, .agent/manifests/map_edges_year.json, .agent/manifests/map_coverage_year.json, .agent/manifests/network_view_nodes_year.json, .agent/manifests/network_view_edges_year.json, .agent/manifests/network_accessibility_year.json, .agent/manifests/graph_metrics_year.json, .agent/manifests/sensitivity_matrix.json, .agent/manifests/community_continuity_year.json, .agent/manifests/community_transitions_year.json, .agent/manifests/institution_hierarchy.json, .agent/manifests/institutions.json, .agent/manifests/nodes_year.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"dashboard_bundle_policy": "public-dashboard-bundle-2026-08-15-v5"}`
- Code commit: `568bbdbf6b3f`
- Transformation: `python -m gisnet.cli build-dashboard-data --resume`
- Known issue: Topic-family choices come from the separate thresholded Topic table.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `year` | `int64` | Complete publication calendar year. | Not null in this public release. | 0 |
| `corpus_view` | `string` | GIS corpus definition: strict or broad. | Not null in this public release. | 0 |
| `hierarchy_view` | `string` | Institution identity view: organization or documented umbrella. | Not null in this public release. | 0 |
| `dimension` | `string` | Dashboard filter dimension: country, subregion, or institution type. | Not null in this public release. | 0 |
| `value` | `string` | Observed display value belonging to the named dashboard filter dimension. | Not null in this public release. | 0 |

## `geography_dimensions`

Country-code labels used to join complete country flows to dashboard geography.

- Path: `dashboard/data/geography_dimensions.parquet`
- Rows: 146
- Primary key: `country_code`
- SHA-256: `05c43d004c46c479f96d3fc6fa9b9b25b00ae253271a90fc160261c1a1359b08`
- Direct source manifest: `.agent/manifests/dashboard_bundle_summary.json`
- Source manifests: `.agent/manifests/trend_series_year.json, .agent/manifests/collaboration_matrix_year.json, .agent/manifests/map_nodes_year.json, .agent/manifests/map_edges_year.json, .agent/manifests/map_coverage_year.json, .agent/manifests/network_view_nodes_year.json, .agent/manifests/network_view_edges_year.json, .agent/manifests/network_accessibility_year.json, .agent/manifests/graph_metrics_year.json, .agent/manifests/sensitivity_matrix.json, .agent/manifests/community_continuity_year.json, .agent/manifests/community_transitions_year.json, .agent/manifests/institution_hierarchy.json, .agent/manifests/institutions.json, .agent/manifests/nodes_year.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"dashboard_bundle_policy": "public-dashboard-bundle-2026-08-15-v5"}`
- Code commit: `568bbdbf6b3f`
- Transformation: `python -m gisnet.cli build-dashboard-data --resume`
- Known issue: Only countries observed in complete annual network nodes are included.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `country_code` | `string` | Source country code associated with the institution. | Not null in this public release. | 0 |
| `country_name` | `string` | Frozen country or territory display name. | Not null in this public release. | 0 |
| `macro_region` | `string` | Frozen UN M49-style macro-region analytical grouping. | Not null in this public release. | 0 |
| `subregion` | `string` | Frozen UN M49-style subregion analytical grouping. | Not null in this public release. | 0 |

## `graph_metrics`

Annual graph-level topology, connectivity, mixing, and turnover metrics.

- Path: `dashboard/data/graph_metrics.parquet`
- Rows: 64
- Primary key: `year, corpus_view, hierarchy_view`
- SHA-256: `a049b76de01f0960aedd986951dafbd0318751cd95133ddd515a14b2f56e9a84`
- Direct source manifest: `.agent/manifests/graph_metrics_year.json`
- Source manifests: `.agent/manifests/edges_year.json, .agent/manifests/institution_outputs_year.json, .agent/manifests/graph_summary_year.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"network_metrics_policy": "network-metrics-2026-08-05-v1"}`
- Code commit: `568bbdbf6b3f`
- Transformation: `python -m gisnet.cli compute-metrics --edges data/processed/edges_year.parquet --institution-outputs data/processed/institution_outputs_year.parquet --resume`
- Known issue: Betweenness uses the disclosed cutoff approximation for large graphs.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `year` | `int64` | Complete publication calendar year. | Not null in this public release. | 0 |
| `corpus_view` | `string` | GIS corpus definition: strict or broad. | Not null in this public release. | 0 |
| `hierarchy_view` | `string` | Institution identity view: organization or documented umbrella. | Not null in this public release. | 0 |
| `node_count` | `int64` | Number of nodes in the annual graph or public view. | Not null in this public release. | 0 |
| `edge_count` | `int64` | Number of undirected edges in the annual graph or public view. | Not null in this public release. | 0 |
| `density` | `double` | Observed edges divided by possible undirected edges. | Not null in this public release. | 0 |
| `mean_degree` | `double` | Arithmetic mean annual node degree. | Not null in this public release. | 0 |
| `mean_full_strength` | `double` | Arithmetic mean annual full-count node strength. | Not null in this public release. | 0 |
| `mean_fractional_strength` | `double` | Arithmetic mean annual fractional node strength. | Not null in this public release. | 0 |
| `connected_component_count` | `int64` | Number of connected components in the annual graph. | Not null in this public release. | 0 |
| `largest_connected_component_share` | `double` | Share of nodes in the largest connected component. | Not null in this public release. | 0 |
| `modularity` | `double` | Primary-resolution Leiden partition modularity. | Not null in this public release. | 0 |
| `modularity_resolution` | `double` | Leiden resolution associated with stored modularity. | Not null in this public release. | 0 |
| `macro_region_assortativity` | `double` | Categorical assortativity by macro-region. | Not null in this public release. | 0 |
| `country_assortativity` | `double` | Categorical assortativity by country. | Not null in this public release. | 0 |
| `cross_region_edge_share` | `double` | Share of annual edges joining different macro-regions. | Not null in this public release. | 0 |
| `cross_region_fractional_weight_share` | `double` | Share of weight on cross-region edges. | Not null in this public release. | 0 |
| `new_edge_count` | `int64` | Edges present this year but absent in the preceding year. | Not null in this public release. | 0 |
| `continuing_edge_count` | `int64` | Edges present in both this and the preceding year. | Not null in this public release. | 0 |
| `disappearing_edge_count` | `int64` | Prior-year edges absent in the current year. | Not null in this public release. | 0 |
| `betweenness_method` | `string` | Exact or cutoff weighted shortest-path method used for betweenness. | Not null in this public release. | 0 |
| `betweenness_sample_size` | `int64` | Number of graph vertices included by the betweenness method. | Not null in this public release. | 0 |
| `betweenness_cutoff` | `int64` | Maximum path length for approximated betweenness; null when exact. | Not null in this public release. | 0 |
| `random_seed` | `int64` | Deterministic random seed for the graph computation. | Not null in this public release. | 0 |

## `institution_identities`

Stable organization identifiers mapped to their documented umbrella identity.

- Path: `dashboard/data/institution_identities.parquet`
- Rows: 46,812
- Primary key: `organization_id`
- SHA-256: `cb26fa08d9bc6632c9ff8bf7ae486bf57a54683bbb5468c8e848285977e27f08`
- Direct source manifest: `.agent/manifests/institution_hierarchy.json`
- Source manifests: `.agent/manifests/institutions_ror.json, .agent/manifests/institutions_geographic.json`
- Configuration hashes: `{"institution_overrides": "a2bfe8f3c1bee1e5a8095f930784b1f04d60c5e740c3c498d87725c306370e76", "project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"hierarchy_policy": "institution-hierarchy-2026-08-05-v1"}`
- Code commit: `568bbdbf6b3f`
- Transformation: `python -m gisnet.cli build-hierarchy --institutions data/processed/institutions_ror.parquet --output data/processed/institution_hierarchy.parquet --resume`
- Known issue: Umbrella collapse occurs only under explicit frozen hierarchy rules.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `organization_id` | `string` | Stable source identifier for the uncollapsed organization identity. | Not null in this public release. | 0 |
| `organization_name` | `string` | Display name associated with the organization identity. | Not null in this public release. | 0 |
| `umbrella_id` | `string` | Stable identifier used for the documented umbrella hierarchy view. | Not null in this public release. | 0 |
| `umbrella_name` | `string` | Display name associated with the umbrella identity. | Not null in this public release. | 0 |
| `is_collapsed` | `bool` | Whether the organization is explicitly collapsed into another umbrella ID. | Not null in this public release. | 0 |

## `map_coverage`

Annual sourced-coordinate coverage and default map display limits.

- Path: `dashboard/data/map_coverage.parquet`
- Rows: 64
- Primary key: `year, corpus_view, hierarchy_view`
- SHA-256: `917e1e7a8a565fd31f9c14e9ee93b6d9352f8ba53155d733e53023354fd0e6f4`
- Direct source manifest: `.agent/manifests/map_coverage_year.json`
- Source manifests: `.agent/manifests/nodes_year.json, .agent/manifests/edges_metrics_year.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"map_data_policy": "geographic-map-data-2026-08-05-v1"}`
- Code commit: `568bbdbf6b3f`
- Transformation: `python -m gisnet.cli build-map-data --resume`
- Known issue: Coverage varies by year and view; missing coordinates are never imputed.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `year` | `int64` | Complete publication calendar year. | Not null in this public release. | 0 |
| `corpus_view` | `string` | GIS corpus definition: strict or broad. | Not null in this public release. | 0 |
| `hierarchy_view` | `string` | Institution identity view: organization or documented umbrella. | Not null in this public release. | 0 |
| `total_node_count` | `int64` | All annual nodes before coordinate filtering. | Not null in this public release. | 0 |
| `coordinate_node_count` | `int64` | Annual nodes with source-provided coordinates. | Not null in this public release. | 0 |
| `total_edge_count` | `int64` | All annual edges before coordinate filtering. | Not null in this public release. | 0 |
| `coordinate_edge_count` | `int64` | Annual edges whose two endpoints have sourced coordinates. | Not null in this public release. | 0 |
| `selected_edge_count` | `int64` | Coordinate-complete edges retained by the default display limit. | Not null in this public release. | 0 |
| `missing_coordinate_node_count` | `int64` | Annual nodes without sourced coordinates. | Not null in this public release. | 0 |
| `missing_coordinate_edge_count` | `int64` | Annual edges lacking one or both endpoint coordinates. | Not null in this public release. | 0 |
| `node_coordinate_coverage_share` | `double` | Coordinate-complete nodes divided by all annual nodes. | Not null in this public release. | 0 |
| `default_edge_limit` | `int32` | Documented maximum edges displayed by default per view. | Not null in this public release. | 0 |
| `default_node_limit` | `int32` | Documented maximum nodes displayed by default per view. | Not null in this public release. | 0 |

## `map_edges`

Top display-ranked collaboration edges whose endpoints have sourced coordinates.

- Path: `dashboard/data/map_edges.parquet`
- Rows: 32,000
- Primary key: `year, corpus_view, hierarchy_view, source_id, target_id`
- SHA-256: `d7c0ddf20858284b40b935962b7c1030930fc93f47b5691815b880e836df6c2e`
- Direct source manifest: `.agent/manifests/map_edges_year.json`
- Source manifests: `.agent/manifests/nodes_year.json, .agent/manifests/edges_metrics_year.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"map_data_policy": "geographic-map-data-2026-08-05-v1"}`
- Code commit: `568bbdbf6b3f`
- Transformation: `python -m gisnet.cli build-map-data --resume`
- Known issue: This thresholded map extract is not the complete annual edge table.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `year` | `int32` | Complete publication calendar year. | Not null in this public release. | 0 |
| `corpus_view` | `string` | GIS corpus definition: strict or broad. | Not null in this public release. | 0 |
| `hierarchy_view` | `string` | Institution identity view: organization or documented umbrella. | Not null in this public release. | 0 |
| `source_id` | `string` | Source endpoint value: stable institution identifier for an undirected edge endpoint. | Not null in this public release. | 0 |
| `target_id` | `string` | Target endpoint value: stable institution identifier for an undirected edge endpoint. | Not null in this public release. | 0 |
| `source_name` | `string` | Source endpoint value: institution display name for an undirected edge endpoint. | Not null in this public release. | 0 |
| `target_name` | `string` | Target endpoint value: institution display name for an undirected edge endpoint. | Not null in this public release. | 0 |
| `source_region` | `string` | Source endpoint value: frozen macro-region label for an undirected edge endpoint. | Not null in this public release. | 0 |
| `target_region` | `string` | Target endpoint value: frozen macro-region label for an undirected edge endpoint. | Not null in this public release. | 0 |
| `source_subregion` | `string` | Source endpoint value: frozen UN M49-style subregion analytical grouping. | Not null in this public release. | 0 |
| `target_subregion` | `string` | Target endpoint value: frozen UN M49-style subregion analytical grouping. | Not null in this public release. | 0 |
| `source_country` | `string` | Source endpoint value: frozen country name for an undirected edge endpoint. | Not null in this public release. | 0 |
| `target_country` | `string` | Target endpoint value: frozen country name for an undirected edge endpoint. | Not null in this public release. | 0 |
| `source_category` | `string` | Source endpoint value: configured analytical institution category for an edge endpoint. | Not null in this public release. | 0 |
| `target_category` | `string` | Target endpoint value: configured analytical institution category for an edge endpoint. | Not null in this public release. | 0 |
| `full_count` | `int64` | Full-count collaboration weight: one per institution pair per Work. | Not null in this public release. | 0 |
| `fractional_count` | `double` | Fractional weight: one divided by the number of pairs on each Work. | Not null in this public release. | 0 |
| `distinct_work_count` | `int64` | Number of distinct source Work identifiers contributing to the row. | Not null in this public release. | 0 |
| `large_consortium_work_count` | `int64` | Contributing Works at or above the consortium warning size. | Not null in this public release. | 0 |
| `excluded_threshold_work_count` | `int64` | Contributing Works excluded by the configured size policy. | Not null in this public release. | 0 |
| `maximum_consortium_size` | `int32` | Largest distinct-institution count among contributing Works. | Not null in this public release. | 0 |
| `topic_families` | `list<element: string>` | Sorted distinct configured Topic families observed on contributing Works. | Not null in this public release. | 0 |
| `work_ids_sample` | `list<element: string>` | Deterministic bounded sample of contributing source Work identifiers. | Not null in this public release. | 0 |
| `distinct_topic_family_count` | `int32` | Number of distinct Topic families contributing to the row. | Not null in this public release. | 0 |
| `active_years_3y` | `int8` | Active years in the fixed-denominator trailing three-year window. | Not null in this public release. | 0 |
| `active_years_5y` | `int8` | Active years in the fixed-denominator trailing five-year window. | Not null in this public release. | 0 |
| `source_work_count` | `int64` | Source endpoint value: distinct primary-corpus Works affiliated with the institution. | Not null in this public release. | 0 |
| `target_work_count` | `int64` | Target endpoint value: distinct primary-corpus Works affiliated with the institution. | Not null in this public release. | 0 |
| `normalized_intensity` | `double` | Fractional weight divided by geometric-mean institutional output. | Not null in this public release. | 0 |
| `persistence_3y` | `double` | Active-year share in the trailing three-year window. | Not null in this public release. | 0 |
| `persistence_5y` | `double` | Active-year share in the trailing five-year window. | Not null in this public release. | 0 |
| `persistence_3y_incomplete_window` | `bool` | True before a complete three-year history is available. | Not null in this public release. | 0 |
| `persistence_5y_incomplete_window` | `bool` | True before a complete five-year history is available. | Not null in this public release. | 0 |
| `visualization_score` | `double` | Non-primary composite used only to rank edges for display. | Not null in this public release. | 0 |
| `visualization_score_is_primary` | `bool` | Always false; guards against scientific interpretation. | Not null in this public release. | 0 |
| `visualization_score_method` | `string` | Stored description of the display-ranking calculation. | Not null in this public release. | 0 |
| `source_latitude` | `double` | Source endpoint value: source-provided institution latitude; never imputed. | Not null in this public release. | 0 |
| `source_longitude` | `double` | Source endpoint value: source-provided institution longitude; never imputed. | Not null in this public release. | 0 |
| `target_latitude` | `double` | Target endpoint value: source-provided institution latitude; never imputed. | Not null in this public release. | 0 |
| `target_longitude` | `double` | Target endpoint value: source-provided institution longitude; never imputed. | Not null in this public release. | 0 |
| `source_institution_type` | `string` | Source endpoint value: configured analytical institution type for an edge endpoint. | Not null in this public release. | 0 |
| `target_institution_type` | `string` | Target endpoint value: configured analytical institution type for an edge endpoint. | Not null in this public release. | 0 |
| `macro_region_pair` | `string` | Stable unordered source/target macro-region pair label. | Not null in this public release. | 0 |
| `default_edge_rank` | `int32` | One-based rank under the default edge display policy. | Not null in this public release. | 0 |
| `default_edge_limit` | `int32` | Documented maximum edges displayed by default per view. | Not null in this public release. | 0 |
| `default_threshold_method` | `string` | Stored description of the default display threshold. | Not null in this public release. | 0 |

## `map_nodes`

Annual institution metrics for nodes with source-provided coordinates.

- Path: `dashboard/data/map_nodes.parquet`
- Rows: 75,304
- Primary key: `year, corpus_view, hierarchy_view, institution_id`
- SHA-256: `353675a124140a3b77d4353adb213417cf453c9d27fd0e4144524188b308e8d5`
- Direct source manifest: `.agent/manifests/map_nodes_year.json`
- Source manifests: `.agent/manifests/nodes_year.json, .agent/manifests/edges_metrics_year.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"map_data_policy": "geographic-map-data-2026-08-05-v1"}`
- Code commit: `568bbdbf6b3f`
- Transformation: `python -m gisnet.cli build-map-data --resume`
- Known issue: Absence means unavailable sourced coordinates, not an absent institution.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `year` | `int64` | Complete publication calendar year. | Not null in this public release. | 0 |
| `corpus_view` | `string` | GIS corpus definition: strict or broad. | Not null in this public release. | 0 |
| `hierarchy_view` | `string` | Institution identity view: organization or documented umbrella. | Not null in this public release. | 0 |
| `institution_id` | `string` | Stable source institution identifier used as the node key. | Not null in this public release. | 0 |
| `display_name` | `string` | Source-provided or canonically selected institution display name. | Not null in this public release. | 0 |
| `ror_id` | `string` | Source-linked Research Organization Registry identifier, when available. | Not null in this public release. | 0 |
| `country_code` | `string` | Source country code associated with the institution. | Not null in this public release. | 0 |
| `country_name` | `string` | Frozen country or territory display name. | Not null in this public release. | 0 |
| `macro_region` | `string` | Frozen UN M49-style macro-region analytical grouping. | Not null in this public release. | 0 |
| `subregion` | `string` | Frozen UN M49-style subregion analytical grouping. | Not null in this public release. | 0 |
| `institution_category` | `string` | Configured analytical category for the source institution type. | Not null in this public release. | 0 |
| `analytical_scope` | `string` | Whether the row is in focal or retained contextual geographic scope. | Not null in this public release. | 0 |
| `latitude` | `double` | Source-provided institution latitude; never imputed. | Not null in this public release. | 0 |
| `longitude` | `double` | Source-provided institution longitude; never imputed. | Not null in this public release. | 0 |
| `work_count` | `int64` | Distinct primary-corpus Works affiliated with the institution. | Not null in this public release. | 0 |
| `fractional_work_count` | `double` | Institutional Work output under the stored fractional allocation. | Not null in this public release. | 0 |
| `collaborative_work_count` | `int64` | Distinct Works containing more than one institution. | Not null in this public release. | 0 |
| `single_institution_work_count` | `int64` | Distinct Works containing only this institution. | Not null in this public release. | 0 |
| `international_work_count` | `int64` | Distinct Works with institutions from multiple countries. | Not null in this public release. | 0 |
| `cross_region_work_count` | `int64` | Distinct Works with institutions from multiple macro-regions. | Not null in this public release. | 0 |
| `international_collaboration_share` | `double` | International Works divided by all included Works. | Not null in this public release. | 0 |
| `cross_region_collaboration_share` | `double` | Cross-region Works divided by all included Works. | Not null in this public release. | 0 |
| `degree` | `int64` | Number of distinct institutional partners in the annual graph. | Not null in this public release. | 0 |
| `full_strength` | `double` | Sum of incident full-count collaboration edge weights. | Not null in this public release. | 0 |
| `fractional_strength` | `double` | Sum of incident fractional collaboration edge weights. | Not null in this public release. | 0 |
| `betweenness` | `double` | Stored weighted betweenness centrality under the disclosed method. | Not null in this public release. | 0 |
| `betweenness_method` | `string` | Exact or cutoff weighted shortest-path method used for betweenness. | Not null in this public release. | 0 |
| `betweenness_sample_size` | `int64` | Number of graph vertices included by the betweenness method. | Not null in this public release. | 0 |
| `betweenness_cutoff` | `int64` | Maximum path length for approximated betweenness; null when exact. | Not null in this public release. | 0 |
| `betweenness_seed` | `int64` | Deterministic random seed recorded for the centrality computation. | Not null in this public release. | 0 |
| `pagerank` | `double` | Weighted PageRank centrality normalized within the annual graph. | Not null in this public release. | 0 |
| `bridge_score` | `double` | Documented cross-community/cross-region bridging indicator. | Not null in this public release. | 0 |
| `partner_country_count` | `int64` | Number of distinct partner countries. | Not null in this public release. | 0 |
| `partner_region_count` | `int64` | Number of distinct partner macro-regions. | Not null in this public release. | 0 |
| `default_node_rank` | `int32` | One-based rank under the default node display policy. | Not null in this public release. | 0 |
| `default_node_limit` | `int32` | Documented maximum nodes displayed by default per view. | Not null in this public release. | 0 |
| `coordinate_policy` | `string` | Statement that coordinates are source-provided and never imputed. | Not null in this public release. | 0 |

## `matrix`

Sparse macro-region, subregion, and country collaboration matrix cells.

- Path: `dashboard/data/matrix.parquet`
- Rows: 97,762
- Primary key: `year, corpus_view, hierarchy_view, geographic_level, source_geography, target_geography`
- SHA-256: `9d524bbb114964473694ec5c5c4d1342568d66b0b1a554ddc62e56ec36054e77`
- Direct source manifest: `.agent/manifests/collaboration_matrix_year.json`
- Source manifests: `.agent/manifests/region_flows_year.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"matrix_policy": "region-collaboration-matrix-2026-08-17-v2"}`
- Code commit: `306db695f048`
- Transformation: `python -m gisnet.cli build-matrix --resume`
- Known issue: An absent row is missing/no observed flow, never an imputed zero.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `year` | `int32` | Complete publication calendar year. | Not null in this public release. | 0 |
| `corpus_view` | `string` | GIS corpus definition: strict or broad. | Not null in this public release. | 0 |
| `hierarchy_view` | `string` | Institution identity view: organization or documented umbrella. | Not null in this public release. | 0 |
| `geographic_level` | `string` | Matrix level: macro_region, subregion, or country. | Not null in this public release. | 0 |
| `source_geography` | `string` | Stable first endpoint label of the undirected geographic cell. | Not null in this public release. | 0 |
| `target_geography` | `string` | Stable second endpoint label of the undirected geographic cell. | Not null in this public release. | 0 |
| `full_count` | `int64` | Full-count collaboration weight: one per institution pair per Work. | Not null in this public release. | 0 |
| `fractional_count` | `double` | Fractional weight: one divided by the number of pairs on each Work. | Not null in this public release. | 0 |
| `distinct_work_count` | `int64` | Number of distinct source Work identifiers contributing to the row. | Not null in this public release. | 0 |
| `distinct_institution_pair_count` | `int64` | Distinct source institution pairs contributing to the row. | Not null in this public release. | 0 |
| `work_ids_sample` | `list<element: string>` | Deterministic bounded sample of contributing source Work identifiers. | Not null in this public release. | 0 |
| `normalized_share` | `double` | Fractional cell weight divided by the applicable annual total. | Not null in this public release. | 0 |
| `source_order` | `int32` | Stable display order for the source geography. | Not null in this public release. | 0 |
| `target_order` | `int32` | Stable display order for the target geography. | Not null in this public release. | 0 |
| `cell_status` | `string` | Explicit observed/missing semantic label for the matrix cell. | Not null in this public release. | 0 |
| `absent_cell_semantics` | `string` | Statement defining an absent sparse matrix row. | Not null in this public release. | 0 |

## `network_accessibility`

Plain-language annual summaries and visible network thresholds.

- Path: `dashboard/data/network_accessibility.parquet`
- Rows: 64
- Primary key: `year, corpus_view, hierarchy_view`
- SHA-256: `233fed187e55bcf3a247b5ade84b6b11a295d3bdc4ae16a9cad934d4fed1bb8b`
- Direct source manifest: `.agent/manifests/network_accessibility_year.json`
- Source manifests: `.agent/manifests/nodes_year.json, .agent/manifests/edges_metrics_year.json, .agent/manifests/communities_year.json, .agent/manifests/network_layout.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"network_view_policy": "fixed-layout-network-view-2026-08-06-v2"}`
- Code commit: `568bbdbf6b3f`
- Transformation: `python -m gisnet.cli build-network-view --resume`
- Known issue: Counts describe the fixed-layout public view, not every raw affiliation.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `year` | `int64` | Complete publication calendar year. | Not null in this public release. | 0 |
| `corpus_view` | `string` | GIS corpus definition: strict or broad. | Not null in this public release. | 0 |
| `hierarchy_view` | `string` | Institution identity view: organization or documented umbrella. | Not null in this public release. | 0 |
| `node_count` | `int64` | Number of nodes in the annual graph or public view. | Not null in this public release. | 0 |
| `top_institution` | `string` | Display name of the highest fractional-strength node in the view. | Not null in this public release. | 0 |
| `top_fractional_strength` | `double` | Fractional strength of the reported top institution. | Not null in this public release. | 0 |
| `isolated_node_count` | `int64` | Nodes with annual degree zero. | Not null in this public release. | 0 |
| `edge_count` | `int64` | Number of undirected edges in the annual graph or public view. | Not null in this public release. | 0 |
| `cross_region_edge_count` | `int64` | Edges joining institutions in different macro-regions. | Not null in this public release. | 0 |
| `visible_minimum_fractional_weight` | `double` | Lowest fractional weight among displayed edges. | Not null in this public release. | 0 |
| `summary_text` | `string` | Generated plain-language accessibility summary for the annual view. | Not null in this public release. | 0 |
| `coordinate_policy` | `string` | Statement that coordinates are source-provided and never imputed. | Not null in this public release. | 0 |
| `default_edge_limit` | `int64` | Documented maximum edges displayed by default per view. | Not null in this public release. | 0 |

## `network_edges`

Top fixed-layout core edges with weights, persistence, details, and coordinates.

- Path: `dashboard/data/network_edges.parquet`
- Rows: 64,000
- Primary key: `year, corpus_view, hierarchy_view, source_id, target_id`
- SHA-256: `920878da893802281d54dfd696b1452a764e395db33d49a94ee2085aac2290e4`
- Direct source manifest: `.agent/manifests/network_view_edges_year.json`
- Source manifests: `.agent/manifests/nodes_year.json, .agent/manifests/edges_metrics_year.json, .agent/manifests/communities_year.json, .agent/manifests/network_layout.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"network_view_policy": "fixed-layout-network-view-2026-08-06-v2"}`
- Code commit: `568bbdbf6b3f`
- Transformation: `python -m gisnet.cli build-network-view --resume`
- Known issue: Limited to the top 1,000 edges per view by a non-primary display score.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `year` | `int32` | Complete publication calendar year. | Not null in this public release. | 0 |
| `corpus_view` | `string` | GIS corpus definition: strict or broad. | Not null in this public release. | 0 |
| `hierarchy_view` | `string` | Institution identity view: organization or documented umbrella. | Not null in this public release. | 0 |
| `source_id` | `string` | Source endpoint value: stable institution identifier for an undirected edge endpoint. | Not null in this public release. | 0 |
| `target_id` | `string` | Target endpoint value: stable institution identifier for an undirected edge endpoint. | Not null in this public release. | 0 |
| `source_name` | `string` | Source endpoint value: institution display name for an undirected edge endpoint. | Not null in this public release. | 0 |
| `target_name` | `string` | Target endpoint value: institution display name for an undirected edge endpoint. | Not null in this public release. | 0 |
| `source_region` | `string` | Source endpoint value: frozen macro-region label for an undirected edge endpoint. | Not null in this public release. | 0 |
| `target_region` | `string` | Target endpoint value: frozen macro-region label for an undirected edge endpoint. | Not null in this public release. | 0 |
| `source_subregion` | `string` | Source endpoint value: frozen UN M49-style subregion analytical grouping. | Not null in this public release. | 0 |
| `target_subregion` | `string` | Target endpoint value: frozen UN M49-style subregion analytical grouping. | Not null in this public release. | 0 |
| `source_country` | `string` | Source endpoint value: frozen country name for an undirected edge endpoint. | Not null in this public release. | 0 |
| `target_country` | `string` | Target endpoint value: frozen country name for an undirected edge endpoint. | Not null in this public release. | 0 |
| `source_category` | `string` | Source endpoint value: configured analytical institution category for an edge endpoint. | Not null in this public release. | 0 |
| `target_category` | `string` | Target endpoint value: configured analytical institution category for an edge endpoint. | Not null in this public release. | 0 |
| `full_count` | `int64` | Full-count collaboration weight: one per institution pair per Work. | Not null in this public release. | 0 |
| `fractional_count` | `double` | Fractional weight: one divided by the number of pairs on each Work. | Not null in this public release. | 0 |
| `distinct_work_count` | `int64` | Number of distinct source Work identifiers contributing to the row. | Not null in this public release. | 0 |
| `large_consortium_work_count` | `int64` | Contributing Works at or above the consortium warning size. | Not null in this public release. | 0 |
| `excluded_threshold_work_count` | `int64` | Contributing Works excluded by the configured size policy. | Not null in this public release. | 0 |
| `maximum_consortium_size` | `int32` | Largest distinct-institution count among contributing Works. | Not null in this public release. | 0 |
| `topic_families` | `list<element: string>` | Sorted distinct configured Topic families observed on contributing Works. | Not null in this public release. | 0 |
| `work_ids_sample` | `list<element: string>` | Deterministic bounded sample of contributing source Work identifiers. | Not null in this public release. | 0 |
| `distinct_topic_family_count` | `int32` | Number of distinct Topic families contributing to the row. | Not null in this public release. | 0 |
| `active_years_3y` | `int8` | Active years in the fixed-denominator trailing three-year window. | Not null in this public release. | 0 |
| `active_years_5y` | `int8` | Active years in the fixed-denominator trailing five-year window. | Not null in this public release. | 0 |
| `source_work_count` | `int64` | Source endpoint value: distinct primary-corpus Works affiliated with the institution. | Not null in this public release. | 0 |
| `target_work_count` | `int64` | Target endpoint value: distinct primary-corpus Works affiliated with the institution. | Not null in this public release. | 0 |
| `normalized_intensity` | `double` | Fractional weight divided by geometric-mean institutional output. | Not null in this public release. | 0 |
| `persistence_3y` | `double` | Active-year share in the trailing three-year window. | Not null in this public release. | 0 |
| `persistence_5y` | `double` | Active-year share in the trailing five-year window. | Not null in this public release. | 0 |
| `persistence_3y_incomplete_window` | `bool` | True before a complete three-year history is available. | Not null in this public release. | 0 |
| `persistence_5y_incomplete_window` | `bool` | True before a complete five-year history is available. | Not null in this public release. | 0 |
| `visualization_score` | `double` | Non-primary composite used only to rank edges for display. | Not null in this public release. | 0 |
| `visualization_score_is_primary` | `bool` | Always false; guards against scientific interpretation. | Not null in this public release. | 0 |
| `visualization_score_method` | `string` | Stored description of the display-ranking calculation. | Not null in this public release. | 0 |
| `source_x` | `double` | Source endpoint value: seeded aggregate-layout horizontal coordinate reused across years. | Not null in this public release. | 0 |
| `source_y` | `double` | Source endpoint value: seeded aggregate-layout vertical coordinate reused across years. | Not null in this public release. | 0 |
| `target_x` | `double` | Target endpoint value: seeded aggregate-layout horizontal coordinate reused across years. | Not null in this public release. | 0 |
| `target_y` | `double` | Target endpoint value: seeded aggregate-layout vertical coordinate reused across years. | Not null in this public release. | 0 |
| `default_edge_rank` | `int32` | One-based rank under the default edge display policy. | Not null in this public release. | 0 |
| `default_edge_limit` | `int32` | Documented maximum edges displayed by default per view. | Not null in this public release. | 0 |
| `edge_width_encoding` | `string` | Stored description of the default edge-width field. | Not null in this public release. | 0 |
| `edge_color_encoding` | `string` | Stored description of the default edge-color field. | Not null in this public release. | 0 |

## `network_nodes`

Fixed-coordinate annual core-node metrics and primary communities.

- Path: `dashboard/data/network_nodes.parquet`
- Rows: 31,486
- Primary key: `year, corpus_view, hierarchy_view, institution_id`
- SHA-256: `9b1c868476190886318b2cdae037511724990530e52ecf905c382762cf503bf7`
- Direct source manifest: `.agent/manifests/network_view_nodes_year.json`
- Source manifests: `.agent/manifests/nodes_year.json, .agent/manifests/edges_metrics_year.json, .agent/manifests/communities_year.json, .agent/manifests/network_layout.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"network_view_policy": "fixed-layout-network-view-2026-08-06-v2"}`
- Code commit: `568bbdbf6b3f`
- Transformation: `python -m gisnet.cli build-network-view --resume`
- Known issue: The public visualization core is thresholded to 500 aggregate nodes.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `year` | `int64` | Complete publication calendar year. | Not null in this public release. | 0 |
| `corpus_view` | `string` | GIS corpus definition: strict or broad. | Not null in this public release. | 0 |
| `hierarchy_view` | `string` | Institution identity view: organization or documented umbrella. | Not null in this public release. | 0 |
| `institution_id` | `string` | Stable source institution identifier used as the node key. | Not null in this public release. | 0 |
| `display_name` | `string` | Source-provided or canonically selected institution display name. | Not null in this public release. | 0 |
| `ror_id` | `string` | Source-linked Research Organization Registry identifier, when available. | Not null in this public release. | 0 |
| `country_code` | `string` | Source country code associated with the institution. | Not null in this public release. | 0 |
| `country_name` | `string` | Frozen country or territory display name. | Not null in this public release. | 0 |
| `macro_region` | `string` | Frozen UN M49-style macro-region analytical grouping. | Not null in this public release. | 0 |
| `subregion` | `string` | Frozen UN M49-style subregion analytical grouping. | Not null in this public release. | 0 |
| `institution_category` | `string` | Configured analytical category for the source institution type. | Not null in this public release. | 0 |
| `analytical_scope` | `string` | Whether the row is in focal or retained contextual geographic scope. | Not null in this public release. | 0 |
| `latitude` | `double` | Source-provided institution latitude; never imputed. | Not null in this public release. | 0 |
| `longitude` | `double` | Source-provided institution longitude; never imputed. | Not null in this public release. | 0 |
| `work_count` | `int64` | Distinct primary-corpus Works affiliated with the institution. | Not null in this public release. | 0 |
| `fractional_work_count` | `double` | Institutional Work output under the stored fractional allocation. | Not null in this public release. | 0 |
| `collaborative_work_count` | `int64` | Distinct Works containing more than one institution. | Not null in this public release. | 0 |
| `single_institution_work_count` | `int64` | Distinct Works containing only this institution. | Not null in this public release. | 0 |
| `international_work_count` | `int64` | Distinct Works with institutions from multiple countries. | Not null in this public release. | 0 |
| `cross_region_work_count` | `int64` | Distinct Works with institutions from multiple macro-regions. | Not null in this public release. | 0 |
| `international_collaboration_share` | `double` | International Works divided by all included Works. | Not null in this public release. | 0 |
| `cross_region_collaboration_share` | `double` | Cross-region Works divided by all included Works. | Not null in this public release. | 0 |
| `degree` | `int64` | Number of distinct institutional partners in the annual graph. | Not null in this public release. | 0 |
| `full_strength` | `double` | Sum of incident full-count collaboration edge weights. | Not null in this public release. | 0 |
| `fractional_strength` | `double` | Sum of incident fractional collaboration edge weights. | Not null in this public release. | 0 |
| `betweenness` | `double` | Stored weighted betweenness centrality under the disclosed method. | Not null in this public release. | 0 |
| `betweenness_method` | `string` | Exact or cutoff weighted shortest-path method used for betweenness. | Not null in this public release. | 0 |
| `betweenness_sample_size` | `int64` | Number of graph vertices included by the betweenness method. | Not null in this public release. | 0 |
| `betweenness_cutoff` | `int64` | Maximum path length for approximated betweenness; null when exact. | Not null in this public release. | 0 |
| `betweenness_seed` | `int64` | Deterministic random seed recorded for the centrality computation. | Not null in this public release. | 0 |
| `pagerank` | `double` | Weighted PageRank centrality normalized within the annual graph. | Not null in this public release. | 0 |
| `bridge_score` | `double` | Documented cross-community/cross-region bridging indicator. | Not null in this public release. | 0 |
| `partner_country_count` | `int64` | Number of distinct partner countries. | Not null in this public release. | 0 |
| `partner_region_count` | `int64` | Number of distinct partner macro-regions. | Not null in this public release. | 0 |
| `x` | `double` | Seeded aggregate-layout horizontal coordinate reused across years. | Not null in this public release. | 0 |
| `y` | `double` | Seeded aggregate-layout vertical coordinate reused across years. | Not null in this public release. | 0 |
| `core_rank` | `int64` | Rank in the full-period aggregate visualization core. | Not null in this public release. | 0 |
| `community_id` | `string` | Stable annual primary-resolution community label; isolates are explicit. | Null means the source or derived value is unavailable; it is not coerced to zero. | 188 |
| `community_size` | `int64` | Number of nodes assigned to the labelled annual community. | Null means the source or derived value is unavailable; it is not coerced to zero. | 188 |
| `node_size_encoding` | `string` | Stored description of the default node-size field. | Not null in this public release. | 0 |
| `node_color_encoding` | `string` | Stored description of the default node-color field. | Not null in this public release. | 0 |
| `coordinate_encoding` | `string` | Stored description of the fixed node-coordinate encoding. | Not null in this public release. | 0 |

## `sensitivity`

Required alternative-definition comparisons and change flags.

- Path: `dashboard/data/sensitivity.parquet`
- Rows: 8
- Primary key: `comparison_id`
- SHA-256: `b3c90024e653a8df4a27d3d46278cb12b4763063fdc3153029d38727a31bf168`
- Direct source manifest: `.agent/manifests/sensitivity_matrix.json`
- Source manifests: `.agent/manifests/graph_metrics_year.json, .agent/manifests/edges_year.json, .agent/manifests/work_edges.json, .agent/manifests/nodes_year.json, .agent/manifests/work_institutions.json, .agent/manifests/work_corpus.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"sensitivity_policy": "required-sensitivity-matrix-2026-08-06-v2"}`
- Code commit: `568bbdbf6b3f`
- Transformation: `python -m gisnet.cli run-sensitivity --resume`
- Known issue: One reviewed-registry comparison is explicitly unavailable.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `comparison_id` | `string` | Stable sensitivity-comparison identifier. | Not null in this public release. | 0 |
| `comparison` | `string` | Human-readable sensitivity question. | Not null in this public release. | 0 |
| `metric` | `string` | Metric compared between the baseline and alternative. | Not null in this public release. | 0 |
| `baseline_label` | `string` | Label for the primary/baseline analytical choice. | Not null in this public release. | 0 |
| `alternative_label` | `string` | Label for the alternative analytical choice. | Not null in this public release. | 0 |
| `baseline_value` | `double` | Measured metric value under the baseline choice. | Null means the source or derived value is unavailable; it is not coerced to zero. | 1 |
| `alternative_value` | `double` | Measured metric value under the alternative choice. | Null means the source or derived value is unavailable; it is not coerced to zero. | 1 |
| `absolute_difference` | `double` | Absolute alternative-minus-baseline metric difference. | Null means the source or derived value is unavailable; it is not coerced to zero. | 1 |
| `absolute_relative_change` | `double` | Absolute difference divided by the baseline magnitude. | Null means the source or derived value is unavailable; it is not coerced to zero. | 1 |
| `major_change` | `bool` | Whether change meets the documented major-change rule. | Not null in this public release. | 0 |
| `major_change_rule` | `string` | Stored threshold rule used to set major_change. | Not null in this public release. | 0 |
| `status` | `string` | Availability/completion state of the sensitivity comparison. | Not null in this public release. | 0 |
| `primary_result_overwritten` | `bool` | Always false; alternatives never replace primary results. | Not null in this public release. | 0 |

## `topics`

Topic-family aggregates derived from the visible fixed-layout edge core.

- Path: `dashboard/data/topics.parquet`
- Rows: 826
- Primary key: `year, corpus_view, hierarchy_view, topic_family`
- SHA-256: `d9fe8030f216267d1c8924151aecf020734aadaa459d1e03498e8ee26ae180e9`
- Direct source manifest: `.agent/manifests/network_view_edges_year.json`
- Source manifests: `.agent/manifests/nodes_year.json, .agent/manifests/edges_metrics_year.json, .agent/manifests/communities_year.json, .agent/manifests/network_layout.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"network_view_policy": "fixed-layout-network-view-2026-08-06-v2"}`
- Code commit: `568bbdbf6b3f`
- Transformation: `python -m gisnet.cli build-network-view --resume`
- Known issue: Topic decisions are provisional and the table covers visible edges only.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `year` | `int32` | Complete publication calendar year. | Not null in this public release. | 0 |
| `corpus_view` | `string` | GIS corpus definition: strict or broad. | Not null in this public release. | 0 |
| `hierarchy_view` | `string` | Institution identity view: organization or documented umbrella. | Not null in this public release. | 0 |
| `topic_family` | `string` | Configured Topic-family label. | Not null in this public release. | 0 |
| `visible_edge_count` | `int64` | Number of visible fixed-layout edges in the aggregate. | Not null in this public release. | 0 |
| `full_count` | `int64` | Full-count collaboration weight: one per institution pair per Work. | Not null in this public release. | 0 |
| `fractional_count` | `double` | Fractional weight: one divided by the number of pairs on each Work. | Not null in this public release. | 0 |
| `edge_work_count_sum` | `int64` | Sum of edge-level distinct Work counts; not globally deduplicated. | Not null in this public release. | 0 |
| `coverage_note` | `string` | Statement delimiting the public Topic aggregate coverage. | Not null in this public release. | 0 |

## `trends`

Annual macro-region collaboration trend series for complete calendar years.

- Path: `dashboard/data/trends.parquet`
- Rows: 384
- Primary key: `year, corpus_view, hierarchy_view, source_region, target_region`
- SHA-256: `0efa8771cd5e4c3554b888b181b0daa4786fd5cce39e887db9b6667669b7a820`
- Direct source manifest: `.agent/manifests/trend_series_year.json`
- Source manifests: `.agent/manifests/region_flows_year.json`
- Configuration hashes: `{"project": "e736ea3adad86f85e79b7fe87c031fd1b103f2a9c45b80df12d39cf80dad14b1"}`
- Source versions: `{"trend_figure_policy": "annual-region-trends-2026-08-17-v2"}`
- Code commit: `306db695f048`
- Transformation: `python -m gisnet.cli build-figures --resume`
- Known issue: The last included year is 2025; partial 2026 observations are excluded.

| Column | Arrow type | Description | Null semantics | Null count |
|---|---|---|---|---:|
| `year` | `int32` | Complete publication calendar year. | Not null in this public release. | 0 |
| `corpus_view` | `string` | GIS corpus definition: strict or broad. | Not null in this public release. | 0 |
| `hierarchy_view` | `string` | Institution identity view: organization or documented umbrella. | Not null in this public release. | 0 |
| `source_region` | `string` | Source endpoint value: frozen macro-region label for an undirected edge endpoint. | Not null in this public release. | 0 |
| `target_region` | `string` | Target endpoint value: frozen macro-region label for an undirected edge endpoint. | Not null in this public release. | 0 |
| `region_pair` | `string` | Stable unordered macro-region pair display label. | Not null in this public release. | 0 |
| `is_intra_region` | `bool` | True when both geographic endpoints are the same macro-region. | Not null in this public release. | 0 |
| `full_count` | `int64` | Full-count collaboration weight: one per institution pair per Work. | Not null in this public release. | 0 |
| `fractional_count` | `double` | Fractional weight: one divided by the number of pairs on each Work. | Not null in this public release. | 0 |
| `normalized_share` | `double` | Fractional cell weight divided by the applicable annual total. | Not null in this public release. | 0 |
| `distinct_work_count` | `int64` | Number of distinct source Work identifiers contributing to the row. | Not null in this public release. | 0 |
| `distinct_institution_pair_count` | `int64` | Distinct source institution pairs contributing to the row. | Not null in this public release. | 0 |
| `year_status` | `string` | Complete-year status label. | Not null in this public release. | 0 |
| `units_note` | `string` | Human-readable statement of stored counting units. | Not null in this public release. | 0 |

## Privacy and release boundary

The dictionary covers only the compact public aggregate/thresholded tables in
`dashboard/data/`. Raw API pages, cache contents, credentials, and private local
paths are outside the release boundary.
