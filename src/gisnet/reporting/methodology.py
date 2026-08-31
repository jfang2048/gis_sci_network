"""Generate the methodology report from validated repository artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from gisnet.artifacts import current_git_commit, utc_timestamp, write_json_artifact
from gisnet.atomic import atomic_write_text
from gisnet.config import config_file_hash, load_yaml, semantic_hash
from gisnet.dataset import file_sha256
from gisnet.manifest import DatasetManifest

_STAGE_VERSION = "methodology-report-2026-08-31-v3"

SUMMARY_PATHS: dict[str, Path] = {
    "corpus": Path("data/reference/work_corpus_summary.json"),
    "edges": Path("data/reference/collaboration_edges_summary.json"),
    "metrics": Path("data/reference/network_metrics_summary.json"),
    "communities": Path("data/reference/community_detection_summary.json"),
    "continuity": Path("data/reference/community_continuity_summary.json"),
    "sensitivity": Path("data/reference/sensitivity_summary.json"),
    "map": Path("data/reference/geographic_map_summary.json"),
    "dashboard": Path("data/reference/dashboard_bundle_summary.json"),
    "reproducibility": Path("data/reference/reproducibility_validation.json"),
    "institutions": Path("data/reference/institution_master_summary.json"),
    "hierarchy": Path("data/reference/institution_hierarchy_summary.json"),
    "versions": Path("data/reference/work_version_diagnostics_summary.json"),
    "trends": Path("data/reference/annual_trends_summary.json"),
    "matrix": Path("data/reference/collaboration_matrix_summary.json"),
    "publication_dates": Path("data/reference/publication_date_qa_summary.json"),
    "subannual": Path("data/reference/subannual_temporal_summary.json"),
    "rolling": Path("data/reference/rolling_temporal_summary.json"),
    "school_identity": Path("data/reference/school_identity_summary.json"),
    "school_index": Path("data/reference/school_index_summary.json"),
    "school_partners": Path("data/reference/school_partner_index_summary.json"),
    "school_profiles": Path("data/reference/school_profile_summary.json"),
    "school_validation": Path("data/reference/school_decision_validation.json"),
}

REQUIRED_HEADINGS = (
    "## 1. Research questions",
    "## 2. Geographic scope",
    "## 3. GIS corpus definitions",
    "## 4. Data sources",
    "## 5. Institution resolution",
    "## 6. Counting methods",
    "## 7. Dynamic network metrics",
    "## 8. Validation",
    "## 9. Sensitivity analysis",
    "## 10. Limitations",
    "## 11. Reproducibility",
    "## 12. Data ethics and geographic naming convention",
)


def build_methodology_report(
    *,
    project_path: str | Path,
    topic_registry_path: str | Path,
    regions_path: str | Path,
    output_path: str | Path,
    summary_paths: dict[str, Path] | None = None,
) -> dict[str, Any]:
    """Render and validate the report without making any network request."""
    project = _load_mapping(project_path)
    registry = _load_mapping(topic_registry_path)
    regions = _load_mapping(regions_path)
    paths = SUMMARY_PATHS if summary_paths is None else summary_paths
    summaries = {name: _load_json(path) for name, path in paths.items()}
    school_validation = summaries["school_validation"]
    if (
        school_validation.get("status") != "passed"
        or school_validation.get("acceptance_check_count") != 13
        or school_validation.get("passed_check_count") != 13
    ):
        raise ValueError("school-decision release validation has not passed all 13 checks")
    figures = _validated_figures(summaries)
    report = render_methodology(project, registry, regions, summaries)
    _validate_report(report)
    destination = Path(output_path)
    atomic_write_text(destination, report)
    report_hash = file_sha256(destination)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "project_hash": config_file_hash(project_path),
                "topic_registry_hash": config_file_hash(topic_registry_path),
                "regions_hash": config_file_hash(regions_path),
                "summary_hashes": {name: file_sha256(path) for name, path in sorted(paths.items())},
            }
        ),
        "required_section_count": len(REQUIRED_HEADINGS),
        "present_section_count": sum(heading in report for heading in REQUIRED_HEADINGS),
        "figure_count": len(figures),
        "all_figures_from_processed_data": True,
        "figure_paths": figures,
        "provisional_topic_decisions_disclosed": "No human review has occurred" in report,
        "partial_year_policy_disclosed": "No partial 2026 data" in report,
        "composite_score_non_primary_disclosed": "not a primary scientific metric" in report,
        "historical_mode_disclosed": "Historical scientific mode" in report,
        "school_decision_mode_disclosed": "Current school-decision mode" in report,
        "school_validation_passed": (
            school_validation["status"] == "passed"
            and school_validation["acceptance_check_count"] == 13
            and school_validation["passed_check_count"] == 13
            and "passed all 13 of 13 acceptance checks" in report
        ),
        "admissions_and_quality_limits_disclosed": (
            "not an admissions ranking" in report and "no universal institutional-quality" in report
        ),
        "report_sha256": report_hash,
        "output": str(destination),
        "generated_at_utc": utc_timestamp(),
    }


def render_methodology(
    project: dict[str, Any],
    registry: dict[str, Any],
    regions: dict[str, Any],
    summaries: dict[str, dict[str, Any]],
) -> str:
    """Render the fixed report structure from already-loaded, validated evidence."""
    analysis = _mapping(project, "analysis")
    consortium = _mapping(project, "consortium")
    network = _mapping(project, "network")
    corpus = summaries["corpus"]
    edges = summaries["edges"]
    metrics = summaries["metrics"]
    communities = summaries["communities"]
    continuity = summaries["continuity"]
    sensitivity = summaries["sensitivity"]
    map_summary = summaries["map"]
    reproducibility = summaries["reproducibility"]
    institutions = summaries["institutions"]
    hierarchy = summaries["hierarchy"]
    versions = summaries["versions"]
    trends = summaries["trends"]
    matrix = summaries["matrix"]
    publication_dates = summaries["publication_dates"]
    subannual = summaries["subannual"]
    rolling = summaries["rolling"]
    school_identity = summaries["school_identity"]
    school_index = summaries["school_index"]
    school_partners = summaries["school_partners"]
    school_profiles = summaries["school_profiles"]
    school_validation = summaries["school_validation"]
    country_count = len(regions.get("countries", []))
    persistence = " and ".join(str(value) for value in network["persistence_windows"])
    strict_topics = len(registry["strict_topic_ids"])
    broad_topics = len(registry["broad_topic_ids"])
    uncertain_topics = len(registry["uncertain_topic_ids"])
    return f"""# Methodology: Dynamic GIS Institutional Collaboration Network

