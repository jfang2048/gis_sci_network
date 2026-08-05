# AI Execution Backlog: Dynamic GIS Institutional Collaboration Network

## 0. Mission

Build a reproducible research pipeline and interactive visualization system that measures annual GIS and broader geospatial research collaboration among universities and research institutions in three macro-regions:

1. Europe
2. Asia
3. Americas

The Americas scope means Northern America, Central America, the Caribbean, and South America. It is not limited to the United States.

The Asia scope means Eastern Asia, Southeastern Asia, Southern Asia, Central Asia, and Western Asia. It is not limited to China.

The system must preserve country and subregion detail so users can still inspect China, Japan, India, the United States, Canada, Brazil, Germany, Italy, and any other country independently.

The default complete-year analysis window is 2010 through 2025. Do not mix partial 2026 data with complete annual results. Partial-year support may exist as an explicitly labelled optional mode.

## 1. Agent execution contract

This repository may be processed by an AI coding agent multiple times. Every run must be resumable, idempotent, and auditable.

### 1.1 Mandatory behavior at the start of every run

1. Read this file completely.
2. Inspect `git status`, the repository tree, `README.md`, `AGENTS.md`, `.agent/state.json`, `.agent/backlog.json`, and the latest entries in `.agent/RUNLOG.md`.
3. Do not overwrite uncommitted user changes.
4. Do not repeat a task already marked `DONE` unless its inputs or configuration hash changed.
5. Validate repository state before editing.
6. Select the highest-priority unblocked task.
7. Prefer one complete, testable task over several partially completed tasks.
8. If the API key is missing, continue all offline tasks and mark only network-dependent tasks as blocked.
9. Never invent OpenAlex Topic IDs, institution IDs, ROR IDs, counts, metrics, or completed results.
10. Never print, commit, cache, serialize, or include the API key in logs, URLs stored on disk, exception messages, notebooks, screenshots, or reports.

### 1.2 Mandatory behavior at the end of every run

1. Run tests relevant to changed code.
2. Run formatting and static checks.
3. Write outputs atomically.
4. Update `.agent/state.json`.
5. Update task statuses in `.agent/backlog.json`.
6. Append a run record to `.agent/RUNLOG.md`.
7. Record all created or changed files.
8. Record commands executed and validation results.
9. Record unresolved failures with enough information for the next run.
10. Create a small local git commit when the task is complete and the working tree does not contain unrelated user changes. Never push unless explicitly requested.

### 1.3 Task states

Use only these states:

1. `TODO`
2. `IN_PROGRESS`
3. `BLOCKED`
4. `DONE`
5. `SKIPPED`
6. `STALE`

A `DONE` task becomes `STALE` when a dependency, schema version, configuration hash, or source-data version changes in a way that invalidates its output.

### 1.4 Persistent state

Create and maintain:

```text
.agent/
├── backlog.json
├── state.json
├── RUNLOG.md
├── decisions.md
├── locks/
├── checkpoints/
├── manifests/
└── failures/
```

Minimum `.agent/state.json` schema:

```json
{
  "schema_version": 1,
  "project_version": "0.1.0",
  "active_run_id": null,
  "last_successful_run_id": null,
  "current_task_id": null,
  "completed_task_ids": [],
  "blocked_task_ids": [],
  "config_hashes": {},
  "source_versions": {},
  "dataset_manifests": {},
  "download_checkpoints": {},
  "last_updated_utc": null
}
```

Each run ID must use:

```text
YYYYMMDDTHHMMSSZ_<short_git_sha_or_nogit>
```

Use `.agent/locks/run.lock` to prevent concurrent writers. Store run ID, PID, hostname, start time, and task ID. Treat a lock as stale only after checking that its process is absent or its age exceeds a configurable threshold.

### 1.5 Atomicity and recovery rules

1. Write every dataset to a temporary path first.
2. Validate row count, schema, uniqueness constraints, and file readability.
3. Rename the temporary output to its final path only after validation.
4. Write a manifest after the final rename.
5. Persist an API pagination checkpoint only after the corresponding raw page is fully written and validated.
6. On restart, verify the last raw page against its checksum before continuing from its stored `next_cursor`.
7. Deduplicate all data by stable IDs after concatenating pages or query shards.
8. A repeated run with unchanged inputs must produce the same logical output and must not create duplicate records.

## 2. Secrets and environment

The user will provide the OpenAlex key using:

```bash
export openalex_api='**********'
```

The application must accept both variable names, in this priority order:

```python
api_key = os.getenv("OPENALEX_API_KEY") or os.getenv("openalex_api")
```

Also provide the canonical optional form:

```bash
export OPENALEX_API_KEY='**********'
```

Never require the secret to be written into a tracked file.

Create `.env.example` containing empty placeholders only:

```dotenv
OPENALEX_API_KEY=
openalex_api=
```

Add the following to `.gitignore`:

```gitignore
.env
.env.*
!.env.example
.agent/locks/
data/raw/
data/interim/
data/cache/
outputs/private/
```

Provide `python -m gisnet.cli check-env`. It must:

1. Detect whether a key exists.
2. Never display the key.
3. Make one lightweight authenticated request when network access is allowed.
4. Report success, authentication failure, rate-limit state, or network failure.
5. Return a nonzero exit code only when the requested operation requires API access.

## 3. Research definition

### 3.1 Main unit of analysis

A node is an institution in a specific year.

An edge exists when two distinct institutions appear on the same included work.

Use two institution-resolution views:

1. `organization`: retain independent OpenAlex or ROR organization records.
2. `umbrella`: collapse selected child organizations into a canonical parent according to versioned rules.

Never overwrite the original organization identity. Store original and canonical IDs separately.

### 3.2 Corpus views

Create two reproducible GIS corpus definitions.

#### Strict GIS

Include work whose primary intellectual or methodological contribution belongs to one or more of:

1. Geographic information science
2. Geographic information systems
3. Spatial analysis
4. Spatial statistics and geostatistics
5. Geocomputation
6. Spatial databases and indexing
7. Cartography and geovisualization
8. Spatial data infrastructures
9. Location-based and geospatial information services

#### Broad Geospatial

Include all Strict GIS work plus:

1. Remote sensing and Earth observation
2. Photogrammetry
3. Point-cloud and LiDAR processing
4. Geodesy
5. GNSS and positioning
6. GeoAI
7. Geospatial computer vision
8. Digital twins with explicit spatial methods
9. Spatial modelling in environmental, urban, mobility, and transport research

Every included OpenAlex Topic must be stored in a versioned registry with:

