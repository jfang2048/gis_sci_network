# Recent completed-month OpenAlex sync

## Purpose and boundary

`sync-recent-works` adds a separate, incremental input overlay for current-decision rolling
views. It does not extend or rewrite the released 2010–2025 complete-year corpus.

At runtime, the command derives the latest fully completed **UTC calendar month**. For example,
a run on 2026-08-28 may retrieve through 2026-07-31 but never includes August. Each missing month
is queried with exact `from_publication_date` and `to_publication_date` bounds. OpenAlex describes
`publication_date` as an ISO date, usually the earliest electronic publication date, and documents
the two date filters for bounded Works queries:

- [OpenAlex Works attributes](https://help.openalex.org/data/works/attributes/)
- [OpenAlex filtering](https://help.openalex.org/api/filtering/)

The sync uses cursor paging with a page size of 100, the currently documented maximum, and follows
`next_cursor` until completion. See [OpenAlex paging](https://help.openalex.org/api/paging/).

## Incremental and recovery contract

1. Query Topics and countries are derived from the accepted historical plan; no source identifier
   is invented.
2. The retrieval ledger records a month only after every shard is complete and every cached raw
   page checksum validates.
3. A rerun plans only months absent from that ledger. Within an incomplete month, existing cursor
   checkpoints and cached pages are reused, so only missing pages require requests.
4. Raw responses use the shared redacted cache. The API key is read from the environment for a bulk
   run and is excluded from plans, cache identities, checkpoints, failures, and the ledger.
5. Newly completed months are renormalized from validated cached pages into a separate recent-data
   directory. Work IDs are deduplicated across query shards and completed months.

The public OpenAlex snapshot is updated on a different cadence, and premium created/updated-date
sync filters have separate access rules. This workflow therefore makes no premium-sync claim and
does not silently infer late-indexed backfills. If a future policy requires a backfill, its exact
publication-date range must be explicit. See [OpenAlex sync guidance](https://help.openalex.org/access/sync/).

## Labels and comparison policy

The ledger and normalization summary store:

- retrieval date and first/last retrieval timestamps;
- `coverage_start`, `coverage_end`, and `window_end`;
- the exact completed months;
- `date_coverage: completed_calendar_months`;
- `is_partial_current_year` and `current_year_state`;
- `raw_partial_year_comparison_allowed: false`.

Recent data may feed rolling current-decision views whose exact boundaries are already displayed.
Raw partial-current-year totals must not be compared with complete historical annual totals. The
annual scientific layer remains the historical reference.

## Commands

```bash
# Deterministic preview; no network request and no write
uv run python -m gisnet.cli sync-recent-works --dry-run

# Bulk, resumable execution; OPENALEX_API_KEY remains environment-only
uv run python -m gisnet.cli sync-recent-works --resume
```

The command writes current artifacts only after acquiring the project run lock. Interrupted raw
queries resume from validated checkpoints; final JSON and Parquet outputs are validated before
atomic replacement.