Generated from validated repository artifacts. Data version: `gisnet-{project["project_version"]}`.
The numerical statements below are taken from the cited relative-path summaries and manifests.

## Analytical modes

**Historical scientific mode** uses complete calendar-year evidence from
{analysis["start_year"]} through {analysis["end_year"]}. It is the stable longitudinal mode for
annual outputs, networks, communities, geographic flows, citation flow, Topic proximity, and
sensitivity analysis.

**Current school-decision mode** adds exact publication month and quarter facts plus rolling 12-,
24-, and 36-month publication windows. The released snapshot ends at
{rolling["observation_end_month"]}; it does not mix a partial 2026 overlay into the historical
annual series. `School` is concise interface language for an eligible university or research
institution, not an admissions ranking, degree-program claim, or universal measure of quality.

## 1. Research questions

This project asks how institutional GIS and broader geospatial research collaboration changed
annually, which institutions connected regional communities, how patterns differ between Strict
and Broad corpus definitions, how current school profiles differ across declared rolling windows,
and how conclusions respond to documented analytical choices.
The unit of collaboration is an observed co-authored Work affiliation pair, not a citation or an
inferred relationship.

## 2. Geographic scope

Complete calendar years {analysis["start_year"]}-{analysis["end_year"]} are included. The focal
macro-regions are Europe, Asia, and the Americas; Africa and Oceania remain represented so that
mixed-region collaborations are not discarded. The frozen registry contains {country_count}
country or territory rows and uses `{regions["mapping_version"]}`. Macro-region, subregion, and
country matrices contain {matrix["matrix_and_drilldown_row_count"]:,} observed sparse rows.