```text
topic_id
display_name
description
domain_id
field_id
subfield_id
corpus_membership
method_family
decision
decision_reason
review_status
retrieved_at
source_version
```

Possible `corpus_membership` values:

1. `strict`
2. `broad_only`
3. `excluded`
4. `uncertain`

Do not proceed from keyword guesses alone. Topic discovery, sampled work inspection, and a documented decision are required.

### 3.3 Work inclusion policy

Primary analysis:

1. Publication years 2010 through 2025.
2. Exclude retracted work.
3. Exclude paratext and records without a meaningful scholarly work identity.
4. Include peer-reviewed article-like and conference-like outputs according to a versioned type policy.
5. Treat preprints as a separate sensitivity view unless no published version exists.
6. Deduplicate by OpenAlex Work ID first.
7. Build an optional DOI and version-family deduplication layer without deleting raw records.
8. Preserve works with one institution for institutional output counts.
9. Use only works with at least two distinct institutions when building collaboration edges.

Create `config/work_types.yml` only after profiling available types in the selected corpus.

### 3.4 Institution scope

The primary analysis includes universities and research institutions.

Initial primary institution categories:

1. Education
2. Government research organization
3. Nonprofit research organization
4. Research facility

Secondary categories retained for sensitivity and filtering:

1. Healthcare
2. Company
3. Archive
4. Other or unknown

Do not silently discard secondary institutions from raw or normalized data. Add an analysis flag such as `is_primary_research_scope`.

### 3.5 Geographic scope

Build a frozen, versioned country-to-region mapping from ISO alpha-2 codes.

Required fields:

```text
country_code
country_name
macro_region
subregion
mapping_source
mapping_version
manual_override
override_reason
```

Required macro-regions:

1. `Europe`
2. `Asia`
3. `Americas`
4. `Africa`
5. `Oceania`
6. `Unknown`

Primary comparisons use Europe, Asia, and Americas. Africa and Oceania remain in the data as `Other` or as explicit regions so mixed collaborations are not lost.

Required subregions:

```text
Northern Europe
Western Europe
Southern Europe
Eastern Europe
Eastern Asia
Southeastern Asia
Southern Asia
Central Asia
Western Asia
Northern America
Central America
Caribbean
South America
Northern Africa
Sub-Saharan Africa
Australia and New Zealand
Melanesia
Micronesia
Polynesia
Unknown
```

Transcontinental cases must be handled by the frozen mapping and documented in `config/region_overrides.yml`. Never make hidden case-by-case decisions in code.

The mapping is a technical analytical convention and must not be presented as a political claim.

### 3.6 Primary region-pair outputs

Produce annual metrics for:

1. Europe to Europe
2. Asia to Asia
3. Americas to Americas
4. Europe to Asia
5. Europe to Americas
6. Asia to Americas
7. Each primary region to Other
8. Country-to-country pairs
9. Subregion-to-subregion pairs

Store region pairs in deterministic lexical order so `Europe|Asia` and `Asia|Europe` cannot appear as separate undirected pairs.

## 4. Metrics

### 4.1 Full counting

For each included work and each unordered pair of distinct institutions, add 1:

\[
C^{full}_{ij,t} = \sum_{p \in t} \mathbf{1}(i,j \in p)
\]

### 4.2 Fractional counting

If a work contains \(k_p\) distinct institutions, assign each pair:

\[
w^{frac}_{ij,p} = \frac{2}{k_p(k_p-1)}
\]

The total pair weight contributed by one work is 1.

### 4.3 Institutional output

For institution \(i\) in year \(t\):

\[
P_{i,t} = \text{number of included works containing } i
\]

Also compute fractional institutional output:

\[
P^{frac}_{i,t} = \sum_{p \ni i}\frac{1}{k_p}
\]

### 4.4 Normalized collaboration intensity

Primary normalized edge intensity:

\[
I_{ij,t} =
\frac{C^{frac}_{ij,t}}
{\sqrt{P_{i,t}P_{j,t}}}
\]

Guard against zero denominators and document the exact output convention.

### 4.5 Persistence

For a configurable trailing window \(w\), default 5:

\[
R^{(w)}_{ij,t}
=
\frac{1}{w}
\sum_{\tau=t-w+1}^{t}
\mathbf{1}(C^{frac}_{ij,\tau}>0)
\]

Mark incomplete early windows and do not silently divide by a shorter period unless a separate `available_year_fraction` metric is used.

### 4.6 Node metrics

For each year, corpus view, and hierarchy view, compute:

1. Work count
2. Fractional work count
3. Degree
4. Weighted degree using full count
5. Weighted degree using fractional count
6. International collaboration share
7. Cross-macro-region collaboration share
8. Betweenness centrality
9. PageRank or eigenvector centrality
10. Community ID
11. Community continuity ID
12. Bridge score between macro-regions
13. Number of active countries and regions among partners

For large graphs, exact betweenness may be replaced with a deterministic approximation. Store the method, sample size, and seed with the result.

### 4.7 Network metrics

For each annual graph:

1. Node count
2. Edge count
3. Density
4. Mean degree
5. Mean weighted degree
6. Connected component count
7. Largest connected component share
8. Modularity
9. Macro-region assortativity
10. Country assortativity
11. Cross-region edge share
12. Cross-region fractional-weight share
13. New edge count
14. Continuing edge count
15. Disappearing edge count

### 4.8 Composite score

A composite score may be used only for interactive ranking:

\[
S_{ij,t}
=
0.5Q(C^{frac}_{ij,t})
+
0.3Q(I_{ij,t})
+
0.2R^{(5)}_{ij,t}
\]

Here \(Q\) is the within-year percentile rank.

Label it `visualization_score`. Do not present it as a universal scientific measure.

## 5. Target architecture

Use Python 3.11 or newer.

Preferred stack:

```text
HTTP and retry: httpx or requests with a dedicated retry layer
Configuration: pydantic-settings and YAML
Tabular processing: Polars
Analytical SQL and validation: DuckDB
Storage: Parquet
Graph construction: igraph
Community detection: Leiden
Small-graph reference tests: NetworkX
Geospatial processing: GeoPandas and Shapely
Visualization: Plotly and PyDeck
Dashboard: Streamlit
Testing: pytest
Formatting and linting: Ruff
Type checking: mypy or pyright
```

Do not introduce distributed systems until profiling demonstrates a need.

### 5.1 Required repository structure

