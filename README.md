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
uv run python -m gisnet.cli normalize-works --resume
uv run python -m gisnet.cli extract-institutions --resume
uv run python -m gisnet.cli build-institutions --resume
uv run python -m gisnet.cli apply-geography --resume
uv run python -m gisnet.cli enrich-institutions --ror-mode cache --resume
uv run python -m gisnet.cli build-hierarchy --resume
uv run python -m gisnet.cli diagnose-versions --resume
uv run python -m gisnet.cli build-corpus --resume
uv run python -m gisnet.cli build-work-institutions --resume
uv run python -m gisnet.cli build-edges --resume
uv run python -m gisnet.cli build-outputs --resume
uv run python -m gisnet.cli build-region-flows --resume
uv run python -m gisnet.cli validate
uv run python -m gisnet.cli verify-reproducibility
uv run python -m gisnet.cli compute-edge-intensity --resume
uv run python -m gisnet.cli build-graphs --resume
uv run python -m gisnet.cli compute-metrics --resume
uv run python -m gisnet.cli detect-communities --resume
uv run python -m gisnet.cli build-layout --resume
uv run python -m gisnet.cli audit-top-entities --resume
uv run python -m gisnet.cli run-sensitivity --resume
uv run python -m gisnet.cli build-figures --resume
uv run python -m gisnet.cli build-matrix --resume
uv run python -m gisnet.cli build-map-data --resume
uv run python -m gisnet.cli build-network-view --resume
uv run python -m gisnet.cli build-dashboard-data --resume
uv run python -m gisnet.cli run-pipeline --start-year 2010 --end-year 2025 --corpus all --hierarchy all --resume
uv run python -m gisnet.cli report --resume
uv run python -m gisnet.cli build-data-dictionary --resume
```

Network-dependent tests are marked `network` and are skipped from ordinary offline
quality gates unless explicitly selected.

The end-to-end command validates every output and provenance manifest before deciding
whether to skip or rebuild a stage. Incomplete downloads always resume, stale derived
branches rebuild in dependency order, valid raw pages are never deleted, and a failure
prints the exact recovery command.

## View the dashboard

The repository includes a compact, public processed-data snapshot, so viewing the
result does not require an API key or a new OpenAlex download.

```bash
uv sync
uv run streamlit run dashboard/app.py
```

Open <http://localhost:8501>. The eight-page dashboard includes regional trends,
collaboration matrices, geographic and fixed-layout network views, an institution-pair
explorer, Topic-family comparisons, methods, and data-quality metadata. See
[`dashboard/README.md`](dashboard/README.md) for snapshot rebuild details.

The generated research methods and limitations are documented in
[`outputs/reports/methodology.md`](outputs/reports/methodology.md).
Every public table column, primary key, null semantic, lineage path, configuration hash,
code hash, and known issue is documented in
[`outputs/reports/data_dictionary.md`](outputs/reports/data_dictionary.md), with a
machine-readable companion at [`data/reference/data_dictionary.json`](data/reference/data_dictionary.json).

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

## Data availability and public-repository policy

The public repository contains source code, configuration, compact reference artifacts, manifests,
and reproducibility instructions. It intentionally excludes credentials and large generated data:
raw/cache pages, interim DuckDB files, processed Parquet files, and private outputs are ignored by
Git. These files are rebuilt locally rather than committed.

The bibliographic source is [OpenAlex](https://openalex.org/). Its
[API documentation](https://docs.openalex.org/) describes access to the upstream records; this
project's `plan-download`, `download-works`, and `normalize-works` commands reproduce the local data
layers from the tracked query plan and configuration. Normalization defaults to a bounded 6 GB
DuckDB memory limit and one worker thread so larger-than-memory processing spills safely to disk.