## 3. GIS corpus definitions

The frozen OpenAlex Topic registry is `{registry["registry_version"]}`. The Strict view contains
{strict_topics} Topics; the Broad view contains {broad_topics}; {uncertain_topics} uncertain Topics
are excluded from primary results and retained for sensitivity analysis. The registry is
**provisional and AI-reviewed**. No human review has occurred. The primary corpus contains
{corpus["strict_primary_count"]:,} Strict and {corpus["broad_primary_count"]:,} Broad Works from
{corpus["work_count"]:,} normalized Works. Expanded-type and preprint variants remain separate
sensitivity views. Exact definitions live in `config/topic_registry.yml` and
`config/work_types.yml`.

## 4. Data sources

Bibliographic Works, authorships, institutions, Topics, and source identifiers come from OpenAlex.
Raw pages are cached with checksums and query IDs before normalization; the completed acquisition
contains only source-provided identifiers. ROR is an optional source for cached institution
enrichment, and UN Statistics Division M49 is the source for the geographic convention. Ordinary
dashboard viewing uses {summaries["dashboard"]["table_count"]} processed public tables and makes
no OpenAlex or ROR request.

School-decision publication time is bibliographic observation time, not collaboration, research,
project, or author-mobility start time. The source supplies a full date string without an
independent precision flag, so January-first values are retained and measured rather than
heuristically removed.
In this snapshot, {publication_dates["work_fact_row_count"]:,} normalized Works have date-QA facts;
missing, malformed, conflicting, or out-of-range dates would remain annual-only and are never given
fabricated months or days.

Validated sparse subannual facts contain {subannual["row_counts"]["institution_outputs_month"]:,}
positive institution-month rows and
{subannual["row_counts"]["collaboration_edges_month"]:,} positive collaboration-edge-month rows.
Zeros are derived from declared entity/period denominators rather than materialized or imputed.

## 5. Institution resolution

Stable OpenAlex institution IDs are the primary keys; source ROR IDs are preserved when present.
The master contains {institutions["institution_count"]:,} institutions, with
{institutions["metadata_qa_count"]:,} metadata QA rows. Two explicit hierarchy views are retained:
organization and umbrella. The hierarchy contains {hierarchy["hierarchy_row_count"]:,} rows and
{hierarchy["relationship_candidate_count"]:,} review candidates. Automatic name-only collapses:
{hierarchy["automatic_collapse_count"]}; explicit configured collapses:
{hierarchy["explicit_collapse_count"]}. Similar names therefore do not silently resolve to one
record.

The school-identity layer contains {school_identity["canonical_school_count"]:,} canonical source
institutions and performs {school_identity["automatic_collapse_count"]} automatic and
{school_identity["explicit_collapse_count"]} explicit collapses. The eligible search index contains
{school_index["eligible_school_count"]:,} stable IDs across every stored macro-region, including
{school_index["outside_prior_core_count"]:,} outside the prior 500-node visualization core.
Eligibility depends on Broad-primary research evidence, not visualization rank, coordinate
availability, admissions status, or a quality threshold.

## 6. Counting methods

For a Work with *k* distinct institutions, every undirected pair receives full weight 1 and
fractional weight `1 / choose(k, 2) = 2 / (k * (k - 1))`. Fractional contributions therefore sum
to 1 per collaborative Work. The stored maximum fractional reconciliation error is
{edges["maximum_fractional_sum_absolute_error"]:.3g}. The consortium warning and exclusion
thresholds are {consortium["warning_institution_count"]} and
{consortium["exclusion_institution_count"]} institutions. Primary annual output contains
{edges["annual_edge_count"]:,} edge observations. Normalized intensity divides fractional edge
weight by the geometric mean of the two institutions' full Work counts.