```text
gis-collaboration/
├── AGENTS.md
├── AI_EXECUTION_BACKLOG.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── config/
│   ├── project.yml
│   ├── regions.yml
│   ├── region_overrides.yml
│   ├── topic_registry.yml
│   ├── work_types.yml
│   ├── institution_types.yml
│   └── institution_overrides.csv
├── data/
│   ├── reference/
│   ├── raw/
│   │   ├── openalex/
│   │   └── ror/
│   ├── interim/
│   ├── processed/
│   └── cache/
├── src/gisnet/
│   ├── __init__.py
│   ├── cli.py
│   ├── config.py
│   ├── logging.py
│   ├── state.py
│   ├── manifest.py
│   ├── openalex/
│   ├── ror/
│   ├── corpus/
│   ├── institutions/
│   ├── network/
│   ├── validation/
│   └── visualization/
├── dashboard/
│   └── app.py
├── scripts/
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── fixtures/
│   └── data/
├── notebooks/
├── outputs/
│   ├── figures/
│   ├── tables/
│   ├── dashboard/
│   └── reports/
└── .agent/
```

Notebooks may explore or verify. Production transformations must live in `src/gisnet`.

## 6. Data contracts

### 6.1 `works.parquet`

Minimum columns:

```text
work_id
doi
title
publication_year
publication_date
work_type
is_retracted
is_paratext
cited_by_count
fwci
primary_topic_id
topic_ids
referenced_work_ids
updated_date
source_query_ids
raw_record_hash
```

Primary key: `work_id`.

### 6.2 `work_topics.parquet`

```text
work_id
topic_id
topic_score
is_primary_topic
corpus_membership
method_family
```

Primary key: `work_id, topic_id`.

### 6.3 `work_institutions.parquet`

```text
work_id
publication_year
institution_id
ror_id
display_name
country_code
macro_region
subregion
institution_type
is_primary_research_scope
original_institution_id
canonical_institution_id
hierarchy_view
affiliation_source
affiliation_confidence
raw_affiliation_strings
```

Primary key for each hierarchy view:

```text
work_id, canonical_institution_id, hierarchy_view
```

A work with multiple authors from the same institution must contain one row for that institution and hierarchy view.

### 6.4 `institutions.parquet`

```text
institution_id
ror_id
display_name
alternative_names
country_code
country_name
macro_region
subregion
institution_type
latitude
longitude
parent_ids
child_ids
predecessor_ids
successor_ids
lineage
canonical_institution_id
canonicalization_rule_id
is_primary_research_scope
openalex_updated_date
ror_version
```

### 6.5 `edges_year.parquet`

```text
year
corpus_view
hierarchy_view
source_id
target_id
source_region
target_region
full_count
fractional_count
normalized_intensity
persistence_3y
persistence_5y
visualization_score
distinct_topic_count
dominant_topic_family
large_consortium_work_count
work_ids_sample
```

Primary key:

```text
year, corpus_view, hierarchy_view, source_id, target_id
```

Require `source_id < target_id` using canonical string ordering.

### 6.6 `nodes_year.parquet`

```text
year
corpus_view
hierarchy_view
institution_id
work_count
fractional_work_count
degree
full_strength
fractional_strength
betweenness
betweenness_method
pagerank
community_id
community_continuity_id
international_collaboration_share
cross_region_collaboration_share
bridge_score
partner_country_count
partner_region_count
```

### 6.7 `region_flows_year.parquet`

```text
year
corpus_view
hierarchy_view
source_region
target_region
full_count
fractional_count
distinct_work_count
distinct_institution_pair_count
normalized_share
```

### 6.8 Manifest schema

Every generated dataset requires a JSON manifest:

```json
{
  "dataset_name": "edges_year",
  "schema_version": 1,
  "created_at_utc": "",
  "run_id": "",
  "git_commit": "",
  "config_hashes": {},
  "source_manifests": [],
  "row_count": 0,
  "column_count": 0,
  "primary_key": [],
  "min_year": null,
  "max_year": null,
  "null_counts": {},
  "checksum_sha256": "",
  "command": "",
  "status": "valid"
}
```

## 7. Command-line interface

Implement one CLI entry point:

```bash
python -m gisnet.cli COMMAND
```

Required commands:

```text
check-env
status
next-task
discover-topics
sample-topic-works
freeze-topics
plan-download
download-works
normalize-works
extract-institutions
enrich-institutions
build-corpus
build-edges
compute-metrics
build-region-flows
detect-communities
match-communities
validate
build-figures
build-dashboard-data
run-pipeline
report
```

Every data-producing command must support:

```text
--config PATH
--resume
--force
--dry-run
--run-id VALUE
--log-level VALUE
```

Commands that operate on time must support:

```text
--start-year
--end-year
```

Commands that operate on corpus or hierarchy must support:

```text
--corpus strict|broad|all
--hierarchy organization|umbrella|all
```

`--force` must be narrowly scoped. It must not delete unrelated outputs.

## 8. Complete backlog

### P0. Repository and execution control

#### GISNET-001: Bootstrap repository

Priority: P0  
Dependencies: none

Work:

1. Create the required directory structure.
2. Create `pyproject.toml`.
3. Add package metadata and console entry point.
4. Add Ruff, pytest, and type-check configuration.
5. Create a minimal `README.md`.
6. Create `AGENTS.md` that points to this backlog and repeats the run contract.
7. Create `.agent` files with valid initial schemas.

Acceptance:

1. `python -m gisnet.cli status` runs.
2. `pytest` runs even if only one smoke test exists.
3. No secret or generated data is tracked.
4. A second bootstrap run makes no destructive changes.

Resume behavior:

Inspect existing files and merge missing pieces. Never replace a more complete user-authored file without preserving its content.

#### GISNET-002: Implement state, lock, and run logging

Priority: P0  
Dependencies: GISNET-001

Work:

1. Implement state load, validation, migration, and atomic save.
2. Implement run locks.
3. Implement structured JSON logs plus a readable run log.
4. Implement task state transitions.
5. Implement stale-output detection from configuration and source hashes.

Acceptance:

1. Simulated interruption leaves recoverable state.
2. Two concurrent writer attempts result in one safe failure.
3. Invalid state JSON is backed up and reported, not silently discarded.
4. Task state changes are recorded with timestamps and reasons.

#### GISNET-003: Implement secret handling and environment checks

Priority: P0  
Dependencies: GISNET-001

Work:

1. Support `OPENALEX_API_KEY` and `openalex_api`.
2. Add redaction to logging and exception formatting.
3. Implement `check-env`.
4. Add tests proving the secret is absent from captured logs.

