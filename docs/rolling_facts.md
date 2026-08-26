# Exact-calendar rolling facts

GISNET-123 adds a parallel rolling layer without changing the released annual products. All time
keys describe **bibliographic publication-time observations**. They do not describe collaboration,
research, project, or author-mobility start dates.

Build and validate the local processed datasets with:

```bash
uv run python -m gisnet.cli build-rolling-facts --resume
```

The command reads accepted local monthly facts and makes no OpenAlex request. Current counts,
checksums, policies, and output paths are recorded in
[`rolling_temporal_summary.json`](../data/reference/rolling_temporal_summary.json).

## Calendar and coverage contract

Windows contain 12, 24, or 36 inclusive calendar months and advance one calendar month at a time.
For example, a 12-month window ending `2025-12` starts `2025-01`; one ending `2026-07` starts
`2025-08`. Every rolling fact carries:

- `window_start`, `window_end`, and `window_months`;
- `observed_month_count` from the intersection of the nominal window with the declared dataset
  coverage, including observed zero-activity months;
- `eligible_month_count`, retained separately for the later incremental-ingestion policy;
- `coverage_ratio = eligible_month_count / window_months`; and
- `is_complete_window`, true only when both observed and eligible counts equal the nominal length.

Completeness is never inferred from positive fact rows. The coverage ledger therefore retains early
incomplete windows even when a school or edge has no positive row.
The CLI declares the historical bounds from `config/project.yml`; direct callers may provide an
explicit declared range. When no range is supplied, the library uses the complete supported-year
domain of the publication-date fact layer, not the minimum and maximum positive institution rows.
The ledger records this provenance together with the checksum of the monthly edge source, and edge
queries reject a stale or changed source rather than mixing generations.

Annual-only Works are not assigned to invented months. `exact_date_work_count` is observed inside the
exact window. `annual_only_work_count` is the count of annual-only candidates in overlapping calendar
years. `date_coverage_ratio` is reported only when every affected year is wholly contained in the
window; otherwise it is null and `date_coverage_status` is `indeterminate_boundary_year`.

## Institution metrics

`institution_outputs_rolling.parquet` is sparse: it stores only positive institution-window facts.
It sums the accepted monthly Work numerators and then recomputes shares; it never averages monthly
shares. The output includes full and fractional Work counts, collaborative/single/international/
cross-region Work counts, partner institution and country counts, fractional collaboration strength,
repeat partners, and effective partner count.

For positive fractional partner strengths `w_i`, `p_i = w_i / sum(w_i)` and:

```text
effective_partner_count = exp(-sum(p_i * ln(p_i)))
repeat_partner_ratio = partners active in >= 2 months / all positive partners
```

The repeat ratio is null when no partner exists. Edge persistence always uses the nominal window
length, even for an incomplete window:

```text
edge_persistence = active_month_count / window_months
```

## Exact sparse edge index

Expanding every positive edge at every rolling endpoint would create 187,718,512 rows on the accepted
2010-2025 facts. `collaboration_edge_window_intervals.parquet` instead stores 7,598,244 maximal,
inclusive intervals during which a canonical unordered stable-ID pair is positive. The interval
index represents the same 187,718,512 endpoints exactly; it is not an approximation or hidden score.
`query_rolling_edges` filters the interval index and reconstructs full/fractional counts and active
months from `collaboration_edges_month.parquet` over the exact calendar boundary.

Normalized intensity is intentionally not stored in the interval index because it depends on the
two endpoint institutions' full Work denominators for the selected window. GISNET-128 will join the
exact queried edge counts to `institution_outputs_rolling.parquet` and apply the existing declared
formula, rather than duplicating denominators or storing a stale derived value here.

The current physical outputs contain 23,476,936 positive institution-window rows and use about 215
MiB for institutions plus 57 MiB for the edge index. The accepted bounded build used 4.75 GiB peak
RSS and completed in about 7 minutes with one DuckDB thread. A latest Broad/organization 24-month
global edge query returned 263,258 rows; fetching all rows into Python took about 1.22 seconds, while
a representative 50-row incident query took about 0.10 seconds. These measurements are hardware- and
cache-dependent and are recorded as build evidence, not scientific metrics.

## Graph policy

The rolling institution and edge facts are exact graph inputs. Node sets must include positive-output
institutions with no edge as isolates. At `2025-12`, Broad/organization 24m has 18,324 nodes (1,035
isolates) and 263,258 edges; Strict/organization 12m has 7,274 nodes (838 isolates) and 31,222 edges.
Representative exact PageRank and Leiden runs were subsecond, but computing every graph metric for all
2,304 corpus/hierarchy/window endpoints is not justified. Rolling betweenness is therefore **not
computed** by GISNET-123. A future approximation must be explicitly named and parameterized; it may
never silently replace an exact metric.

## Scope and identity boundary

Like GISNET-122, this layer uses `is_primary_research_scope` across all configured geographies. It is
intentionally broader than the released annual `is_primary_network_scope`; backward compatibility
means the annual files remain unchanged, not that totals must be equal. Organization and umbrella are
identity views, not canonical schools. After GISNET-125, any canonical-school rolling view must be
rebuilt from Work-level memberships so that mapped organizations are de-duplicated and self-pairs are
removed; summing these rows is not a valid shortcut.