International collaboration share is the number of included institutional Works spanning more than
one country divided by **all included institutional Works** in the declared period. Cross-region
collaboration share uses the same denominator and counts Works spanning more than one macro-region.
Bridge score is cross-macro-region fractional strength divided by total fractional strength. These
shares are descriptive research metrics, not quality scores.

## 7. Dynamic network metrics

Each year/corpus/hierarchy combination is an undirected weighted graph. Stored metrics include
degree, full and fractional strength, weighted betweenness, PageRank, connected components,
density, assortativity, bridge score, and Leiden community assignments. Exact weighted
betweenness is used through {network["approximate_betweenness_threshold"]:,} nodes; larger graphs
use `{metrics["large_graph_betweenness_method"]}` and disclose that approximation. The
{metrics["graph_metric_row_count"]} graph rows and {metrics["node_metric_row_count"]:,} node rows
use seed {metrics["random_seed"]}. Leiden resolutions are
{", ".join(str(value) for value in communities["resolutions"])}; 1.0 is primary. Persistence uses
fixed-denominator trailing windows of {persistence} years and flags incomplete early windows.
For school-decision evidence, rolling persistence is active publication months divided by 12, 24,
or 36; complete-quarter persistence is active publication months divided by 3; and annual ego
persistence is active years in the trailing five-year window divided by 5. Publication persistence
does not identify a relationship start date or continuous relationship duration.
Adjacent-year communities use `{continuity["assignment_algorithm"]}`. Matches below Jaccard
{continuity["confident_match_threshold"]:.2f} are uncertain; the release records
{continuity["uncertain_match_count"]:,} such matches plus explicit split, merge, birth, and
disappearance event rows.
Visualization score is not a primary scientific metric; it only ranks edges for display.

## 8. Validation

Edge arithmetic reports {edges["work_edge_count"]:,} Work-edge rows and
{edges["collaborative_work_view_count"]:,} collaborative Work-view observations. The release
reproducibility check validated {reproducibility["dataset_check_count"]} core datasets with
{reproducibility["checksum_mismatch_count"]} checksum mismatches and
{reproducibility["temporary_output_count"]} incomplete temporary outputs. Recovery tests cover
pagination resumption, failed atomic validation, corrupt-cache quarantine, invalid-state backup,
and deterministic normalization. The stored PageRank sum error is
{metrics["maximum_pagerank_sum_error"]:.3g}.

The final cross-layer school-decision matrix passed all {school_validation["passed_check_count"]} of
{school_validation["acceptance_check_count"]} acceptance checks. The stored release therefore
passed all 13 of 13 acceptance checks. It covers outside-core search and partner availability,
subannual and rolling reconciliation, publication-date safety, geographic map/matrix and width
equality, Profile/Compare source equality, Strict-within-Broad, privacy, deterministic checksums,
and annual regressions.
The complete index has {school_index["eligible_school_count"]:,} schools; the compact partner index
has {school_partners["directed_partner_row_count"]:,} directed rows for
{school_partners["school_count"]:,} schools, including
{school_partners["outside_prior_global_edge_core_count"]:,} outside the former global edge core.
Profiles retain {school_profiles["profile_row_count"]:,} explicit school/corpus/window rows,
including unsupported or no-recent-activity states rather than fabricated values.

## 9. Sensitivity analysis

The required matrix contains {sensitivity["comparison_count"]} comparisons:
{sensitivity["completed_comparison_count"]} complete and
{sensitivity["unavailable_comparison_count"]} explicitly unavailable. A change of at least
{sensitivity["major_change_threshold"]:.0%} is flagged; {sensitivity["major_change_count"]}
comparisons meet that threshold. Sensitivity results never overwrite the primary result
(`primary_result_overwritten = {str(sensitivity["primary_result_overwritten"]).lower()}`). Exact
rows are stored in `data/processed/sensitivity_matrix.parquet` and the public dashboard extract.

## 10. Limitations