Acceptance:

1. Both environment variable names work.
2. Missing key produces a useful non-secret message.
3. Logs contain no full key and no key-bearing URL.

#### GISNET-004: Define project configuration schema

Priority: P0  
Dependencies: GISNET-001

Work:

1. Create typed configuration models.
2. Add defaults for 2010 through 2025.
3. Add strict and broad corpus views.
4. Add organization and umbrella hierarchy views.
5. Add deterministic random seed.
6. Add consortium thresholds, rolling windows, and graph thresholds.
7. Add config validation and canonical hashing.

Acceptance:

1. Invalid years, unknown views, or duplicate region rules fail early.
2. Equivalent YAML formatting produces the same semantic config hash.
3. Every output manifest records the relevant config hash.

### P1. Geographic and institutional reference data

#### GISNET-010: Build frozen geographic mapping

Priority: P0  
Dependencies: GISNET-004

Work:

1. Generate a complete ISO alpha-2 country and territory mapping.
2. Assign macro-region and subregion.
3. Apply explicit transcontinental overrides from configuration.
4. Preserve source and mapping version.
5. Add country names only as labels, never as join keys.

Acceptance:

1. Every non-null institution country code maps to exactly one macro-region and one subregion.
2. Europe, Asia, and Americas contain multiple countries.
3. The Americas include Northern America, Central America, Caribbean, and South America.
4. Asia includes Eastern, Southeastern, Southern, Central, and Western Asia.
5. Unknown codes remain `Unknown` and appear in a QA table.
6. Unit tests cover Russia, Turkey, Kazakhstan, Cyprus, Greenland, Hong Kong, Macao, Taiwan, and Kosovo or any special code present in source data.

#### GISNET-011: Profile institution types

Priority: P1  
Dependencies: GISNET-003, GISNET-004

Work:

1. Retrieve or inspect institution type values used by the current source data.
2. Create `config/institution_types.yml`.
3. Map types to primary, secondary, and excluded analytical categories.
4. Do not delete excluded records from the raw layer.

Acceptance:

1. Every observed institution type receives a mapping.
2. Unknown future types become `unknown`, not an exception.
3. The primary scope can be changed without re-downloading works.

#### GISNET-012: Implement institution override registry

Priority: P1  
Dependencies: GISNET-010

Work:

1. Create `config/institution_overrides.csv`.
2. Support actions `keep`, `collapse`, `replace`, `exclude_from_primary`, and `manual_country`.
3. Require a reason and provenance field.
4. Detect cycles in collapse or replacement relationships.
5. Preserve original IDs.

Acceptance:

1. Cyclic canonicalization fails.
2. Canonicalization is deterministic.
3. The original organization view is unchanged.
4. The umbrella view can be rebuilt after editing only the override registry.

### P2. OpenAlex access layer

#### GISNET-020: Implement API client

Priority: P0  
Dependencies: GISNET-002, GISNET-003

Work:

1. Implement authenticated GET requests.
2. Use explicit timeouts.
3. Implement retry with exponential backoff and jitter for transient failures.
4. Handle 429 and server errors.
5. Read rate-limit headers and response metadata.
6. Support `select`, `filter`, `search`, `group_by`, `per_page`, and cursor parameters.
7. Redact secrets from request diagnostics.

Acceptance:

1. Unit tests use mocked responses.
2. Retries stop after a configured maximum.
3. Authentication failures are not retried indefinitely.
4. Rate-limit exhaustion creates a resumable blocked state.
5. No request URL containing a key is persisted.

#### GISNET-021: Implement raw response cache

Priority: P0  
Dependencies: GISNET-020

Work:

1. Hash the endpoint and non-secret query parameters.
2. Store raw JSON pages compressed.
3. Store response status, retrieval time, query hash, checksum, and next cursor separately.
4. Add cache validation and corruption recovery.
5. Never cache the key.

Acceptance:

1. Repeating the same query can use a valid cache.
2. Corrupted cache entries are quarantined and re-fetched.
3. Cache keys are stable across runs.

#### GISNET-022: Implement cursor checkpointing

Priority: P0  
Dependencies: GISNET-020, GISNET-021

Work:

1. Start deep pagination with the source-supported initial cursor.
2. Persist each page before advancing the cursor.
3. Resume from the last validated cursor.
4. Stop only when the source reports no next page.
5. Detect cursor loops and repeated pages.
6. Record page counts and result counts.

Acceptance:

1. A test that fails after page 3 resumes at page 4.
2. No work is lost or duplicated after resume.
3. Repeated cursor values stop with an explicit failure record.
4. A completed query is never restarted unless forced or invalidated.

#### GISNET-023: Implement query planner and cost preview

Priority: P1  
Dependencies: GISNET-020, GISNET-022

Work:

1. Build queries by year, Topic shard, and country-code shard.
2. Split large OR filters into bounded shards.
3. Add stable query IDs.
4. Perform count-only or first-page previews.
5. Estimate request count and expected result volume.
6. Save a query plan before bulk download.
7. Ensure any work with at least one institution in Europe, Asia, or the Americas is eligible.
8. Deduplicate works returned by multiple geographic or Topic shards.

Acceptance:

1. `plan-download --dry-run` performs no bulk download.
2. Query plans are deterministic for unchanged configuration.
3. No shard exceeds configured source limits.
4. The plan covers all configured target country codes.
5. Duplicate coverage is expected and documented.

### P3. GIS Topic discovery and corpus specification

#### GISNET-030: Seed Topic discovery terms

Priority: P0  
Dependencies: GISNET-020, GISNET-004

Work:

Create a versioned discovery-term file covering:

1. GIScience
2. Geographic information systems
3. Spatial analysis
4. Geostatistics
5. Geocomputation
6. Spatial database
7. Cartography
8. Geovisualization
9. Spatial data infrastructure
10. Location-based services
11. Remote sensing
12. Earth observation
13. Photogrammetry
14. LiDAR
15. Point clouds
16. Geodesy
17. GNSS
18. Positioning
19. GeoAI
20. Geospatial artificial intelligence
21. Spatial machine learning
22. Digital twin and geospatial methods
23. Spatial environmental modelling
24. Urban spatial modelling
25. Mobility and transport spatial modelling

Acceptance:

1. Terms are grouped into strict candidates and broad candidates.
2. Each term has a rationale.
3. The file is independent of source IDs.

#### GISNET-031: Discover candidate Topics

Priority: P0  
Dependencies: GISNET-030, GISNET-020

Work:

1. Search Topics for each discovery term.
2. Retrieve Topic metadata and hierarchy.
3. Deduplicate by Topic ID.
4. Record which discovery terms found each Topic.
5. Rank candidates using transparent lexical and hierarchy evidence.
6. Do not automatically mark every candidate as included.

Acceptance:

1. Candidate registry contains real source IDs and retrieval timestamps.
2. Each candidate is traceable to one or more discovery terms.
3. No manually invented ID exists.

#### GISNET-032: Sample works for Topic review

Priority: P0  
Dependencies: GISNET-031, GISNET-020

Work:

1. Retrieve a deterministic sample of works for every candidate Topic.
2. Prefer a stratified sample across years and citation ranges.
3. Store title, abstract availability, source, year, primary Topic, all Topics, and institutions.
4. Generate a compact HTML or Markdown review report.
5. Flag Topics with insufficient sample data.

Acceptance:

1. Re-running with the same seed returns the same sample when source data is unchanged.
2. Every candidate has sample evidence or a documented retrieval failure.
3. No full-text content is required.

#### GISNET-033: Classify and freeze Topic registry

Priority: P0  
Dependencies: GISNET-032

Work:

1. Classify candidates into `strict`, `broad_only`, `excluded`, or `uncertain`.
2. Use Topic names, hierarchy, descriptions, keywords, and sampled works.
3. Write a concise decision reason for every Topic.
4. Group included Topics into method families.
5. Mark automated decisions as `provisional`.
6. If no human review occurs, continue with the provisional registry and expose that limitation in all reports.
7. Freeze a registry version and hash.

Acceptance:

1. No included Topic lacks a reason.
2. Strict is a subset of Broad.
3. Uncertain Topics are excluded from primary results and available in sensitivity mode.
4. Registry changes invalidate corpus-derived outputs.

#### GISNET-034: Validate corpus boundary

Priority: P1  
Dependencies: GISNET-033

Work:

1. Draw a deterministic validation sample.
2. Produce an annotation sheet with `relevant`, `irrelevant`, and `uncertain`.
3. Compute precision when labels exist.
4. Create a known-positive test set from established GIS venues or manually supplied works.
5. Estimate recall only when the reference set supports it.
6. Report differences between Strict and Broad.

Acceptance:

1. Validation samples and labels are versioned.
2. Precision is not reported without sufficient labels.
3. The report distinguishes measured values from assumptions.

### P4. Work acquisition and normalization

#### GISNET-040: Profile work types

Priority: P1  
Dependencies: GISNET-033, GISNET-020

Work:

1. Group or sample selected works by work type.
2. Inspect conference-like and preprint-like records.
3. Create the primary work-type policy.
4. Create preprint and expanded-type sensitivity policies.

Acceptance:

1. Work-type inclusion has a documented reason.
2. Unknown future types are retained in raw data and flagged.
3. The primary policy can be changed without changing Topic discovery.

#### GISNET-041: Build bulk query plan

Priority: P0  
Dependencies: GISNET-023, GISNET-033, GISNET-040, GISNET-010

Work:

1. Build a query plan for 2010 through 2025.
2. Include all Strict and Broad Topics.
3. Include works with at least one institution from Europe, Asia, or the Americas.
4. Use year shards and bounded Topic and country-code shards.
5. Request only required fields.
6. Save predicted counts and request volume.

Acceptance:

1. The plan is complete and deterministic.
2. Each query has a stable query ID.
3. The plan can be resumed at query and page level.
4. The current incomplete year is excluded by default.

#### GISNET-042: Download raw Works

Priority: P0  
Dependencies: GISNET-041, GISNET-022

Work:

1. Execute query plan with checkpoints.
2. Store raw pages and response metadata.
3. Stop safely on rate-limit exhaustion.
4. Resume without replaying completed pages.
5. Record source update dates where available.
6. Do not transform data in the downloader.

Acceptance:

1. Every planned query is `complete`, `blocked`, or `failed`.
2. Raw page checksums validate.
3. The downloader can be interrupted and restarted.
4. The key is absent from all stored files.

#### GISNET-043: Normalize Works

Priority: P0  
Dependencies: GISNET-042

Work:

1. Parse raw pages.
2. Deduplicate by Work ID.
3. Preserve all source query IDs.
4. Normalize Topics and basic bibliographic fields.
5. Validate publication years.
6. Quarantine malformed records.
7. Write `works.parquet` and `work_topics.parquet`.

Acceptance:

1. `work_id` is unique.
2. All included years are within configured bounds.
3. Malformed records appear in a QA dataset.
4. Output is deterministic across repeated runs.

#### GISNET-044: Build version-family and DOI diagnostics

Priority: P2  
Dependencies: GISNET-043

Work:

1. Identify duplicate DOI values.
2. Identify possible preprint and published-version families.
3. Preserve all records.
4. Create a recommended primary representative.
5. Produce sensitivity flags.

Acceptance:

1. No raw work is deleted.
2. Primary and sensitivity corpus policies can select representatives deterministically.
3. Ambiguous families are flagged rather than guessed.

### P5. Institution extraction and enrichment

#### GISNET-050: Extract distinct institutions per work

Priority: P0  
Dependencies: GISNET-043

Work:

1. Parse authorships and institution assertions.
2. Deduplicate institutions within each work.
3. Preserve raw affiliation strings where available.
4. Record country and type from source records.
5. Create an unresolved-affiliation QA table.
6. Do not form edges yet.

Acceptance:

1. Multiple authors from the same institution produce one work-institution row.
2. Works with no resolved institution remain in a QA table.
3. Counts reconcile with sampled raw records.

#### GISNET-051: Build institution master table

Priority: P0  
Dependencies: GISNET-050

Work:

1. Deduplicate OpenAlex institution records.
2. Retrieve missing institution metadata by stable ID when needed.
3. Store ROR IDs, country, type, lineage, coordinates if available, and source dates.
4. Cache singleton lookups.
5. Preserve missing values.

Acceptance:

1. `institution_id` is unique.
2. Every work-institution row joins to an institution or appears in an explicit unresolved table.
3. No name-based join is used when a stable ID exists.

#### GISNET-052: Enrich institutions with ROR

Priority: P1  
Dependencies: GISNET-051

Work:

1. Use ROR IDs already supplied by source data.
2. Support ROR singleton retrieval with caching.
3. Support an optional local ROR dump.
4. Record ROR schema and data version.
5. Extract names, types, locations, and relationships.
6. Never overwrite raw OpenAlex fields; add source-specific fields and resolved fields.

Acceptance:

1. Enrichment is optional and resumable.
2. Missing ROR IDs do not block the pipeline.
3. Conflicts between OpenAlex and ROR country or type are recorded.
4. ROR API and dump modes yield the same normalized schema.

#### GISNET-053: Apply geographic mapping

Priority: P0  
Dependencies: GISNET-010, GISNET-051

Work:

1. Map institution country codes to macro-region and subregion.
2. Apply manual country overrides only through configuration.
3. Produce unknown and conflict reports.
4. Keep mixed-region works intact.

Acceptance:

1. Every known country code receives a region.
2. Asia and Americas are represented as full macro-regions.
3. No logic checks specifically for only China or the United States.

#### GISNET-054: Build organization and umbrella views

Priority: P0  
Dependencies: GISNET-012, GISNET-052, GISNET-053

Work:

1. Keep the original organization view.
2. Resolve umbrella canonical IDs using lineages, ROR relationships, and explicit overrides.
3. Avoid automatically collapsing large federated systems without a rule.
4. Detect self-edges created by collapsing and remove them only from edge construction.
5. Produce a canonicalization audit table.

Acceptance:

1. Original and canonical IDs are both retained.
2. Organization and umbrella views can be compared.
3. No canonicalization cycle exists.
4. Every collapse has a rule ID and reason.

### P6. Corpus tables and graph construction

#### GISNET-060: Build Strict and Broad work sets

Priority: P0  
Dependencies: GISNET-033, GISNET-043, GISNET-044

Work:

1. Apply Topic registry and work-type policy.
2. Build strict, broad, and sensitivity flags.
3. Keep the Broad set as a superset of Strict.
4. Add exclusion reasons.
5. Produce annual and Topic-family counts.

Acceptance:

1. Strict is a subset of Broad.
2. Every excluded work has at least one machine-readable exclusion reason.
3. Annual counts reconcile with source tables.

#### GISNET-061: Build normalized work-institution tables

Priority: P0  
Dependencies: GISNET-050, GISNET-054, GISNET-060

Work:

1. Join corpus membership, institution master data, region mapping, and hierarchy views.
2. Deduplicate within work and hierarchy view.
3. Add primary research-scope flags.
4. Write partitioned Parquet by year or corpus where useful.

Acceptance:

1. Primary key constraints pass.
2. Organization and umbrella rows are independently valid.
3. A single-institution work remains available for output metrics.

#### GISNET-062: Build annual collaboration edges

Priority: P0  
Dependencies: GISNET-061

Work:

1. For each work, create unordered institution pairs.
2. Calculate full and fractional pair weights.
3. Store source and target in stable order.
4. Record consortium-size diagnostics.
5. Build organization and umbrella views.
6. Build Strict and Broad views.
7. Exclude self-pairs after canonicalization.
8. Aggregate by year.

Acceptance:

1. A synthetic three-institution work creates three full edges of weight 1.
2. The same work creates three fractional edges of weight 1/3.
3. Fractional weights sum to 1 per work.
4. Duplicate authors from one institution do not alter edge weights.
5. Repeated runs produce identical edges.

#### GISNET-063: Build institutional output tables

Priority: P0  
Dependencies: GISNET-061

Work:

1. Calculate full work count.
2. Calculate fractional work count.
3. Calculate international and cross-region work shares.
4. Preserve zero-edge institutions with valid output.

Acceptance:

1. Work counts reconcile with work-institution rows.
2. Fractional contributions sum to 1 per work within each hierarchy view.
3. Institutions with no collaboration edges remain in node outputs.

#### GISNET-064: Compute normalized intensity and persistence

Priority: P1  
Dependencies: GISNET-062, GISNET-063

Work:

1. Join institutional output denominators.
2. Compute normalized collaboration intensity.
3. Compute trailing 3-year and 5-year persistence.
4. Add explicit incomplete-window flags.
5. Compute visualization score.

Acceptance:

1. No divide-by-zero error exists.
2. Persistence values are between 0 and 1.
3. Visualization score is labelled as non-primary.
4. Early-year window handling is explicit.

#### GISNET-065: Build region and country flows

Priority: P0  
Dependencies: GISNET-062, GISNET-053

Work:

1. Aggregate institution edges to macro-region pairs.
2. Aggregate to subregion pairs.
3. Aggregate to country pairs.
4. Produce both counts and shares.
5. Preserve target-to-Other flows.
6. Use deterministic unordered pair ordering.

Acceptance:

1. Europe-to-Asia equals Asia-to-Europe in the undirected representation.
2. Asia includes more than China.
3. Americas include more than the United States.
4. Macro-region totals reconcile with institution-edge totals under the same scope.

### P7. Network analysis

#### GISNET-070: Build annual graph objects

Priority: P1  
Dependencies: GISNET-062, GISNET-063

Work:

1. Build weighted undirected graphs.
2. Attach node and edge attributes.
3. Support minimum weight and primary-institution filters without changing stored data.
4. Serialize lightweight graph exports.

Acceptance:

1. Graph node and edge counts match Parquet tables.
2. Isolated output-producing institutions can be retained in node-level analysis.
3. Filters are reproducible from configuration.

#### GISNET-071: Compute node and graph metrics

Priority: P1  
Dependencies: GISNET-070

Work:

1. Compute required node metrics.
2. Compute required graph metrics.
3. Use deterministic approximate betweenness when exact computation is too expensive.
4. Store method metadata.
5. Validate metric ranges and null behavior.

Acceptance:

1. Metrics pass checks on known synthetic graphs.
2. Large-graph fallback is documented.
3. Metric outputs join uniquely by year, corpus, hierarchy, and institution.

#### GISNET-072: Detect annual communities

Priority: P1  
Dependencies: GISNET-070

Work:

1. Run Leiden on the weighted graph.
2. Use deterministic seeds where supported.
3. Store modularity and resolution parameter.
4. Run sensitivity at more than one resolution.
5. Flag graphs too small for meaningful community detection.

Acceptance:

1. Every non-isolated node has one annual community ID.
2. Community results are reproducible within supported algorithm constraints.
3. Resolution choices are reported.

#### GISNET-073: Match communities across years

Priority: P2  
Dependencies: GISNET-072

Work:

1. Calculate Jaccard overlap between adjacent-year communities.
2. Use a documented assignment algorithm.
3. Assign continuity IDs.
4. Detect split, merge, birth, and disappearance events.
5. Do not assume annual numeric community labels are stable.

Acceptance:

1. Synthetic split and merge cases pass tests.
2. Continuity IDs are stable under unchanged data.
3. Low-overlap matches are flagged uncertain.

