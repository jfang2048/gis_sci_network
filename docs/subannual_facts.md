# Subannual school-decision facts

Policy version: `subannual-school-facts-2026-08-17-v1`

Build and validate the local processed datasets with:

```bash
uv run python -m gisnet.cli build-subannual-facts --resume
```

The command reads local processed data only. It does not call OpenAlex.

## Scientific boundary

The temporal key is bibliographic publication time. It is not collaboration start, research
start, project start, or author-mobility time. Only records marked
`subannual_date_eligible = true` by the publication-date QA layer enter these facts. No missing
month or day is completed with an invented value.

The school-decision entity scope is every `is_primary_research_scope` institution in the selected
Strict/Broad corpus and organization/umbrella identity view. It includes Europe, Asia, the
Americas, Africa, Oceania, and unknown geography. This is intentionally broader than the released
annual network, whose historical `is_primary_network_scope` is limited to Europe, Asia, and the
Americas. The released annual files and their meanings remain unchanged.

## Positive fact tables

The four fact tables are sparse: an absent row means no exact-date-eligible positive observation
for that entity and period, not an imputed scientific zero.

| Dataset | Primary key | Current rows |
| --- | --- | ---: |
| `institution_outputs_month.parquet` | `publication_month, corpus_view, hierarchy_view, institution_id` | 1,745,888 |
| `institution_outputs_quarter.parquet` | `publication_quarter, corpus_view, hierarchy_view, institution_id` | 1,117,588 |
| `collaboration_edges_month.parquet` | `publication_month, corpus_view, hierarchy_view, source_id, target_id` | 4,407,772 |
| `collaboration_edges_quarter.parquet` | `publication_quarter, corpus_view, hierarchy_view, source_id, target_id` | 4,066,652 |

Institution tables carry the same additive counts and recomputed shares as the annual institution
facts: full Work count, fractional Work count, collaborative/single-institution Work counts,
international/cross-region Work counts, and their shares. Endpoint metadata is retained for
predicate reads; stable IDs, not names, are keys.

For each Work, let `k` be the number of distinct eligible stable institution IDs in the selected
identity view:

```text
institution fractional contribution = 1 / k
full contribution to each unordered pair = 1
fractional contribution to each unordered pair = 2 / (k * (k - 1))
```

The pair fractions for every collaborative Work sum to one. Pairs are canonical and undirected:
`source_id < target_id`. A hierarchy collapse that leaves one ID produces an institution output
but no self-edge. Quarterly shares are recomputed from quarterly numerators and denominators; they
are not averages of monthly shares.

## Reconciliation

`subannual_reconciliation.parquet` stores annual-source comparisons for institution/edge and
month/quarter facts by year, corpus, and hierarchy. Every row stores expected and actual full and
fractional totals, their differences, and `reconciliation_passed`.

The current 256 reconciliation rows all pass. Maximum absolute full-count difference is zero.
The largest fractional difference is `1.0391e-08`, below the declared `1e-07` aggregate floating
summation tolerance; per-Work pair fractions are separately required to be within `1e-10` of one.
Monthly additive metrics also reconcile to quarterly metrics by entity and quarter.

## Sparsity and zero denominators

`subannual_sparsity.parquet` avoids a large entity-by-calendar materialization. For each diagnostic
stratum:

```text
possible_period_count = annual_entity_count * eligible_period_count
zero_period_count = possible_period_count - active_period_count
zero_rate = zero_period_count / possible_period_count
```

The entity universe includes exact-dated entities observed inside the requested month bounds and
entities whose annual-only publication year overlaps those bounds, so missing dates do not make an
entity disappear. An exact-dated entity observed only outside a custom partial boundary is not
misclassified as an in-window zero; a genuinely annual-only record in a boundary year is retained
conservatively because no evidence can place it within that year. `date_eligible_entity_count` is
stored separately. A second span-adjusted denominator covers only inclusive first-to-last active
periods. Every percentage has recoverable numerator and denominator fields.

Diagnostics are stratified by Strict/Broad corpus, hierarchy, macro-region (unordered
macro-region pair for edges), and transparent institution activity bands based on supported-period
full Work count: `1_to_4_works`, `5_to_19_works`, `20_to_99_works`, and `100_plus_works`.

Current organization-view results over 2010-01 through 2025-12 (192 months; 64 quarters):

| Entity | Corpus | Month zero rate | Quarter zero rate | Median active months/entity | Median Works/active month |
| --- | --- | ---: | ---: | ---: | ---: |
| Institution | Broad | 87.8646% | 77.6962% | 6 | 1 |
| Institution | Strict | 93.5402% | 86.0095% | 3 | 1 |
| Edge | Broad | 98.7797% | 96.6459% | 1 | 1 |
| Edge | Strict | 99.1104% | 97.4385% | 1 | 1 |

For institutions with 100 or more supported-period Works, Broad/Strict monthly zero rates fall to
36.20%/42.54%. Raw monthly facts are therefore useful inputs for rolling windows and exact
high-activity inspection, but a raw-month ranking would be dominated by zeros and single-Work
cells. The later decision UI should default to rolling 12- or 24-month views; quarterly facts are a
less sparse raw-period alternative.

## Physical representation and measured performance

The completed files total about 155 MB, so the initial representation is one Zstandard-compressed
Parquet per fact table rather than a partition tree. This avoids tiny partitions while retaining
DuckDB/Polars predicate pushdown. On the acceptance machine, the final repeated full build completed
in 98.48 seconds with maximum RSS 4,277,784 KiB. Warm median DuckDB reads over six post-warmup runs
were:

| Query | Median |
| --- | ---: |
| One institution's 192 monthly rows | 16.881 ms |
| One Broad organization month, top 100 | 4.350 ms |
| One institution's 24-month ego edges, top 50 | 6.130 ms |
| One Broad organization quarter, top 100 | 3.986 ms |

These results do not justify partitioning the present files. GISNET-123 builds rolling facts
from these sparse positives rather than an all-school × all-partner × all-month Cartesian product.
The completed exact-calendar representation is documented in
[`rolling_facts.md`](rolling_facts.md).

## Reproducibility and provenance

All six Parquet outputs are validated before grouped promotion. If any promotion fails, every
prior output is restored. A repeated full-data build reproduced all six SHA-256 hashes. Dataset
manifests are in `.agent/manifests/`; counts, hashes, policies, and current overall sparsity rows
are in `data/reference/subannual_temporal_summary.json`.