Topic decisions remain provisional and may include false positives or false negatives. Affiliation
metadata can be incomplete, hierarchy candidates require human review, and OpenAlex coverage is
not a census of all scholarship. Collaboration is co-authorship, not citation flow, knowledge
flow, research similarity, or causality. Version diagnostics identify
{versions["ambiguous_possible_family_count"]:,} ambiguous possible Work families. Sourced
coordinate coverage ranges from {map_summary["minimum_node_coordinate_coverage_share"]:.2%} to
{map_summary["maximum_node_coordinate_coverage_share"]:.2%}; no coordinate is invented. The
network dashboard is a thresholded view and must not be used to infer absence from a hidden edge.
No partial 2026 data are included; {trends["year_maximum"]} is the last complete calendar year.

`School` does not assert degree-granting status, programme availability, admissions likelihood,
teaching quality, cost, or student fit. School Finder and comparison ordering are not an admissions
ranking, and the release defines no universal institutional-quality score. Rolling values can be
sparse or incomplete and inherit publication-date precision limits. The partner index retains at
most {school_partners["top_k"]} partners per school, corpus, and latest rolling window; country and
macro-region ego summaries therefore aggregate retained partners rather than a complete global
edge matrix. The current school identity is byte-equivalent to organization identity; future
evidence-backed collapses require rebuilding dependent facts.

Optional layer analysis preserves three distinct network meanings. Co-authorship is an undirected
fractional collaboration layer; citation flow is directed from citing to cited institution; and
cosine Topic similarity is an undirected research-proximity layer over a deterministic annual
500-institution core. The multiplex comparison reports each layer separately and computes only
unweighted node and dyad presence overlap. Citation direction is ignored for dyad matching only.
No layer weights are combined, no composite score is defined, and Topic-layer overlap inherits the
annual-core coverage boundary.

## 11. Reproducibility

Run from the repository root:

```bash
# Historical scientific mode.
uv run python -m gisnet.cli run-pipeline \\
  --start-year {analysis["start_year"]} --end-year {analysis["end_year"]} \\
  --corpus all --hierarchy all --resume

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
```

The command validates hashes and provenance, skips valid stages, resumes incomplete downloads,
rebuilds only stale dependency branches, preserves valid raw pages, and prints the next recovery
command on failure. Generated static figures are:

- `figures/annual_region_trends.svg`, derived from `data/processed/trend_series_year.parquet`;
- `figures/view_comparison.svg`, derived from `data/processed/trend_series_year.parquet`;
- `figures/region_matrix.svg`, derived from
  `data/processed/collaboration_matrix_year.parquet`.

Visual encodings are stable across the dashboard and static figures. Macro-regions use a fixed,
color-vision-conscious categorical palette; time series also use dash patterns so color is not the
only cue. Share matrices and maps use one ordered Cividis scale. Missing cells remain distinct from
observed zero, network edge width stays constant, and exact values remain available in hover text,
accessible descriptions, or companion tables.

All reported figures are generated from processed data. The trend summary covers
{trends["trend_row_count"]} rows and reports `partial_years_included =
{str(trends["partial_years_included"]).lower()}`; matrix reconciliation failures are
{matrix["reconciliation_failure_count"]}.

## 12. Data ethics and geographic naming convention

Only public scholarly metadata and aggregate/thresholded derived tables are released. API keys,
raw response caches, and private local paths are excluded. Institution identifiers are retained to
support auditability; rankings should not be interpreted as measures of institutional quality.
The geographic convention is UN M49-style and is a technical analytical grouping, not a political
statement about sovereignty, borders, recognition, or affiliation. Missing geography remains
Unknown rather than being guessed.

## Evidence inventory

- Corpus: `data/reference/work_corpus_summary.json`
- Institutions: `data/reference/institution_master_summary.json`
- Edges: `data/reference/collaboration_edges_summary.json`
- Metrics: `data/reference/network_metrics_summary.json`
- Communities: `data/reference/community_detection_summary.json`
- Community continuity: `data/reference/community_continuity_summary.json`
- Sensitivity: `data/reference/sensitivity_summary.json`
- Reproducibility: `data/reference/reproducibility_validation.json`
- Publication-date QA: `data/reference/publication_date_qa_summary.json`
- Month and quarter facts: `data/reference/subannual_temporal_summary.json`
- Rolling 12/24/36-month facts: `data/reference/rolling_temporal_summary.json`
- School identity/index: `data/reference/school_identity_summary.json` and
  `data/reference/school_index_summary.json`
