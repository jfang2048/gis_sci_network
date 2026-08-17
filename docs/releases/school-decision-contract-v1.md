# School-decision analytical contract v1

This development prerelease records the repository's transition from a primarily regional,
complete-year bibliometric dashboard toward a research-based institution-comparison system. It
does **not** claim that the School Finder or school-profile interface is complete.

![Architecture showing the released historical layer and planned school-decision extension](https://raw.githubusercontent.com/jfang2048/gis_sci_network/school-decision-contract-v1/figures/school_decision_architecture.svg)

## Included in this snapshot

- A strict, machine-readable school-decision contract with 30 independently interpretable metrics.
- Explicit separation of research activity, specialization, collaboration reach and persistence,
  network position, citation influence, research proximity, momentum, and user-defined fit.
- A prohibition on unexplained global university-quality scores. User-defined fit remains UI-only
  and its weights are not stored in scientific datasets.
- Separate citation-flow, Topic-similarity, and multiplex comparison layers added after `v0.1.0`.
- Refined accessible SVG figures for the existing complete-year annual analysis.
- Dependency-gated backlog tasks GISNET-121–139 for date QA, rolling windows, complete school
  indexing, per-school partners, geographic flows, comparison UI, validation, and final release.

## Current versus planned

| Capability | Status |
| --- | --- |
| Complete 2010–2025 annual scientific analysis | Available |
| Citation flow as a separate knowledge-flow proxy | Available |
| Topic similarity as a separate research-proximity measure | Available, core-limited |
| School-decision definitions and semantic validation | Available |
| Monthly/quarterly facts and rolling 12m/24m/36m windows | Planned |
| Complete-universe School Finder and School Profile | Planned |
| School comparison and school ego maps | Planned |

## Verification

The tagged snapshot is validated with:

```bash
uv sync --extra dev
uv run python -m gisnet.cli validate-school-contract --resume
uv run python -m gisnet.release verify
scripts/quality-gate.sh
```

The release manifest inventories public configuration, compact tables, reference artifacts,
figures, reports, and provenance manifests with SHA-256 checksums. The privacy scan rejects API
keys, GitHub tokens, private keys, and private home paths.

## Downloadable assets

- `manifest.json` and `manifest.json.sha256`: checksums and privacy-verified inventory for the
  public snapshot.
- `gisnet-figures-school-decision-contract-v1.zip` and its `.sha256`: SVG source figures plus PNG
  renditions for convenient viewing and reuse.

## Important interpretation boundaries

- This is research-based institutional comparison, not an admissions ranking.
- Publication volume is activity, not teaching quality or universal institutional quality.
- Co-authorship is observed publication collaboration; citation flow is a knowledge-flow proxy;
  Topic similarity is research proximity. They are not merged into one scientific edge weight.
- The GIS Topic registry remains provisional and has not completed human review.
- The released annual network and Topic views remain visualization-core limited. The planned
  complete school index and per-school partner index are specifically intended to remove that UI
  limitation without destroying the annual scientific outputs.
