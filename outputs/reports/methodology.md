# Methodology: Dynamic GIS Institutional Collaboration Network

Generated from validated repository artifacts. Data version: `gisnet-0.1.0`.
The numerical statements below are taken from the cited relative-path summaries and manifests.

## Analytical modes

**Historical scientific mode** uses complete calendar-year evidence from
2010 through 2025. It is the stable longitudinal mode for
annual outputs, networks, communities, geographic flows, citation flow, Topic proximity, and
sensitivity analysis.

**Current school-decision mode** adds exact publication month and quarter facts plus rolling 12-,
24-, and 36-month publication windows. The released snapshot ends at
2025-12; it does not mix a partial 2026 overlay into the historical
annual series. `School` is concise interface language for an eligible university or research
institution, not an admissions ranking, degree-program claim, or universal measure of quality.

## 1. Research questions

This project asks how institutional GIS and broader geospatial research collaboration changed
annually, which institutions connected regional communities, how patterns differ between Strict
and Broad corpus definitions, how current school profiles differ across declared rolling windows,
and how conclusions respond to documented analytical choices.
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
dashboard viewing uses 27 processed public tables and makes
no OpenAlex or ROR request.

School-decision publication time is bibliographic observation time, not collaboration, research,
project, or author-mobility start time. The source supplies a full date string without an
independent precision flag, so January-first values are retained and measured rather than
heuristically removed.
In this snapshot, 1,176,947 normalized Works have date-QA facts;
missing, malformed, conflicting, or out-of-range dates would remain annual-only and are never given
fabricated months or days.

Validated sparse subannual facts contain 1,745,888
positive institution-month rows and
4,407,772 positive collaboration-edge-month rows.
Zeros are derived from declared entity/period denominators rather than materialized or imputed.

## 5. Institution resolution

Stable OpenAlex institution IDs are the primary keys; source ROR IDs are preserved when present.
The master contains 46,812 institutions, with
1,702 metadata QA rows. Two explicit hierarchy views are retained:
organization and umbrella. The hierarchy contains 93,624 rows and
9,313 review candidates. Automatic name-only collapses:
0; explicit configured collapses:
0. Similar names therefore do not silently resolve to one
record.

The school-identity layer contains 46,812 canonical source
institutions and performs 0 automatic and
0 explicit collapses. The eligible search index contains
28,042 stable IDs across every stored macro-region, including
27,542 outside the prior 500-node visualization core.
Eligibility depends on Broad-primary research evidence, not visualization rank, coordinate
availability, admissions status, or a quality threshold.

## 6. Counting methods

For a Work with *k* distinct institutions, every undirected pair receives full weight 1 and
fractional weight `1 / choose(k, 2) = 2 / (k * (k - 1))`. Fractional contributions therefore sum
to 1 per collaborative Work. The stored maximum fractional reconciliation error is
9.83e-14. The consortium warning and exclusion
thresholds are 25 and
100 institutions. Primary annual output contains
2,999,736 edge observations. Normalized intensity divides fractional edge
weight by the geometric mean of the two institutions' full Work counts.

International collaboration share is the number of included institutional Works spanning more than
one country divided by **all included institutional Works** in the declared period. Cross-region
collaboration share uses the same denominator and counts Works spanning more than one macro-region.
Bridge score is cross-macro-region fractional strength divided by total fractional strength. These
shares are descriptive research metrics, not quality scores.

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
For school-decision evidence, rolling persistence is active publication months divided by 12, 24,
or 36; complete-quarter persistence is active publication months divided by 3; and annual ego
persistence is active years in the trailing five-year window divided by 5. Publication persistence
does not identify a relationship start date or continuous relationship duration.
Adjacent-year communities use `deterministic greedy one-to-one assignment by descending Jaccard, intersection, then annual community IDs`. Matches below Jaccard
0.25 are uncertain; the release records
1,501 such matches plus explicit split, merge, birth, and
disappearance event rows.
Visualization score is not a primary scientific metric; it only ranks edges for display.

## 8. Validation

Edge arithmetic reports 4,505,668 Work-edge rows and
1,062,936 collaborative Work-view observations. The release
reproducibility check validated 27 core datasets with
0 checksum mismatches and
0 incomplete temporary outputs. Recovery tests cover
pagination resumption, failed atomic validation, corrupt-cache quarantine, invalid-state backup,
and deterministic normalization. The stored PageRank sum error is
3.61e-14.