#### GISNET-074: Build fixed network layout

Priority: P1  
Dependencies: GISNET-070

Work:

1. Construct an aggregate core graph over the full period.
2. Select a reproducible layout algorithm.
3. Compute coordinates once with a fixed seed.
4. Reuse coordinates across annual views.
5. Store layout version and threshold.

Acceptance:

1. Nodes do not jump because of independent yearly layouts.
2. New or low-frequency nodes receive deterministic fallback positions.
3. Layout changes invalidate only visualization outputs.

### P8. Validation and sensitivity

#### GISNET-080: Validate edge arithmetic

Priority: P0  
Dependencies: GISNET-062

Work:

1. Add synthetic tests for two, three, and many institutions.
2. Test duplicate authors.
3. Test umbrella collapse.
4. Test consortium thresholds.
5. Reconcile per-work fractional totals.

Acceptance:

All arithmetic invariants pass.

#### GISNET-081: Audit top institutions and edges

Priority: P1  
Dependencies: GISNET-064, GISNET-071

Work:

1. Sample top institutions by output and centrality.
2. Sample top cross-region edges.
3. Display underlying works, affiliation strings, and canonicalization decisions.
4. Flag suspicious name, country, type, or hierarchy records.
5. Route corrections through override registries.

Acceptance:

1. Audit report is reproducible.
2. Corrections never modify raw data.
3. Each correction has a reason and provenance.

#### GISNET-082: Run required sensitivity matrix

Priority: P1  
Dependencies: GISNET-064, GISNET-071

Required comparisons:

1. Strict versus Broad
2. Full versus fractional counting
3. Organization versus umbrella
4. Annual versus 3-year rolling network
5. Include versus exclude large consortium papers
6. Primary institution types versus expanded types
7. Published-only versus published plus preprints
8. Provisional Topic registry versus reviewed registry when available

Acceptance:

1. Every comparison has a machine-readable result table.
2. Major rank or trend changes are highlighted.
3. No sensitivity result overwrites the primary result.

#### GISNET-083: Reproducibility and interruption tests

Priority: P0  
Dependencies: GISNET-022, GISNET-043, GISNET-062

Work:

1. Run the same pipeline twice from unchanged inputs.
2. Compare dataset checksums or normalized logical hashes.
3. Simulate interruption during pagination.
4. Simulate interruption during Parquet write.
5. Simulate corrupted cache and state files.
6. Confirm clean recovery.

Acceptance:

1. Logical outputs match.
2. Interrupted runs resume without duplication.
3. Corruption is surfaced and quarantined.

### P9. Visualization

#### GISNET-090: Build annual trend figures

Priority: P1  
Dependencies: GISNET-065, GISNET-071

Figures:

1. Annual Europe-to-Asia collaboration
2. Annual Europe-to-Americas collaboration
3. Annual Asia-to-Americas collaboration
4. Intra-region collaboration for all three regions
5. Cross-region share over time
6. Strict versus Broad comparison
7. Organization versus umbrella comparison

Acceptance:

1. Axes, units, corpus view, counting method, and year range are explicit.
2. Partial years are absent or visually labelled.
3. Static exports are publication-ready SVG or high-resolution PNG.

#### GISNET-091: Build region collaboration matrix

Priority: P1  
Dependencies: GISNET-065

Work:

1. Generate annual region-pair matrices.
2. Add country and subregion drilldown tables.
3. Support counts, fractional weights, and normalized shares.
4. Keep ordering stable across years.

Acceptance:

1. Matrix totals reconcile with region-flow data.
2. Cells have tooltips or companion tables with exact values.
3. Sparse or missing values are distinguished from zero.

#### GISNET-092: Build geographic collaboration map

Priority: P1  
Dependencies: GISNET-051, GISNET-064

Work:

1. Plot institutions using sourced coordinates.
2. Plot top or thresholded collaboration edges.
3. Add year, corpus, hierarchy, region-pair, institution-type, and Topic-family filters.
4. Avoid rendering all edges by default.
5. Provide map coverage and missing-coordinate counts.
6. Do not invent coordinates.

Acceptance:

1. Institutions without coordinates are reported.
2. Map filters do not alter source data.
3. Default view remains legible.
4. Edge threshold is visible.

#### GISNET-093: Build fixed-layout network visualization

Priority: P1  
Dependencies: GISNET-074, GISNET-071, GISNET-072

Work:

1. Use fixed aggregate coordinates.
2. Encode node size by a selectable metric.
3. Encode node category by macro-region or community.
4. Filter by year and minimum edge weight.
5. Show institution and edge details.
6. Provide a textual summary for accessibility.

Acceptance:

1. Coordinates are stable across years.
2. Legends state all encodings.
3. The graph remains usable after filtering.

#### GISNET-094: Build institution-pair explorer

Priority: P2  
Dependencies: GISNET-064

Work:

1. Search two institutions.
2. Show annual full count, fractional count, intensity, and persistence.
3. Show dominant Topic families.
4. Show supporting work samples.
5. Show organization and umbrella identities.

Acceptance:

1. Search uses stable IDs internally.
2. Similar institution names do not silently resolve to the wrong record.
3. Missing years display zero or missing according to data semantics.

#### GISNET-095: Build Streamlit dashboard

Priority: P1  
Dependencies: GISNET-090, GISNET-091, GISNET-092, GISNET-093

Required pages:

1. Overview
2. Region trends
3. Geographic map
4. Institutional network
5. Institution explorer
6. Topic-family comparison
7. Methods and limitations
8. Data quality

Required global filters:

```text
Year
Corpus view
Hierarchy view
Counting method
Macro-region pair
Country
Subregion
Institution type
Topic family
Consortium policy
```

Acceptance:

1. Dashboard reads processed data only.
2. It makes no OpenAlex requests during ordinary viewing.
3. It starts with one documented command.
4. Empty filter combinations show a clear message.
5. Data and methods versions are visible.

### P10. Reporting and release

#### GISNET-100: Generate methodology report

Priority: P1  
Dependencies: GISNET-082, GISNET-095

Required sections:

1. Research questions
2. Geographic scope
3. GIS corpus definitions
4. Data sources
5. Institution resolution
6. Counting methods
7. Dynamic network metrics
8. Validation
9. Sensitivity analysis
10. Limitations
11. Reproducibility
12. Data ethics and geographic naming convention

Acceptance:

