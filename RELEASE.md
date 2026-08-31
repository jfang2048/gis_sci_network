# Release guide

The stable `v0.1.0` tag is the first reproducible public release of the 2010–2025 GIS institutional
collaboration network. The current `main` snapshot preserves that historical scientific mode and
adds the validated current school-decision mode, including exact subannual facts, rolling windows,
complete stable-ID school access, profiles, comparisons, and separate scientific layers.

## Published channels

| Channel | Tag | Meaning |
| --- | --- | --- |
| Stable scientific release | [`v0.1.0`](https://github.com/jfang2048/gis_sci_network/releases/tag/v0.1.0) | Reproducible complete-year annual analysis |
| Development prerelease | [`school-decision-contract-v1`](https://github.com/jfang2048/gis_sci_network/releases/tag/school-decision-contract-v1) | Immutable contract snapshot; later temporal facts on `main` are not part of this tag |
| Current validated snapshot | [`main`](https://github.com/jfang2048/gis_sci_network) | Historical mode plus the completed GISNET-120–139 school-decision extension |

Neither the contract prerelease nor the current snapshot replaces the stable annual tag. The
contract tag records the initial analytical specification. The current snapshot implements both
declared modes and preserves their time and interpretation boundaries.

[![Current architecture showing validated annual and school-decision modes](figures/school_decision_architecture.svg)](figures/school_decision_architecture.svg)

## See the result

```bash
uv sync
uv run streamlit run dashboard/app.py
```

Open <http://localhost:8501>. Ordinary viewing uses only `dashboard/data/` and makes
no OpenAlex or ROR request.

## Verify the public release

```bash
uv run python -m gisnet.release verify
scripts/quality-gate.sh
```

`release/manifest.json` contains the size and SHA-256 checksum of every public
configuration file, compact table, reference artifact, static figure, report, and
provenance manifest. `release/manifest.json.sha256` protects the manifest itself.
The manifest checked in on `main` follows the current public asset set; immutable release tags
retain the manifest that was published at their tagged commit.

The stored GISNET-138 artifact at `data/reference/school_decision_validation.json` passed all 13
acceptance checks and is included with its provenance manifest and checksum. The quality gate reruns
the real-snapshot acceptance test when local processed sources are available.

## Reproduce the data pipeline

Raw API responses and the full local processed layer are intentionally excluded from
the public repository. To rebuild them in a clean clone, provide the API key through
the environment only and run the resumable pipeline:

```bash
uv sync --extra dev
export OPENALEX_API_KEY='...'
uv run python -m gisnet.cli check-env
uv run python -m gisnet.cli run-pipeline \
  --start-year 2010 --end-year 2025 --corpus all --hierarchy all --resume
# Current school-decision mode from the validated historical layer.
uv run python -m gisnet.cli validate-school-contract --resume
uv run python -m gisnet.cli build-publication-date-qa --resume
uv run python -m gisnet.cli build-subannual-facts --resume
uv run python -m gisnet.cli build-rolling-facts --resume
uv run python -m gisnet.cli build-school-identities --resume
uv run python -m gisnet.cli build-school-index --resume
uv run python -m gisnet.cli build-school-partners --resume
uv run python -m gisnet.cli build-school-profiles --resume
uv run python -m gisnet.cli build-dashboard-data --resume
uv run python -m gisnet.cli validate-school-decision --resume
uv run python -m gisnet.cli report --resume
uv run python -m gisnet.cli build-data-dictionary --resume
uv run python -m gisnet.release build --root .
uv run python -m gisnet.release verify --root .
scripts/quality-gate.sh
```

Interrupted downloads resume from checkpoints; valid raw pages are never deleted.
The API key is not written to configuration, manifests, datasets, or logs.

## Large upstream data links

- [OpenAlex data and snapshots](https://docs.openalex.org/download-all-data)
- [Research Organization Registry data dump](https://ror.readme.io/docs/data-dump)
- [United Nations M49 geography standard](https://unstats.un.org/unsd/methodology/m49/)

## Known limitations

- The Topic registry is provisional and has not received human review.
- `School` means an eligible university or research institution; it does not assert degree-
  granting status, programme availability, admissions suitability, teaching quality, cost, or fit.
- The project defines no universal institutional-quality score. Activity, specialization,
  collaboration, centrality, citation, proximity, and data quality remain separate.
- Publication time is bibliographic observation time, not collaboration, research, project, or
  author-mobility start time. The source has no independent date-precision flag.
- Institution coordinates are sparse and are never imputed. Missing dates and unsupported layer
  values are also not imputed.
- Network, map, citation, Topic-proximity, and retained partner views are thresholded or bounded;
  absence from a display does not prove absence from source facts.
- Community matches below Jaccard 0.25 are retained but explicitly uncertain.
- The visualization score ranks display edges only and is not a primary research metric.
- 2025 is the last complete calendar year; partial 2026 observations are excluded.
- The optional citation layer is corpus-internal. Its coverage table reports references whose
  cited Work or in-scope cited institution is unavailable, and preserves negative citation lags
  as source-data anomalies rather than silently excluding them.
- The optional Topic-similarity layer is a union-top-k network over a deterministic annual core,
  not a complete all-institution similarity matrix. Its coverage table reports the omitted
  institution-year rows and the edge-selection boundary.
- The optional multiplex comparison keeps co-authorship, directed citation flow, and Topic
  proximity separate. Pairwise overlaps are unweighted node/dyad-presence diagnostics; citation
  direction is ignored only for dyad matching, and no cross-layer weight or composite score is
  defined. Topic-layer comparisons retain the 500-institution annual-core boundary.

See [`outputs/reports/methodology.md`](outputs/reports/methodology.md) for the full
method and limitations, and [`outputs/reports/data_dictionary.md`](outputs/reports/data_dictionary.md)
for column-level lineage and null semantics.
