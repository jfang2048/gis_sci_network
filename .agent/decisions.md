# Decisions

## 2026-08-05 — Repository root

The supplied working directory is the repository root; the architecture's
`gis-collaboration/` label is descriptive rather than a required nested directory.
`AI_EXECUTION_BACKLOG.md` is a symlink to the supplied authoritative backlog filename.

## 2026-08-05 — Geographic convention

The frozen registry uses the English UN M49 geographic table retrieved on 2026-08-05,
then converts the Americas' intermediate regions to the required subregions and groups
African intermediate regions under Sub-Saharan Africa. RU, TR, KZ, and CY are explicit
frozen conventions. TW is added to complete current ISO alpha-2 coverage, XK is an
explicit user-assigned analytical code, and ZZ represents unknown input. These are
technical analytical conventions, not political claims.

## 2026-08-05 — Secret transport and storage

OpenAlex credentials are resolved from `OPENALEX_API_KEY` before `openalex_api` and are
sent only with live HTTPS requests. Cache identities, metadata, diagnostics, exceptions,
manifests, and logs remove or redact credential parameters.

## 2026-08-05 — Raw page durability

Raw response bodies use deterministic gzip encoding. Non-secret request metadata and
checksums are stored separately. A cursor advances only after the compressed page and
metadata both validate and the checkpoint is atomically replaced.

## 2026-08-05 — Provisional Topic boundary

OpenAlex Topic searches returned 40 unique candidates from the 25 required source-ID-free
terms. Six terms returned no direct source match; those zero-result searches remain in the
candidate audit rather than being replaced with invented IDs. Each candidate received six
deterministic work samples across three year and two citation strata. The resulting registry
contains 6 Strict Topics and 23 Broad Topics (including Strict), with 7 uncertain Topics held
outside primary results. All decisions remain provisional until human review.


## 2026-08-05 — Institution and work-type policies

All nine institution types observed in the live OpenAlex profile have explicit configurable
mappings. Education, government, nonprofit, and facility are primary; archive, company,
healthcare, and other are retained only for secondary views; funder is excluded from institutional
nodes. The override registry remains empty rather than inventing entity corrections, while its
validated schema supports keep, collapse, replace, primary exclusion, and manual country actions.

All 25 work types observed in both Topic corpora are explicitly mapped. Articles, conference
papers, reviews, data papers, and software papers form the primary view. Preprints are a distinct
sensitivity view, and other scholarly long-form or grey-literature types are available only in the
expanded view. Unknown future types remain raw and flagged rather than silently included.

## 2026-08-05 — Boundary evidence and bulk query plan

The corpus-boundary sheet contains 36 deterministic rows across Strict, Broad-only, and
excluded/uncertain controls. Precision is withheld because none has a human relevance label.
Recovery of 10/10 manually reviewed known-positive sampled works is reported only as small
reference-set recall, never as population recall.

The bulk plan queries the 23-topic Broad superset so one acquisition can derive both Strict and
Broad views. It contains 336 deterministic year/Topic/country shards covering 160 codes in Europe,
Asia, and the Americas for complete years 2010–2025. Count previews estimate 1,561,250 returned
records including shard duplicates, 7,975 bulk page requests, and USD 7.975 under the configured
per-page planning assumption. Duplicate coverage is intentional; normalization must deduplicate by
OpenAlex Work ID and retain all source query IDs. These are preview estimates, not observed corpus
counts or a spending authorization.


## 2026-08-05 — Raw Works acquisition

The saved plan was executed without transformation into 7,978 validated compressed raw pages.
All 336 query checkpoints completed and yielded 1,561,250 records including expected cross-shard
duplicates, exactly matching the count-preview result volume. The three-page difference from the
preview estimate occurs when cursor pagination requires a terminal empty page after a full
200-record page. Raw response cache and page checkpoints remain local ignored runtime state;
the tracked status artifact and manifest preserve aggregate provenance but do not claim the raw
cache can be reconstructed without re-downloading it.

## 2026-08-05 — Bounded-memory Work normalization

DuckDB normalization is explicitly capped at 6 GB with one worker thread and insertion-order
preservation disabled; all exported datasets retain explicit deterministic `ORDER BY` clauses.
This replaces DuckDB's host-relative default, which reserved 80% of the 30 GiB machine and caused
a kernel OOM kill. Parquet checksums are streamed. Work-Topic staging rejects orphan rows before
export, and referenced-work parsing uses a distinct identifier so it cannot overwrite `work_id`.
Large raw, interim, and processed data remain ignored and are rebuilt locally from OpenAlex.
