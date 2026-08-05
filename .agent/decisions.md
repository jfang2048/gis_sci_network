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