The final cross-layer school-decision matrix passed all 13 of
13 acceptance checks. The stored release therefore
passed all 13 of 13 acceptance checks. It covers outside-core search and partner availability,
subannual and rolling reconciliation, publication-date safety, geographic map/matrix and width
equality, Profile/Compare source equality, Strict-within-Broad, privacy, deterministic checksums,
and annual regressions.
The complete index has 28,042 schools; the compact partner index
has 1,048,856 directed rows for
19,212 schools, including
18,712 outside the former global edge core.
Profiles retain 168,252 explicit school/corpus/window rows,
including unsupported or no-recent-activity states rather than fabricated values.

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
coordinate coverage ranges from 99.92% to
100.00%; no coordinate is invented. The
network dashboard is a thresholded view and must not be used to infer absence from a hidden edge.
No partial 2026 data are included; 2025 is the last complete calendar year.

`School` does not assert degree-granting status, programme availability, admissions likelihood,
teaching quality, cost, or student fit. School Finder and comparison ordering are not an admissions
ranking, and the release defines no universal institutional-quality score. Rolling values can be
sparse or incomplete and inherit publication-date precision limits. The partner index retains at
most 50 partners per school, corpus, and latest rolling window; country and
macro-region ego summaries therefore aggregate retained partners rather than a complete global
edge matrix. The current school identity is byte-equivalent to organization identity; future
evidence-backed collapses require rebuilding dependent facts.

Optional layer analysis preserves three distinct network meanings. Co-authorship is an undirected
fractional collaboration layer; citation flow is directed from citing to cited institution; and
cosine Topic similarity is an undirected research-proximity layer over a deterministic annual
500-institution core. The multiplex comparison reports each layer separately and computes only
unweighted node and dyad presence overlap. Citation direction is ignored for dyad matching only.
No layer weights are combined, no composite score is defined, and Topic-layer overlap inherits the
annual-core coverage boundary.

## 11. Reproducibility

Run from the repository root:

```bash
# Historical scientific mode.
uv run python -m gisnet.cli run-pipeline \
  --start-year 2010 --end-year 2025 \
  --corpus all --hierarchy all --resume

# Current school-decision mode from the validated historical layer.
uv run python -m gisnet.cli validate-school-contract --resume
uv run python -m gisnet.cli build-publication-date-qa --resume
uv run python -m gisnet.cli build-subannual-facts --resume
uv run python -m gisnet.cli build-rolling-facts --resume
uv run python -m gisnet.cli build-school-identities --resume
uv run python -m gisnet.cli build-school-index --resume
uv run python -m gisnet.cli build-school-partners --resume
uv run python -m gisnet.cli build-school-profiles --resume
uv run python -m gisnet.cli build-dashboard-data --resume
uv run python -m gisnet.cli validate-school-decision --resume
uv run python -m gisnet.cli report --resume
uv run python -m gisnet.cli build-data-dictionary --resume
uv run python -m gisnet.release build --root .
uv run python -m gisnet.release verify --root .
```

The command validates hashes and provenance, skips valid stages, resumes incomplete downloads,
rebuilds only stale dependency branches, preserves valid raw pages, and prints the next recovery
command on failure. Generated static figures are:

- `figures/annual_region_trends.svg`, derived from `data/processed/trend_series_year.parquet`;
- `figures/view_comparison.svg`, derived from `data/processed/trend_series_year.parquet`;
- `figures/region_matrix.svg`, derived from
  `data/processed/collaboration_matrix_year.parquet`.

Visual encodings are stable across the dashboard and static figures. Macro-regions use a fixed,
color-vision-conscious categorical palette; time series also use dash patterns so color is not the
only cue. Share matrices and maps use one ordered Cividis scale. Missing cells remain distinct from
observed zero, network edge width stays constant, and exact values remain available in hover text,
accessible descriptions, or companion tables.

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
- Community continuity: `data/reference/community_continuity_summary.json`
- Sensitivity: `data/reference/sensitivity_summary.json`
- Reproducibility: `data/reference/reproducibility_validation.json`
- Publication-date QA: `data/reference/publication_date_qa_summary.json`
- Month and quarter facts: `data/reference/subannual_temporal_summary.json`
- Rolling 12/24/36-month facts: `data/reference/rolling_temporal_summary.json`
- School identity/index: `data/reference/school_identity_summary.json` and
  `data/reference/school_index_summary.json`
- School partners/profiles: `data/reference/school_partner_index_summary.json` and
  `data/reference/school_profile_summary.json`
- School-decision acceptance: `data/reference/school_decision_validation.json`
- Optional multiplex comparison: `data/reference/multiplex_comparison_summary.json`
- Figures: `data/reference/annual_trends_summary.json` and
  `data/reference/collaboration_matrix_summary.json`
