# School-decision analytical contract

Contract version: `school-decision-2026-08-17-v1`
Machine-readable source: [`config/school_decision.yml`](../config/school_decision.yml)

Validate the contract and refresh its provenance manifest with:

```bash
uv run python -m gisnet.cli validate-school-contract --resume
```

## Product boundary

The school-decision layer helps a user investigate the GIS research activity, specialization,
collaboration ecosystem, network position, citation influence, research proximity, and recent
momentum of a university or research institution. **School** is concise interface language; it
does not imply that every included research organization awards degrees.

This is not an admissions ranking. It does not directly measure teaching quality, admissions
probability, tuition, funding, student satisfaction, or degree-program availability. Publication
volume is activity, not quality, and the system does not define a universal best institution.

## Eligible profile entity

An eligible profile is a stable source organization, or an evidence-backed canonical school, with
at least one Work in the selected GIS corpus and primary research scope during a supported
observation period. The default primary source types are education, government, nonprofit, and
facility; eligibility is implemented with `is_primary_research_scope`
and the normalized categories `education`, `government_research`, `nonprofit_research`, and
`research_facility`. Secondary organization types remain retained and explicitly filterable.

Eligibility includes Europe, Asia, the Americas, Africa, Oceania, and explicitly unknown geography.
It never depends on a global node rank, global edge rank, map Top N, visualization score, or
coordinate availability. Stable IDs, not names, are join keys. Missing coordinates affect maps
only. Ambiguous names and unresolved organizational fragmentation must remain visible.

The organization identity is immutable. A different `canonical_school_id` requires documented
OpenAlex lineage, ROR relationships, or an explicit override with rule ID, reason, evidence, and
provenance. National academies, federated systems, hospitals, laboratories, and research councils
are not collapsed merely because a possible relationship exists. The current released umbrella
view has zero active collapses and is therefore non-informative until supported rules exist.

## Two temporal modes

1. **Historical scientific mode** uses complete-calendar-year annual outputs for 2010-2025.
2. **Current school-decision mode** remains dependency-gated through GISNET-124. GISNET-121 now
   provides the validated publication-date facts and coverage QA; GISNET-122 through GISNET-124
   will add subannual facts, rolling windows, and safe incremental current-year acquisition. A raw
   partial year is never compared with a complete year.

The primary stored keys are `publication_month`, `publication_quarter`, `publication_year`,
`window_start`, `window_end`, and `window_months`. Missing month/day values are never fabricated.
The implemented date fact preserves `publication_date_raw`, stores a canonical date/month/quarter
only for an exact calendar-valid, year-consistent date within the supported range, and distinguishes
`exact_valid`, `missing`, `malformed`, `year_conflict`, and `outside_supported_range`. Year-only and
month-only source strings are malformed for subannual use rather than completed with an invented
month or day. Publication time is bibliographic observation time; it is not collaboration start,
research start, project start, or author-mobility time.

The current source snapshot supplies full valid dates for every normalized Work, but it has no
independent precision field and 261,950 of 1,176,947 values fall on January 1. These source values
remain eligible because there is no evidence-backed alternative rule; their concentration is
reported with its numerator and denominator. The exact-DOI version-family policy is also unchanged:
primary Strict/Broad facts retain the existing recommended representative, while ambiguous
title-only families remain separate. No new date-based family deduplication is applied. Compared
with the declared all-version sensitivity, the primary policy removes 129 Strict and 360 Broad
exact-date-eligible records across 71 and 119 publication months respectively; the largest monthly
differences are 7 Strict and 14 Broad Works.

Recommended defaults are rolling 12 months for recent trend, rolling 24 months for a stable recent
profile, rolling 36 months for longer stability, and annual data for long-term history.

## Independent analytical dimensions

| Dimension | Meaning | Examples | Must not mean |
|---|---|---|---|
| Research activity | Amount of included GIS research | Full/fractional Works, rolling Works | Quality |
| Research specialization | Topic concentration or baseline-relative concentration | Topic share, specialization lift | Better research |
| Collaboration reach | Institutional and geographic breadth | International share, partner/country counts | Quality |
| Collaboration persistence | Recurrence of publication-time observations | Repeat-partner ratio, monthly persistence | Relationship start or duration |
| Network position | Position in a declared graph | Degree, PageRank, betweenness, community, bridge score | Absolute or causal influence |
| Citation influence | Bibliometric and directed citation-flow proxies | Citations, FWCI, incoming citation flow | Teaching quality or causal impact |
| Research proximity | Similarity of Topic profiles | Cosine Topic similarity | Collaboration |
| Recent momentum | Change between like recent windows | Rolling 12m change | Forecast or quality growth |
| User-defined research fit | Alignment with explicit user preferences | Topic fit, weighted fit score | Universal quality or admissions rank |

Every metric is classified as descriptive, normalized, bibliometric, network-derived, or
user-defined. Exact formulas, units, availability, graph/time boundaries, comparison conditions,
and forbidden interpretations are stored in the machine-readable contract.

Current code semantics retained by the contract include:

- normalized co-authorship intensity uses fractional edge count divided by the geometric mean of
  the two institutions' **full Work counts**;
- international and cross-region shares divide qualifying Works by **all included institutional
  Works** in the period;
- bridge score is cross-macro-region fractional strength divided by total fractional strength;
- PageRank and betweenness are graph-relative and cannot be compared without matching graph and
  method boundaries.

Later GISNET-120+ tasks must reconcile any legacy report wording that differs from these implemented
definitions.

## Separate evidence layers

- **Co-authorship** is actual shared-publication collaboration and is undirected.
- **Citation flow** is a directed knowledge-flow proxy from citing institution to cited institution.
- **Topic similarity** is cosine research-profile proximity.

These layers are never merged into a single scientific edge weight. Topic similarity never means
collaboration. Existing core limits remain disclosed until complete school-oriented products are
built.

## Score policy

An unexplained global university-quality score is prohibited. The only permitted combined score is
`user_defined_fit_score`: an optional UI-only weighted mean of displayed 0-to-1 components over the
current filtered candidate set. Naturally bounded proportions and similarities use their displayed
values. Activity and momentum use ascending average percentile ranks:
`(average_rank - 1) / (candidate_count - 1)`; a one-candidate or all-tied reference set is 0.5.
The score is null when every weight is zero or any positive-weight component is missing. All
weights, component values, transformations, candidate-set boundaries, corpus, and time window must
be visible. Weights and scores remain in UI session state and are never persisted in scientific
source datasets.

## Scientific status

The GIS Topic registry is provisional and AI-reviewed; human review remains incomplete. Topic-based
school comparison is exploratory bibliometric evidence, not authoritative scientific truth. That
warning must remain visible on School Finder, School Profile, Compare Schools, documentation, and
released metadata.
