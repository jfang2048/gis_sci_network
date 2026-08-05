# Methodology: Dynamic GIS Institutional Collaboration Network

Generated from validated repository artifacts. Data version: `gisnet-0.1.0`.
The numerical statements below are taken from the cited relative-path summaries and manifests.

## 1. Research questions

This project asks how institutional GIS and broader geospatial research collaboration changed
annually, which institutions connected regional communities, how patterns differ between Strict
and Broad corpus definitions, and how conclusions respond to documented analytical choices.
The unit of collaboration is an observed co-authored Work affiliation pair, not a citation or an
inferred relationship.

## 2. Geographic scope

Complete calendar years 2010-2025 are included. The focal
macro-regions are Europe, Asia, and the Americas; Africa and Oceania remain represented so that
mixed-region collaborations are not discarded. The frozen registry contains 251
country or territory rows and uses `un-m49-retrieved-2026-08-05`. Macro-region, subregion, and
country matrices contain 97,762 observed sparse rows.

## 3. GIS corpus definitions

The frozen OpenAlex Topic registry is `provisional-ai-2026-08-05-v1`. The Strict view contains
6 Topics; the Broad view contains 23; 7 uncertain Topics
are excluded from primary results and retained for sensitivity analysis. The registry is
**provisional and AI-reviewed**. No human review has occurred. The primary corpus contains
190,205 Strict and 1,005,606 Broad Works from
1,176,947 normalized Works. Expanded-type and preprint variants remain separate
sensitivity views. Exact definitions live in `config/topic_registry.yml` and
`config/work_types.yml`.

## 4. Data sources

Bibliographic Works, authorships, institutions, Topics, and source identifiers come from OpenAlex.
Raw pages are cached with checksums and query IDs before normalization; the completed acquisition
contains only source-provided identifiers. ROR is an optional source for cached institution
enrichment, and UN Statistics Division M49 is the source for the geographic convention. Ordinary
dashboard viewing uses 11 processed public tables and makes
no OpenAlex or ROR request.

## 5. Institution resolution

Stable OpenAlex institution IDs are the primary keys; source ROR IDs are preserved when present.
The master contains 46,812 institutions, with
1,679 metadata QA rows. Two explicit hierarchy views are retained:
organization and umbrella. The hierarchy contains 93,624 rows and
8,971 review candidates. Automatic name-only collapses:
0; explicit configured collapses:
0. Similar names therefore do not silently resolve to one
record.

## 6. Counting methods

For a Work with *k* distinct institutions, every undirected pair receives full weight 1 and
fractional weight `1 / choose(k, 2)`. Fractional contributions therefore sum to 1 per collaborative
Work. The stored maximum fractional reconciliation error is
9.83e-14. The consortium warning and exclusion
thresholds are 25 and
100 institutions. Primary annual output contains
2,999,736 edge observations. Normalized intensity divides fractional edge
weight by the geometric mean of the two institutions' fractional output.

## 7. Dynamic network metrics

Each year/corpus/hierarchy combination is an undirected weighted graph. Stored metrics include
degree, full and fractional strength, weighted betweenness, PageRank, connected components,
density, assortativity, bridge score, and Leiden community assignments. Exact weighted
betweenness is used through 10,000 nodes; larger graphs
use `igraph weighted shortest paths with cutoff=3` and disclose that approximation. The
64 graph rows and 501,890 node rows
use seed 20250805. Leiden resolutions are
0.5, 1.0, 1.5; 1.0 is primary. Persistence uses
fixed-denominator trailing windows of 3 and 5 years and flags incomplete early windows.
Visualization score is not a primary scientific metric; it only ranks edges for display.

## 8. Validation

Edge arithmetic reports 4,505,668 Work-edge rows and
1,062,936 collaborative Work-view observations. The release
reproducibility check validated 12 core datasets with
0 checksum mismatches and
0 incomplete temporary outputs. Recovery tests cover
pagination resumption, failed atomic validation, corrupt-cache quarantine, invalid-state backup,
and deterministic normalization. The stored PageRank sum error is
3.47e-14.

## 9. Sensitivity analysis

The required matrix contains 8 comparisons:
7 complete and
1 explicitly unavailable. A change of at least
20% is flagged; 3
comparisons meet that threshold. Sensitivity results never overwrite the primary result
(`primary_result_overwritten = false`). Exact
rows are stored in `data/processed/sensitivity_matrix.parquet` and the public dashboard extract.

## 10. Limitations

Topic decisions remain provisional and may include false positives or false negatives. Affiliation
metadata can be incomplete, hierarchy candidates require human review, and OpenAlex coverage is
not a census of all scholarship. Collaboration is co-authorship, not citation flow, knowledge
flow, research similarity, or causality. Version diagnostics identify
10,679 ambiguous possible Work families. Sourced
coordinate coverage ranges from 0.10% to
0.33%; no coordinate is invented. The
network dashboard is a thresholded view and must not be used to infer absence from a hidden edge.
No partial 2026 data are included; 2025 is the last complete calendar year.

## 11. Reproducibility

Run from the repository root:

```bash
uv run python -m gisnet.cli run-pipeline \
  --start-year 2010 --end-year 2025 \
  --corpus all --hierarchy all --resume
```

The command validates hashes and provenance, skips valid stages, resumes incomplete downloads,
rebuilds only stale dependency branches, preserves valid raw pages, and prints the next recovery
command on failure. Generated static figures are:

- `figures/annual_region_trends.svg`, derived from `data/processed/trend_series_year.parquet`;
- `figures/view_comparison.svg`, derived from `data/processed/trend_series_year.parquet`;
- `figures/region_matrix.svg`, derived from
  `data/processed/collaboration_matrix_year.parquet`.

All reported figures are generated from processed data. The trend summary covers
384 rows and reports `partial_years_included =
false`; matrix reconciliation failures are
0.

## 12. Data ethics and geographic naming convention

Only public scholarly metadata and aggregate/thresholded derived tables are released. API keys,
raw response caches, and private local paths are excluded. Institution identifiers are retained to
support auditability; rankings should not be interpreted as measures of institutional quality.
The geographic convention is UN M49-style and is a technical analytical grouping, not a political
statement about sovereignty, borders, recognition, or affiliation. Missing geography remains
Unknown rather than being guessed.

## Evidence inventory

- Corpus: `data/reference/work_corpus_summary.json`
- Institutions: `data/reference/institution_master_summary.json`
- Edges: `data/reference/collaboration_edges_summary.json`
- Metrics: `data/reference/network_metrics_summary.json`
- Communities: `data/reference/community_detection_summary.json`
- Sensitivity: `data/reference/sensitivity_summary.json`
- Reproducibility: `data/reference/reproducibility_validation.json`
- Figures: `data/reference/annual_trends_summary.json` and
  `data/reference/collaboration_matrix_summary.json`
