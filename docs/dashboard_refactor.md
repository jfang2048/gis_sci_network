# Dashboard refactor and performance contract

## Scope

GISNET-137 is a behavior-preserving refactor of the processed-data-only Streamlit dashboard. It
must not change scientific values, page semantics, stable-ID selection, source-data boundaries, or
the seven-page information architecture. It must not add a dependency.

The refactor stops when:

1. data access, reusable presentation components, filter logic, map/chart builders, School Profile,
   and School Comparison are outside the oversized `dashboard/app.py` entry point;
2. the existing dashboard behavior suite and new module-boundary regressions pass;
3. complete-school queries retain predicate pushdown and meet the budgets below; and
4. the full repository quality gate and release verification pass.

## Regression lock before refactoring

The pre-refactor dashboard integration suite covers all seven public pages, every global control,
the Geographic Flow Explorer, School Finder/Profile/Compare behavior, the School Ego Map for a
school outside the prior global core, the fixed-layout collaboration view, directed citation flow,
Topic research proximity, institution-pair history, exact tables, provisional-corpus warnings, and
data-quality evidence.

Baseline command on commit `fb2648b`:

```bash
/usr/bin/time -f 'elapsed_seconds=%e max_rss_kib=%M' \
  uv run pytest -q tests/integration/test_dashboard.py
```

Observed baseline: 7 tests passed in 14.47 seconds; command wall time was 15.20 seconds and maximum
resident set size was 1,154,412 KiB. These are measured local test-process values, not production
service-level claims.

Before moving implementation, add regression checks that enforce a thin entry point, explicit
module boundaries, stable cached-table contracts, and predicate-pushed complete-school queries.

## Small refactor passes

1. Extract cached snapshot loading and required-table validation into a dashboard runtime module.
2. Extract labels, colors, exact-table rendering, empty-state rendering, and shared Plotly styling
   into a presentation-components module.
3. Keep filter transformations in `dashboard_filters.py` and map/chart construction in the existing
   geographic-flow, School Ego Map, and network modules; move any remaining pure filter helper out
   of the entry point.
4. Move School Finder, School Profile, and School Comparison rendering into page modules that reuse
   the existing predicate-pushed query and scientific-view helpers.
5. Delete only code proven unreachable by the seven-page router; do not create replacement layers
   or compatibility abstractions for dead paths.
6. Run focused regressions after each pass, then the complete quality gate.

## Complete-school query budgets

The dashboard may load the compact complete school index once through the Streamlit resource cache.
It must query School Profile, School Topic Profile, and School Ego Partner Parquets with DuckDB
predicate pushdown by stable school ID, corpus, window/period, and school hierarchy where applicable.
It must not eagerly load the 1,388,052-row partner table or any complete scientific edge source into
pandas.

Measured pre-refactor baseline used the four highest-recent-activity schools with retained partner
evidence, selected from the checked-in index by stable ID. Nine query samples were used except for
the three-sample index load:

| Path | Rows returned | Median ms | Maximum ms | Deep result memory |
| --- | ---: | ---: | ---: | ---: |
| Complete school index load | 28,042 | 61.073 | 63.897 | 26,585,245 bytes |
| One-school profile query | 1 | 41.056 | 49.209 | 2,388 bytes |
| Four-school profile query | 4 | 48.569 | 50.774 | 9,180 bytes |
| One-school Topic query | 13 | 25.710 | 26.015 | 3,762 bytes |
| Four-school Topic query | 51 | 26.805 | 31.015 | 14,715 bytes |
| One-school ego-partner query | 50 | 20.244 | 21.148 | 32,544 bytes |

Acceptance budgets on the same local snapshot and command shape:

- complete school-index load: median below 250 ms and deep frame memory below 32 MiB;
- one-to-four-school profile/Topic queries: median below 100 ms and returned-frame memory below
  128 KiB;
- one-school ego-partner query: median below 100 ms, no more than the retained 50 rows, and
  returned-frame memory below 128 KiB;
- seven-page integration suite: wall time below 30 seconds and peak RSS below 1.5 GiB.

Performance results are descriptive for this local checked-in snapshot. They must be remeasured
after the refactor; no fabricated or inferred value may replace an observed result.

## Completed refactor

The entry point now contains routing and the remaining annual/global views. It delegates cached
snapshot I/O to `dashboard/dashboard_data_access.py`, shared Plotly/Streamlit presentation to
`dashboard/dashboard_components.py`, stable-ID selection to
`dashboard/dashboard_school_common.py`, and the three institution-first pages to dedicated Finder,
Profile, and Comparison modules. Scientific filters remain in
`src/gisnet/visualization/dashboard_filters.py`; map and chart construction continues to use the
existing geographic-flow, School Ego Map, network, profile, comparison, and scientific-layer
modules.

The unreachable legacy `School Ego Map` route was deleted after the seven-page router and baseline
suite proved that School Profile is the sole public owner of that rendering. `dashboard/app.py`
decreased from 3,177 lines / 128,940 bytes to 1,525 lines / 62,019 bytes. No dependency changed.

Repeatable query benchmark:

```bash
uv run python scripts/benchmark_dashboard.py --samples 9
```

Observed post-refactor results on the same snapshot and representative stable IDs:

| Path | Rows returned | Median ms | Maximum ms | Deep result memory |
| --- | ---: | ---: | ---: | ---: |
| Complete school index load | 28,042 | 59.555 | 90.905 | 26,585,245 bytes |
| One-school profile query | 1 | 42.448 | 44.290 | 2,388 bytes |
| Four-school profile query | 4 | 48.711 | 50.009 | 9,180 bytes |
| One-school Topic query | 13 | 25.663 | 26.312 | 3,762 bytes |
| Four-school Topic query | 51 | 27.013 | 28.016 | 14,715 bytes |
| One-school ego-partner query | 50 | 20.094 | 20.501 | 32,544 bytes |

All six benchmark checks passed their declared latency, returned-memory, and retained-row budgets.
The isolated seven-page suite passed 7 tests in 12.48 seconds; command wall time was 13.05 seconds
and maximum resident set size was 1,074,200 KiB. Relative to the measured baseline, wall time fell
by 2.15 seconds and peak RSS fell by 80,212 KiB. These small observed differences are not claimed as
causal performance improvements; the acceptance result is that behavior and budgets were preserved.