- School partners/profiles: `data/reference/school_partner_index_summary.json` and
  `data/reference/school_profile_summary.json`
- School-decision acceptance: `data/reference/school_decision_validation.json`
- Optional multiplex comparison: `data/reference/multiplex_comparison_summary.json`
- Figures: `data/reference/annual_trends_summary.json` and
  `data/reference/collaboration_matrix_summary.json`
"""


def write_methodology_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    report_path: str | Path,
    run_id: str,
    project_path: str | Path,
    topic_registry_path: str | Path,
    regions_path: str | Path,
    command: str,
) -> None:
    config_hashes = {
        "project": config_file_hash(project_path),
        "topic_registry": config_file_hash(topic_registry_path),
        "regions": config_file_hash(regions_path),
    }
    source_manifests = [f".agent/manifests/{path.stem}.json" for path in SUMMARY_PATHS.values()]
    DatasetManifest(
        dataset_name="methodology_report",
        created_at_utc=utc_timestamp(),
        run_id=run_id,
        git_commit=current_git_commit(),
        config_hashes=config_hashes,
        source_manifests=source_manifests,
        source_versions={"methodology_report": _STAGE_VERSION},
        row_count=1,
        column_count=1,
        primary_key=["report_sha256"],
        null_counts={"report_sha256": 0},
        checksum_sha256=file_sha256(report_path),
        command=command,
    ).write(".agent/manifests/methodology_report.json")
    write_json_artifact(
        path=summary_path,
        dataset_name="methodology_report_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions={"methodology_report": _STAGE_VERSION},
        source_manifests=source_manifests,
        command=command,
    )


def _validated_figures(summaries: dict[str, dict[str, Any]]) -> list[str]:
    expected = {
        "annual_region_trends.svg": summaries["trends"]["outputs"]["annual_region_trends_svg"],
        "view_comparison.svg": summaries["trends"]["outputs"]["view_comparison_svg"],
        "region_matrix.svg": summaries["matrix"]["outputs"]["region_matrix_svg"],
    }
    figures: list[str] = []
    for name, raw_path in expected.items():
        path = Path(str(raw_path))
        if path.name != name or not path.is_file():
            raise ValueError(f"validated processed-data figure is unavailable: {path}")
        figures.append(str(path))
    return figures


def _validate_report(report: str) -> None:
    missing = [heading for heading in REQUIRED_HEADINGS if heading not in report]
    if missing:
        raise ValueError(f"methodology report lacks required sections: {missing}")
    disclosures = (
        "No human review has occurred",
        "No partial 2026 data",
        "not a primary scientific metric",
        "All reported figures are generated from processed data",
        "Historical scientific mode",
        "Current school-decision mode",
        "passed all 13 of 13 acceptance checks",
        "not an admissions ranking",
        "no universal institutional-quality score",
    )
    missing_disclosures = [value for value in disclosures if value not in report]
    if missing_disclosures:
        raise ValueError(f"methodology report lacks disclosures: {missing_disclosures}")
    forbidden = ("OPENALEX_API_KEY=", "/home/", "github_pat_", "ghp_")
    found = [value for value in forbidden if value in report]
    if found:
        raise ValueError(f"methodology report contains forbidden private values: {found}")


def _load_mapping(path: str | Path) -> dict[str, Any]:
    value = load_yaml(path)
    if not isinstance(value, dict):
        raise ValueError(f"expected a mapping: {path}")
    return value


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _mapping(value: dict[str, Any], key: str) -> dict[str, Any]:
    nested = value.get(key)
    if not isinstance(nested, dict):
        raise ValueError(f"expected a mapping at {key}")
    return nested