1. Every reported figure is generated from processed data.
2. Provisional Topic decisions are disclosed.
3. Partial-year data policy is stated.
4. Composite score is not treated as a primary scientific metric.

#### GISNET-101: Generate data dictionary and provenance report

Priority: P1  
Dependencies: all processed datasets

Work:

1. Document every column.
2. Record source and transformation lineage.
3. Record configuration and code hashes.
4. Document null semantics.
5. Document primary keys.
6. Document known data-quality issues.

Acceptance:

1. Every released table has a dictionary.
2. Every output traces to source manifests.
3. No key or private local path appears.

#### GISNET-102: Create end-to-end pipeline command

Priority: P0  
Dependencies: all required pipeline stages through GISNET-095

Work:

Implement:

```bash
python -m gisnet.cli run-pipeline \
  --start-year 2010 \
  --end-year 2025 \
  --corpus all \
  --hierarchy all \
  --resume
```

The orchestrator must:

1. Skip valid completed stages.
2. Resume incomplete downloads.
3. Invalidate stale derived outputs.
4. Stop safely on an unrecoverable stage.
5. Print the next recovery command.
6. Never delete valid raw data automatically.

Acceptance:

1. A fresh run builds the complete project.
2. An interrupted run continues from checkpoints.
3. A repeated completed run performs validation and exits without unnecessary work.
4. A config change rebuilds only affected stages.

#### GISNET-103: Add CI and local quality gate

Priority: P1  
Dependencies: GISNET-083

Required checks:

```bash
ruff check .
ruff format --check .
pytest
python -m gisnet.cli status
```

Add type checking when the codebase is ready.

Acceptance:

1. CI uses synthetic fixtures and does not require the real API key.
2. Integration tests requiring network are marked and skipped by default.
3. Secrets are never exposed in CI logs.

#### GISNET-104: Produce final release bundle

Priority: P2  
Dependencies: GISNET-100, GISNET-101, GISNET-102, GISNET-103

Release contents:

1. Source code
2. Configuration files
3. Topic registry
4. Region mapping
5. Institution override registry
6. Processed aggregate tables
7. Static figures
8. Dashboard instructions
9. Methodology report
10. Data dictionary
11. Provenance manifests
12. Known limitations
13. Reproduction commands

Acceptance:

1. Release contains no secret.
2. Release does not require raw API responses unless explicitly included.
3. All public outputs have checksums.
4. A clean environment can reproduce the processed outputs when supplied with the API key and sufficient API allowance.

### P11. Optional research extensions

Do not start these tasks before primary release tasks are complete.

#### GISNET-110: Directed institution citation-flow network

Build directed annual edges from citing institution to cited institution. Treat this as knowledge flow, not collaboration.

#### GISNET-111: Topic-similarity network

Build institutional Topic vectors and cosine similarity. Treat this as research proximity, not collaboration.

#### GISNET-112: Multiplex comparison

Compare co-authorship, citation flow, and Topic similarity as separate layers. Never merge them without explicit layer weights and sensitivity analysis.

#### GISNET-113: Author mobility layer

Estimate researcher movement between institutions using author affiliation histories. Add strong uncertainty and identity-disambiguation warnings.

#### GISNET-114: Forecasting

Forecast region-pair trends only after structural breaks, missingness, and partial-year issues have been evaluated. Label all forecasts as model outputs.

## 9. Backlog scheduling rules

The agent must select work using this order:

1. P0 task with all dependencies complete
2. P1 task with all dependencies complete
3. P2 task with all dependencies complete
4. Optional task only after release readiness

Within equal priority:

1. State and recovery infrastructure
2. Research-definition infrastructure
3. Raw-data acquisition
4. Normalization
5. Graph construction
6. Validation
7. Visualization
8. Reporting

Never start a downstream visualization task using placeholder measured values. Synthetic data is allowed only in tests and clearly labelled UI prototypes.

## 10. Run budget behavior

A run may end because of context, time, API allowance, network failure, or tool interruption. Before ending:

1. Finish the current atomic write or discard its temporary output.
2. Persist the last safe checkpoint.
3. Mark the task `IN_PROGRESS` or `BLOCKED`.
4. Record the exact next command.
5. Record the first uncompleted substep.
6. Do not mark a task `DONE` based only on code creation. Its acceptance checks must pass.

If API allowance is exhausted:

1. Preserve all downloaded pages.
2. Record remaining query IDs.
3. Mark the download task `BLOCKED` with reason `rate_limit`.
4. Continue offline normalization or tests when possible.
5. Do not repeatedly retry until the next run.

## 11. Required run-log template

Append this template to `.agent/RUNLOG.md`:

```text
## Run <run_id>

Started UTC:
Ended UTC:
Task:
Initial git status:
Final git status:

### Objective

### Work completed

### Files changed

### Commands executed

### Validation results

### Data and configuration hashes

### Checkpoints written

### Failures or blockers

### Decisions made

### Exact next action
```

## 12. Definition of done

The primary project is complete only when all statements below are true:

1. The analysis covers complete years 2010 through 2025.
2. Europe, Asia, and the Americas are treated as macro-regions.
3. Asia is not reduced to China.
4. The Americas are not reduced to the United States.
5. Country and subregion drilldowns are available.
6. Universities and non-university research institutions are represented.
7. Strict GIS and Broad Geospatial corpora are independently reproducible.
8. Organization and umbrella institution views are both available.
9. Full and fractional collaboration weights are available.
10. Annual, rolling, normalized, and persistence metrics are available.
11. Interrupted downloads and transformations resume safely.
12. Repeated runs do not duplicate data.
13. Top institutions and edges have audit evidence.
14. Required sensitivity analyses are complete.
15. Geographic maps use sourced coordinates.
16. Annual network layouts are temporally comparable.
17. The dashboard reads processed data without live API dependency.
18. Tests and quality gates pass.
19. Data dictionaries and manifests exist.
20. No API key appears in tracked files or outputs.
21. A final report states limitations and provisional decisions.
22. A fresh environment can reproduce the project using documented commands.

## 13. First-run instruction to the coding agent

Execute the following sequence without asking for confirmation unless an irreversible conflict with existing user files is discovered:

```text
1. Inspect the repository and preserve existing work.
2. Implement GISNET-001 through GISNET-004.
3. Implement GISNET-010 and GISNET-020 through GISNET-022.
4. Run all available tests.
5. Update the persistent backlog and state files.
6. Commit the completed atomic work locally.
7. End with the exact next task and command.
```

If an existing repository already contains equivalent infrastructure, validate it against the acceptance criteria and mark tasks complete only when evidence exists.
