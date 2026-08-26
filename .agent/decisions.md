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

## 2026-08-17 — School-decision analytical contract

The institution becomes the primary interactive entity for the school-decision layer while the
existing complete-year annual regional analysis remains intact. `School` is interface shorthand
for an eligible university or research institution; it neither asserts degree-program availability
nor changes the organization identity. The complete eligible search universe uses primary research
scope across all stored macro-regions and never depends on global node/edge ranks, visualization
score, map thresholds, or coordinate presence.

Activity, specialization, collaboration reach, persistence, network position, citation influence,
research proximity, momentum, and user-defined fit are independent dimensions. Co-authorship,
directed citation flow, and Topic similarity remain separate layers and are never merged into one
scientific edge weight. A global university-quality score is prohibited. The only permitted
combined score name is `user_defined_fit_score`; it is transparent, UI-only, and not persisted in
scientific datasets.

Current code behavior is authoritative where legacy wording conflicts: normalized collaboration
intensity uses full Work-count denominators, international and cross-region shares divide by all
included institutional Works, and bridge score is cross-region fractional strength divided by
total fractional strength. The methodology and data-dictionary generators were corrected during
GISNET-120; GISNET-139 must retain those definitions. The GIS Topic registry remains provisional
and its incomplete human review must be disclosed on all school-oriented results.

## 2026-08-17 — Publish the school-decision contract as a prerelease snapshot

- Keep the stable `v0.1.0` tag immutable because it identifies the original complete-year annual
  scientific release.
- Publish `school-decision-contract-v1` as a GitHub prerelease at commit `c2d4be9`; this exposes
  the validated analytical contract and post-0.1.0 research layers without claiming the planned
  School Finder, rolling-window datasets, profiles, comparison UI, or ego maps are complete.
- Ship the checksum-complete public manifest and a deterministic figure bundle containing SVG
  sources and PNG renditions. Preserve citation flow, Topic similarity, and co-authorship as
  separate evidence layers in both the release text and architecture image.

## 2026-08-17 — Publication-date QA and source precision

- Preserve `publication_date` as bibliographic observation-time metadata and never interpret it as
  collaboration, research, project, or author-mobility start time.
- A source literal is subannual-eligible only when it is a full calendar-valid date, agrees with
  `publication_year`, and lies within the configured supported range. Null, malformed or partial,
  year-conflicting, and out-of-range values remain annual-only; no month or day is imputed.
- The current normalized snapshot contains 1,176,947 exact-valid source dates, but the source has
  no independent precision flag and 261,950 values fall on January 1. Retain and measure these
  values rather than inventing a January-1 exclusion heuristic.
- Preserve the released exact-DOI representative policy for primary Strict/Broad facts. Do not
  merge version-family dates or choose a new family date. Relative to all-version sensitivity, the
  policy removes 129 Strict and 360 Broad exact-date-eligible records across 71 and 119 months.

## 2026-08-17 — Subannual school-decision facts and sparsity

- Use `is_primary_research_scope` across every stored geography for school-decision month/quarter
  facts. This intentionally includes Africa, Oceania, and unknown geography and does not alter the
  legacy annual `is_primary_network_scope` files.
- Preserve stable-ID Work arithmetic: each institution receives `1/k`; each unordered pair receives
  full weight 1 and fractional weight `2 / (k * (k - 1))`. Publication month changes grouping only.
- Store sparse positive Parquet facts and derive zero cells from recoverable entity and period
  denominators. Diagnostic activity bands are transparent full-Work ranges 1–4, 5–19, 20–99, and
  100+; they are descriptive strata, not quality tiers.
- For custom month bounds, exact-dated entities enter the sparsity universe only inside the bounds.
  Annual-only entities remain when their publication year overlaps because no evidence can locate
  them within that year.
- Current raw month networks are highly sparse (Broad/Strict edge-month zero rates 98.78%/99.11%);
  retain raw facts for exact analysis and rolling inputs, but prefer rolling 12/24-month decision
  defaults rather than a raw-month ranking.
- Keep single Zstandard Parquet files for GISNET-122: measured predicate reads are 4–17 ms and the
  six outputs total about 155 MB, so partitioning is not justified.

## 2026-08-26 — Exact rolling-window physical representation

- Materialize positive institution-window facts, but store rolling collaboration edges as maximal
  inclusive positive window-end intervals. This is a lossless index over 187,718,512 positive edge
  endpoints, not an approximation; `query_rolling_edges` returns exact `window_start`, `window_end`,
  `window_months`, weights, active months, persistence, and coverage after verifying the accepted
  monthly-edge checksum.
- Keep the explicit coverage ledger independent of positive rows so zero-activity and early
  incomplete months remain observable. Publication dates are bibliographic observation time, never
  collaboration, research, project, or mobility start time.
- Do not store rolling normalized intensity inside the interval index. GISNET-128 must join queried
  fractional edge counts to both endpoint full-Work denominators in
  `institution_outputs_rolling.parquet` and apply the existing declared formula, avoiding duplicated
  denominators or stale derived values.
- Preserve complete-year annual outputs as the long-term historical reference. Organization and
  umbrella rolling views remain identity views; GISNET-125 must rebuild any evidence-backed school
  view from Work memberships rather than summing organization rows.
