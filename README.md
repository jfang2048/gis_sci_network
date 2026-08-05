# Dynamic GIS Institutional Collaboration Network

This repository builds a reproducible annual network of GIS and broader geospatial
research collaboration among universities and research institutions in Europe, Asia,
and the Americas. Complete-year results cover 2010–2025 by default. Africa and Oceania
remain represented so mixed-region collaboration is not discarded.

The authoritative execution plan is
[`AI_EXECUTION_BACKLOG_GIS_COLLABORATION.md`](AI_EXECUTION_BACKLOG_GIS_COLLABORATION.md).

## Development setup

Python 3.11 or newer is required. With [uv](https://docs.astral.sh/uv/):

```bash
uv sync --extra dev
uv run python -m gisnet.cli status
uv run pytest
```

Set an OpenAlex API key only in the environment. The uppercase name takes priority;
the lowercase name is accepted for compatibility.

```bash
export OPENALEX_API_KEY='...'
uv run python -m gisnet.cli check-env
```

The key is never written to tracked configuration, caches, manifests, or run logs.

## Execution state

Agent task state and audit records live in `.agent/`. Generated raw, interim, cache,
and private output directories are intentionally ignored. All data-producing commands
will use temporary files, validate them, and atomically replace final outputs.

## Current commands

```bash
uv run python -m gisnet.cli status
uv run python -m gisnet.cli next-task
uv run python -m gisnet.cli check-env
uv run python -m gisnet.cli validate-regions
uv run python -m gisnet.cli discover-topics --resume
uv run python -m gisnet.cli sample-topic-works --resume
uv run python -m gisnet.cli freeze-topics
uv run python -m gisnet.cli validate-corpus-boundary
uv run python -m gisnet.cli profile-institution-types --resume
uv run python -m gisnet.cli profile-work-types --resume
uv run python -m gisnet.cli plan-download --dry-run
uv run python -m gisnet.cli plan-download --resume
uv run python -m gisnet.cli download-works --resume --workers 4
```

Network-dependent tests are marked `network` and are skipped from ordinary offline
quality gates unless explicitly selected.

## Topic registry status

`config/topic_registry.yml` is currently a **provisional, AI-reviewed** registry backed
by candidate metadata and deterministic sampled-work evidence in
`outputs/reports/topic_review.md`. No human review is implied. Uncertain Topics are
excluded from primary Strict and Broad results and retained for sensitivity analysis.

## Current corpus and download policy

Institution and work-type mappings are explicit configuration rather than embedded code.
Unknown future source types remain flagged for review. The primary work set includes articles,
conference papers, reviews, data papers, and software papers; preprints and expanded types are
separate sensitivity views.

The saved bulk plan uses the Broad Topic set because it is a superset of Strict and can derive
both views after normalization. It shards by publication year, Topic, and eligible author-
institution country. Query-count estimates include expected duplicates across shards; downstream
normalization must deduplicate on OpenAlex Work ID while preserving source query IDs. The current
boundary precision is intentionally withheld until sufficient human annotation exists.
