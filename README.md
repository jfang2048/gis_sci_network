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
```

Network-dependent tests are marked `network` and are skipped from ordinary offline
quality gates unless explicitly selected.
