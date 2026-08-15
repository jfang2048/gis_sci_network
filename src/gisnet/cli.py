"""Single command-line entry point for the GIS collaboration pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

import duckdb
from pydantic import ValidationError

from gisnet.config import config_file_hash, load_project_config, load_yaml
from gisnet.corpus.build import build_work_corpus, write_corpus_artifacts
from gisnet.corpus.normalize import normalize_raw_works, write_normalization_artifacts
from gisnet.corpus.topics import (
    discover_candidate_topics,
    freeze_topic_registry,
    load_candidate_payload,
    load_discovery_terms,
    load_topic_decisions,
    sample_candidate_works,
    write_candidate_artifact,
    write_frozen_topic_registry,
    write_sample_artifacts,
)
from gisnet.corpus.validation import (
    build_boundary_sample,
    evaluate_boundary,
    load_known_positives,
    write_annotation_sheet,
    write_boundary_artifacts,
)
from gisnet.corpus.versions import build_version_diagnostics, write_version_artifacts
from gisnet.corpus.work_types import (
    load_work_type_policy,
    profile_work_types,
    write_work_type_profile,
)
from gisnet.geography import load_region_registry, write_mapping_csv
from gisnet.institutions.extract import extract_work_institutions, write_extraction_artifacts
from gisnet.institutions.geography import apply_institution_geography, write_geography_artifacts
from gisnet.institutions.hierarchy import build_institution_hierarchy, write_hierarchy_artifacts
from gisnet.institutions.master import build_institution_master, write_institution_master_artifacts
from gisnet.institutions.overrides import InstitutionOverrideRegistry
from gisnet.institutions.types import (
    load_institution_type_policy,
    profile_institution_types,
    write_institution_type_profile,
)
from gisnet.network.communities import build_annual_communities, write_community_artifacts
from gisnet.network.continuity import build_community_continuity, write_continuity_artifacts
from gisnet.network.edges import build_collaboration_edges, write_edge_artifacts
from gisnet.network.flows import build_geographic_flows, write_flow_artifacts
from gisnet.network.graphs import build_annual_graph_catalogue, write_graph_artifacts
from gisnet.network.intensity import build_edge_intensity, write_intensity_artifacts
from gisnet.network.metrics import build_network_metrics, write_metric_artifacts
from gisnet.network.outputs import build_institution_outputs, write_output_artifacts
from gisnet.network.work_institutions import (
    build_normalized_work_institutions,
    write_work_institution_artifacts,
)
from gisnet.openalex.cache import RawResponseCache
from gisnet.openalex.client import (
    AuthenticationError,
    NetworkError,
    OpenAlexClient,
    OpenAlexError,
    RateLimitError,
    ResponseError,
)
from gisnet.openalex.downloader import (
    execute_download_plan,
    load_download_plan,
    write_download_status_manifest,
)
from gisnet.openalex.planner import (
    build_query_plan,
    load_download_planner_config,
    preview_query_plan,
    validate_query_plan,
    write_query_plan,
)
from gisnet.pipeline import DEFAULT_STAGES, run_pipeline, write_pipeline_artifact
from gisnet.reporting.data_dictionary import (
    build_public_data_dictionary,
    write_data_dictionary_artifacts,
)
from gisnet.reporting.methodology import build_methodology_report, write_methodology_artifacts
from gisnet.ror.enrich import enrich_institutions_with_ror, write_ror_artifacts
from gisnet.secrets import get_openalex_api_key
from gisnet.state import (
    BacklogStore,
    InvalidStateError,
    ProjectStateStore,
    RunLock,
    TaskStatus,
    make_run_id,
)
from gisnet.validation.audit import build_top_entity_audit, write_audit_artifacts
from gisnet.validation.edges import validate_edge_arithmetic, write_edge_validation_artifact
from gisnet.validation.reproducibility import (
    verify_reproducibility,
    write_reproducibility_artifact,
)
from gisnet.validation.sensitivity import build_sensitivity_matrix, write_sensitivity_artifacts
from gisnet.visualization.dashboard_data import build_dashboard_bundle, write_dashboard_artifact
from gisnet.visualization.layout import build_fixed_layout, write_layout_artifacts
from gisnet.visualization.map_data import build_map_data, write_map_artifacts
from gisnet.visualization.matrix import build_collaboration_matrix, write_matrix_artifacts
from gisnet.visualization.network_view import build_network_view, write_network_view_artifacts
from gisnet.visualization.trends import build_annual_trends, write_trend_artifacts

_NOT_IMPLEMENTED_COMMANDS: tuple[str, ...] = ()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m gisnet.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser("status", help="show durable project and task state")
    status.add_argument("--state", default=".agent/state.json", type=Path)
    status.add_argument("--backlog", default=".agent/backlog.json", type=Path)
    status.add_argument("--config", default="config/project.yml", type=Path)
    status.add_argument("--json", action="store_true", dest="as_json")
    status.set_defaults(handler=_status)

    next_task = subparsers.add_parser("next-task", help="show the next schedulable backlog task")
    next_task.add_argument("--backlog", default=".agent/backlog.json", type=Path)
    next_task.set_defaults(handler=_next_task)

    check_env = subparsers.add_parser("check-env", help="validate secret presence and API access")
    check_env.add_argument("--config", default="config/project.yml", type=Path)
    check_env.add_argument(
        "--offline", action="store_true", help="check key presence without making a request"
    )
    check_env.set_defaults(handler=_check_env)

    regions = subparsers.add_parser(
        "validate-regions", help="validate the frozen geographic registry"
    )
    regions.add_argument("--config", default="config/project.yml", type=Path)
    regions.add_argument(
        "--write-csv", default=None, type=Path, help="atomically regenerate the reference CSV"
    )
    regions.set_defaults(handler=_validate_regions)

    institution_types = subparsers.add_parser(
        "profile-institution-types", help="profile and map current OpenAlex institution types"
    )
    _add_pipeline_arguments(institution_types)
    institution_types.add_argument("--policy", default="config/institution_types.yml", type=Path)
    institution_types.add_argument(
        "--output", default="data/reference/institution_type_profile.json", type=Path
    )
    institution_types.set_defaults(handler=_profile_institution_types)

    discover = subparsers.add_parser(
        "discover-topics", help="search OpenAlex for evidence-ranked candidate Topics"
    )
    _add_pipeline_arguments(discover)
    discover.add_argument("--terms", default="config/discovery_terms.yml", type=Path)
    discover.add_argument("--output", default="data/reference/topic_candidates.json", type=Path)
    discover.add_argument("--max-results-per-term", default=5, type=int)
    discover.set_defaults(handler=_discover_topics)

    sample = subparsers.add_parser(
        "sample-topic-works", help="retrieve deterministic work evidence for every candidate"
    )
    _add_pipeline_arguments(sample)
    sample.add_argument("--candidates", default="data/reference/topic_candidates.json", type=Path)
    sample.add_argument("--output", default="data/interim/topic_work_samples.json", type=Path)
    sample.add_argument("--report", default="outputs/reports/topic_review.md", type=Path)
    sample.set_defaults(handler=_sample_topic_works)

    freeze = subparsers.add_parser(
        "freeze-topics", help="merge candidate metadata, sample evidence, and reviewed decisions"
    )
    _add_pipeline_arguments(freeze)
    freeze.add_argument("--candidates", default="data/reference/topic_candidates.json", type=Path)
    freeze.add_argument("--samples", default="data/interim/topic_work_samples.json", type=Path)
    freeze.add_argument("--decisions", default="config/topic_decisions.yml", type=Path)
    freeze.add_argument("--output", default="config/topic_registry.yml", type=Path)
    freeze.set_defaults(handler=_freeze_topics)

    boundary = subparsers.add_parser(
        "validate-corpus-boundary",
        help="build a deterministic annotation sample and supported boundary metrics",
    )
    _add_pipeline_arguments(boundary)
    boundary.add_argument("--registry", default="config/topic_registry.yml", type=Path)
    boundary.add_argument("--samples", default="data/interim/topic_work_samples.json", type=Path)
    boundary.add_argument("--known-positives", default="config/known_positive_works.csv", type=Path)
    boundary.add_argument(
        "--annotations", default="data/reference/corpus_boundary_annotations.csv", type=Path
    )
    boundary.add_argument(
        "--metrics", default="data/reference/corpus_boundary_validation.json", type=Path
    )
    boundary.add_argument(
        "--report", default="outputs/reports/corpus_boundary_validation.md", type=Path
    )
    boundary.add_argument("--per-group", default=12, type=int)
    boundary.set_defaults(handler=_validate_corpus_boundary)

    work_types = subparsers.add_parser(
        "profile-work-types", help="profile selected-corpus work types and inspection samples"
    )
    _add_pipeline_arguments(work_types)
    work_types.add_argument("--policy", default="config/work_types.yml", type=Path)
    work_types.add_argument("--registry", default="config/topic_registry.yml", type=Path)
    work_types.add_argument("--output", default="data/reference/work_type_profile.json", type=Path)
    work_types.set_defaults(handler=_profile_work_types)

    plan_download = subparsers.add_parser(
        "plan-download", help="build and preview deterministic OpenAlex work-query shards"
    )
    _add_pipeline_arguments(plan_download)
    plan_download.add_argument("--download-config", default="config/download.yml", type=Path)
    plan_download.add_argument("--registry", default="config/topic_registry.yml", type=Path)
    plan_download.add_argument("--regions", default="config/regions.yml", type=Path)
    plan_download.add_argument("--output", default="data/reference/download_plan.json", type=Path)
    plan_download.add_argument(
        "--skip-preview", action="store_true", help="write a validated plan without API counts"
    )
    plan_download.set_defaults(handler=_plan_download)

    download_works = subparsers.add_parser(
        "download-works", help="execute the saved plan into validated raw cache pages"
    )
    _add_pipeline_arguments(download_works)
    download_works.add_argument("--plan", default="data/reference/download_plan.json", type=Path)
    download_works.add_argument(
        "--status", default="data/reference/raw_works_download_status.json", type=Path
    )
    download_works.add_argument("--download-config", default="config/download.yml", type=Path)
    download_works.add_argument("--max-queries", type=int)
    download_works.add_argument("--workers", type=int, default=4)
    download_works.set_defaults(handler=_download_works)

    normalize_works = subparsers.add_parser(
        "normalize-works", help="deduplicate raw Work pages into validated Parquet tables"
    )
    _add_pipeline_arguments(normalize_works)
    normalize_works.add_argument("--plan", default="data/reference/download_plan.json", type=Path)
    normalize_works.add_argument("--registry", default="config/topic_registry.yml", type=Path)
    normalize_works.add_argument(
        "--raw-checkpoints", default=".agent/checkpoints/openalex", type=Path
    )
    normalize_works.add_argument(
        "--staging", default="data/interim/normalize_works.duckdb", type=Path
    )
    normalize_works.add_argument(
        "--checkpoint", default=".agent/checkpoints/normalize_works.json", type=Path
    )
    normalize_works.add_argument("--output-directory", default="data/processed", type=Path)
    normalize_works.add_argument(
        "--summary", default="data/reference/works_normalization_summary.json", type=Path
    )
    normalize_works.add_argument("--batch-size", default=5000, type=int)
    normalize_works.add_argument("--duckdb-memory-limit", default="6GB")
    normalize_works.add_argument("--duckdb-threads", default=1, type=int)
    normalize_works.set_defaults(handler=_normalize_works)

    extract_institutions = subparsers.add_parser(
        "extract-institutions", help="extract distinct institution assertions from Work authorships"
    )
    _add_pipeline_arguments(extract_institutions)
    extract_institutions.add_argument("--works", default="data/processed/works.parquet", type=Path)
    extract_institutions.add_argument(
        "--extracted",
        default="data/processed/work_institutions_extracted.parquet",
        type=Path,
    )
    extract_institutions.add_argument(
        "--unresolved",
        default="data/processed/work_institutions_unresolved.parquet",
        type=Path,
    )
    extract_institutions.add_argument(
        "--summary", default="data/reference/institution_extraction_summary.json", type=Path
    )
    extract_institutions.add_argument("--batch-size", default=2000, type=int)
    extract_institutions.set_defaults(handler=_extract_institutions)

    build_institutions = subparsers.add_parser(
        "build-institutions", help="build a stable-ID institution master and metadata audit"
    )
    _add_pipeline_arguments(build_institutions)
    build_institutions.add_argument(
        "--extracted",
        default="data/processed/work_institutions_extracted.parquet",
        type=Path,
    )
    build_institutions.add_argument(
        "--institution-types", default="config/institution_types.yml", type=Path
    )
    build_institutions.add_argument(
        "--institutions", default="data/processed/institutions.parquet", type=Path
    )
    build_institutions.add_argument(
        "--qa", default="data/processed/institution_metadata_qa.parquet", type=Path
    )
    build_institutions.add_argument(
        "--summary", default="data/reference/institution_master_summary.json", type=Path
    )
    build_institutions.add_argument("--lookup-batch-size", default=50, type=int)
    build_institutions.add_argument("--offline", action="store_true")
    build_institutions.set_defaults(handler=_build_institutions)

    apply_geography = subparsers.add_parser(
        "apply-geography", help="apply frozen country-to-region conventions to institutions"
    )
    _add_pipeline_arguments(apply_geography)
    apply_geography.add_argument(
        "--institutions", default="data/processed/institutions.parquet", type=Path
    )
    apply_geography.add_argument("--regions", default="config/regions.yml", type=Path)
    apply_geography.add_argument(
        "--institution-overrides", default="config/institution_overrides.csv", type=Path
    )
    apply_geography.add_argument(
        "--output", default="data/processed/institutions_geographic.parquet", type=Path
    )
    apply_geography.add_argument(
        "--qa", default="data/processed/institution_geography_qa.parquet", type=Path
    )
    apply_geography.add_argument(
        "--summary", default="data/reference/institution_geography_summary.json", type=Path
    )
    apply_geography.set_defaults(handler=_apply_geography)

    enrich_institutions = subparsers.add_parser(
        "enrich-institutions", help="optionally enrich stable institution IDs from ROR"
    )
    _add_pipeline_arguments(enrich_institutions)
    enrich_institutions.add_argument(
        "--institutions", default="data/processed/institutions_geographic.parquet", type=Path
    )
    enrich_institutions.add_argument(
        "--output", default="data/processed/institutions_ror.parquet", type=Path
    )
    enrich_institutions.add_argument(
        "--qa", default="data/processed/institution_ror_qa.parquet", type=Path
    )
    enrich_institutions.add_argument(
        "--summary", default="data/reference/institution_ror_summary.json", type=Path
    )
    enrich_institutions.add_argument("--ror-cache", default="data/cache/ror", type=Path)
    enrich_institutions.add_argument(
        "--ror-mode", choices=("cache", "api", "dump"), default="cache"
    )
    enrich_institutions.add_argument("--ror-dump", type=Path)
    enrich_institutions.add_argument("--ror-dump-version")
    enrich_institutions.add_argument("--max-ror-lookups", type=int, default=0)
    enrich_institutions.set_defaults(handler=_enrich_institutions)

    build_hierarchy = subparsers.add_parser(
        "build-hierarchy", help="build comparable organization and explicit-rule umbrella views"
    )
    _add_pipeline_arguments(build_hierarchy)
    build_hierarchy.add_argument(
        "--institutions", default="data/processed/institutions_ror.parquet", type=Path
    )
    build_hierarchy.add_argument(
        "--institution-overrides", default="config/institution_overrides.csv", type=Path
    )
    build_hierarchy.add_argument(
        "--output", default="data/processed/institution_hierarchy.parquet", type=Path
    )
    build_hierarchy.add_argument(
        "--audit", default="data/processed/institution_canonicalization_audit.parquet", type=Path
    )
    build_hierarchy.add_argument(
        "--candidates", default="data/processed/institution_hierarchy_candidates.parquet", type=Path
    )
    build_hierarchy.add_argument(
        "--summary", default="data/reference/institution_hierarchy_summary.json", type=Path
    )
    build_hierarchy.set_defaults(handler=_build_hierarchy)

    diagnose_versions = subparsers.add_parser(
        "diagnose-versions", help="build exact-DOI and conservative possible-version diagnostics"
    )
    _add_pipeline_arguments(diagnose_versions)
    diagnose_versions.add_argument("--works", default="data/processed/works.parquet", type=Path)
    diagnose_versions.add_argument(
        "--output", default="data/processed/work_version_diagnostics.parquet", type=Path
    )
    diagnose_versions.add_argument(
        "--duplicate-dois",
        default="data/processed/work_duplicate_doi_diagnostics.parquet",
        type=Path,
    )
    diagnose_versions.add_argument(
        "--ambiguous",
        default="data/processed/work_ambiguous_version_candidates.parquet",
        type=Path,
    )
    diagnose_versions.add_argument(
        "--summary", default="data/reference/work_version_diagnostics_summary.json", type=Path
    )
    diagnose_versions.add_argument("--duckdb-memory-limit", default="4GB")
    diagnose_versions.add_argument("--duckdb-threads", default=1, type=int)
    diagnose_versions.set_defaults(handler=_diagnose_versions)

    build_corpus = subparsers.add_parser(
        "build-corpus", help="materialize Strict, Broad, and sensitivity Work memberships"
    )
    _add_pipeline_arguments(build_corpus)
    build_corpus.add_argument("--works", default="data/processed/works.parquet", type=Path)
    build_corpus.add_argument(
        "--work-topics", default="data/processed/work_topics.parquet", type=Path
    )
    build_corpus.add_argument(
        "--versions", default="data/processed/work_version_diagnostics.parquet", type=Path
    )
    build_corpus.add_argument("--work-types", default="config/work_types.yml", type=Path)
    build_corpus.add_argument("--topic-registry", default="config/topic_registry.yml", type=Path)
    build_corpus.add_argument("--output", default="data/processed/work_corpus.parquet", type=Path)
    build_corpus.add_argument(
        "--annual-counts", default="data/processed/corpus_annual_counts.parquet", type=Path
    )
    build_corpus.add_argument(
        "--topic-family-counts",
        default="data/processed/corpus_topic_family_counts.parquet",
        type=Path,
    )
    build_corpus.add_argument(
        "--summary", default="data/reference/work_corpus_summary.json", type=Path
    )
    build_corpus.add_argument("--duckdb-memory-limit", default="4GB")
    build_corpus.add_argument("--duckdb-threads", default=1, type=int)
    build_corpus.set_defaults(handler=_build_corpus)

    build_work_institutions = subparsers.add_parser(
        "build-work-institutions",
        help="join corpus and hierarchy into normalized Work institutions",
    )
    _add_pipeline_arguments(build_work_institutions)
    build_work_institutions.add_argument(
        "--extracted", default="data/processed/work_institutions_extracted.parquet", type=Path
    )
    build_work_institutions.add_argument(
        "--work-corpus", default="data/processed/work_corpus.parquet", type=Path
    )
    build_work_institutions.add_argument(
        "--institutions", default="data/processed/institutions_ror.parquet", type=Path
    )
    build_work_institutions.add_argument(
        "--hierarchy-map", default="data/processed/institution_hierarchy.parquet", type=Path
    )
    build_work_institutions.add_argument(
        "--output", default="data/processed/work_institutions.parquet", type=Path
    )
    build_work_institutions.add_argument(
        "--summary", default="data/reference/work_institutions_summary.json", type=Path
    )
    build_work_institutions.add_argument("--duckdb-memory-limit", default="4GB")
    build_work_institutions.add_argument("--duckdb-threads", default=1, type=int)
    build_work_institutions.set_defaults(handler=_build_work_institutions)

    build_edges = subparsers.add_parser(
        "build-edges", help="build per-Work pairs and annual full/fractional edges"
    )
    _add_pipeline_arguments(build_edges)
    build_edges.add_argument(
        "--work-institutions", default="data/processed/work_institutions.parquet", type=Path
    )
    build_edges.add_argument("--work-edges", default="data/processed/work_edges.parquet", type=Path)
    build_edges.add_argument("--output", default="data/processed/edges_year.parquet", type=Path)
    build_edges.add_argument(
        "--diagnostics", default="data/processed/edge_work_diagnostics.parquet", type=Path
    )
    build_edges.add_argument(
        "--summary", default="data/reference/collaboration_edges_summary.json", type=Path
    )
    build_edges.add_argument("--duckdb-memory-limit", default="4GB")
    build_edges.add_argument("--duckdb-threads", default=1, type=int)
    build_edges.set_defaults(handler=_build_edges)

    build_outputs = subparsers.add_parser(
        "build-outputs", help="build annual institutional full/fractional output tables"
    )
    _add_pipeline_arguments(build_outputs)
    build_outputs.add_argument(
        "--work-institutions", default="data/processed/work_institutions.parquet", type=Path
    )
    build_outputs.add_argument(
        "--output", default="data/processed/institution_outputs_year.parquet", type=Path
    )
    build_outputs.add_argument(
        "--reconciliation",
        default="data/processed/institution_output_reconciliation.parquet",
        type=Path,
    )
    build_outputs.add_argument(
        "--summary", default="data/reference/institution_outputs_summary.json", type=Path
    )
    build_outputs.add_argument("--duckdb-memory-limit", default="4GB")
    build_outputs.add_argument("--duckdb-threads", default=1, type=int)
    build_outputs.set_defaults(handler=_build_outputs)

    build_flows = subparsers.add_parser(
        "build-region-flows", help="aggregate institution pairs to region, subregion, and country"
    )
    _add_pipeline_arguments(build_flows)
    build_flows.add_argument("--work-edges", default="data/processed/work_edges.parquet", type=Path)
    build_flows.add_argument(
        "--output", default="data/processed/region_flows_year.parquet", type=Path
    )
    build_flows.add_argument(
        "--reconciliation",
        default="data/processed/region_flow_reconciliation.parquet",
        type=Path,
    )
    build_flows.add_argument(
        "--summary", default="data/reference/region_flows_summary.json", type=Path
    )
    build_flows.add_argument("--duckdb-memory-limit", default="4GB")
    build_flows.add_argument("--duckdb-threads", default=1, type=int)
    build_flows.set_defaults(handler=_build_region_flows)

    validate = subparsers.add_parser("validate", help="run stored-data arithmetic invariants")
    _add_pipeline_arguments(validate)
    validate.add_argument("--work-edges", default="data/processed/work_edges.parquet", type=Path)
    validate.add_argument("--edges", default="data/processed/edges_year.parquet", type=Path)
    validate.add_argument(
        "--edge-diagnostics", default="data/processed/edge_work_diagnostics.parquet", type=Path
    )
    validate.add_argument(
        "--output", default="data/reference/edge_arithmetic_validation.json", type=Path
    )
    validate.set_defaults(handler=_validate_outputs)

    reproducibility = subparsers.add_parser(
        "verify-reproducibility", help="verify core dataset checksums and clean recovery state"
    )
    _add_pipeline_arguments(reproducibility)
    reproducibility.add_argument(
        "--output", default="data/reference/reproducibility_validation.json", type=Path
    )
    reproducibility.set_defaults(handler=_verify_reproducibility)

    intensity = subparsers.add_parser(
        "compute-edge-intensity", help="compute normalized edge intensity and trailing persistence"
    )
    _add_pipeline_arguments(intensity)
    intensity.add_argument("--edges", default="data/processed/edges_year.parquet", type=Path)
    intensity.add_argument(
        "--institution-outputs",
        default="data/processed/institution_outputs_year.parquet",
        type=Path,
    )
    intensity.add_argument(
        "--output", default="data/processed/edges_metrics_year.parquet", type=Path
    )
    intensity.add_argument(
        "--summary", default="data/reference/edge_intensity_summary.json", type=Path
    )
    intensity.add_argument("--duckdb-memory-limit", default="4GB")
    intensity.add_argument("--duckdb-threads", default=1, type=int)
    intensity.set_defaults(handler=_compute_edge_intensity)

    graphs = subparsers.add_parser(
        "build-graphs", help="build annual weighted undirected graph catalogues"
    )
    _add_pipeline_arguments(graphs)
    graphs.add_argument("--edges", default="data/processed/edges_year.parquet", type=Path)
    graphs.add_argument(
        "--institution-outputs",
        default="data/processed/institution_outputs_year.parquet",
        type=Path,
    )
    graphs.add_argument("--output", default="data/processed/graph_summary_year.parquet", type=Path)
    graphs.add_argument(
        "--catalogue", default="data/reference/annual_graph_catalogue.json", type=Path
    )
    graphs.add_argument("--duckdb-memory-limit", default="4GB")
    graphs.add_argument("--duckdb-threads", default=1, type=int)
    graphs.set_defaults(handler=_build_graphs)

    metrics = subparsers.add_parser(
        "compute-metrics", help="compute annual node centrality and graph metrics"
    )
    _add_pipeline_arguments(metrics)
    metrics.add_argument("--edges", default="data/processed/edges_year.parquet", type=Path)
    metrics.add_argument(
        "--institution-outputs",
        default="data/processed/institution_outputs_year.parquet",
        type=Path,
    )
    metrics.add_argument("--nodes-output", default="data/processed/nodes_year.parquet", type=Path)
    metrics.add_argument(
        "--graphs-output", default="data/processed/graph_metrics_year.parquet", type=Path
    )
    metrics.add_argument(
        "--summary", default="data/reference/network_metrics_summary.json", type=Path
    )
    metrics.add_argument("--duckdb-memory-limit", default="4GB")
    metrics.add_argument("--duckdb-threads", default=1, type=int)
    metrics.set_defaults(handler=_compute_metrics)

    communities = subparsers.add_parser(
        "detect-communities", help="detect deterministic annual weighted Leiden communities"
    )
    _add_pipeline_arguments(communities)
    communities.add_argument("--edges", default="data/processed/edges_year.parquet", type=Path)
    communities.add_argument("--nodes", default="data/processed/nodes_year.parquet", type=Path)
    communities.add_argument(
        "--output", default="data/processed/communities_year.parquet", type=Path
    )
    communities.add_argument(
        "--sensitivity",
        default="data/processed/community_sensitivity_year.parquet",
        type=Path,
    )
    communities.add_argument(
        "--summary", default="data/reference/community_detection_summary.json", type=Path
    )
    communities.add_argument("--duckdb-memory-limit", default="4GB")
    communities.add_argument("--duckdb-threads", default=1, type=int)
    communities.set_defaults(handler=_detect_communities)

    continuity = subparsers.add_parser(
        "match-communities", help="match adjacent-year communities and assign continuity IDs"
    )
    _add_pipeline_arguments(continuity)
    continuity.add_argument(
        "--communities", default="data/processed/communities_year.parquet", type=Path
    )
    continuity.add_argument(
        "--continuity-output",
        default="data/processed/community_continuity_year.parquet",
        type=Path,
    )
    continuity.add_argument(
        "--transitions-output",
        default="data/processed/community_transitions_year.parquet",
        type=Path,
    )
    continuity.add_argument(
        "--summary", default="data/reference/community_continuity_summary.json", type=Path
    )
    continuity.add_argument("--confident-match-threshold", default=0.25, type=float)
    continuity.add_argument("--event-overlap-threshold", default=0.10, type=float)
    continuity.add_argument("--duckdb-memory-limit", default="2GB")
    continuity.add_argument("--duckdb-threads", default=1, type=int)
    continuity.set_defaults(handler=_match_communities)

    layout = subparsers.add_parser(
        "build-layout", help="build one fixed aggregate network layout for all annual views"
    )
    _add_pipeline_arguments(layout)
    layout.add_argument("--edges", default="data/processed/edges_year.parquet", type=Path)
    layout.add_argument("--nodes", default="data/processed/nodes_year.parquet", type=Path)
    layout.add_argument("--output", default="data/processed/network_layout.parquet", type=Path)
    layout.add_argument(
        "--summary", default="data/reference/network_layout_summary.json", type=Path
    )
    layout.add_argument("--core-size", default=500, type=int)
    layout.add_argument("--duckdb-memory-limit", default="4GB")
    layout.add_argument("--duckdb-threads", default=1, type=int)
    layout.set_defaults(handler=_build_layout)

    audit = subparsers.add_parser(
        "audit-top-entities", help="audit top institutions and cross-region edges"
    )
    _add_pipeline_arguments(audit)
    audit.add_argument("--nodes", default="data/processed/nodes_year.parquet", type=Path)
    audit.add_argument("--edges", default="data/processed/edges_metrics_year.parquet", type=Path)
    audit.add_argument(
        "--work-institutions",
        default="data/processed/work_institutions_extracted.parquet",
        type=Path,
    )
    audit.add_argument("--institutions", default="data/processed/institutions.parquet", type=Path)
    audit.add_argument(
        "--hierarchy-path",
        default="data/processed/institution_hierarchy.parquet",
        type=Path,
    )
    audit.add_argument(
        "--institution-output",
        default="data/processed/top_institution_audit.parquet",
        type=Path,
    )
    audit.add_argument("--edge-output", default="data/processed/top_edge_audit.parquet", type=Path)
    audit.add_argument(
        "--summary", default="data/reference/top_entity_audit_summary.json", type=Path
    )
    audit.add_argument("--sample-size", default=50, type=int)
    audit.add_argument("--duckdb-memory-limit", default="4GB")
    audit.add_argument("--duckdb-threads", default=1, type=int)
    audit.set_defaults(handler=_audit_top_entities)

    sensitivity = subparsers.add_parser(
        "run-sensitivity", help="run the required eight-comparison sensitivity matrix"
    )
    _add_pipeline_arguments(sensitivity)
    sensitivity.add_argument(
        "--graph-metrics", default="data/processed/graph_metrics_year.parquet", type=Path
    )
    sensitivity.add_argument("--edges", default="data/processed/edges_year.parquet", type=Path)
    sensitivity.add_argument("--work-edges", default="data/processed/work_edges.parquet", type=Path)
    sensitivity.add_argument("--nodes", default="data/processed/nodes_year.parquet", type=Path)
    sensitivity.add_argument(
        "--work-institutions",
        default="data/processed/work_institutions.parquet",
        type=Path,
    )
    sensitivity.add_argument(
        "--work-corpus", default="data/processed/work_corpus.parquet", type=Path
    )
    sensitivity.add_argument("--topic-registry", default="config/topic_registry.yml", type=Path)
    sensitivity.add_argument(
        "--output", default="data/processed/sensitivity_matrix.parquet", type=Path
    )
    sensitivity.add_argument(
        "--scope-output",
        default="data/processed/institution_scope_sensitivity_year.parquet",
        type=Path,
    )
    sensitivity.add_argument(
        "--summary", default="data/reference/sensitivity_summary.json", type=Path
    )
    sensitivity.add_argument("--duckdb-memory-limit", default="4GB")
    sensitivity.add_argument("--duckdb-threads", default=1, type=int)
    sensitivity.set_defaults(handler=_run_sensitivity)

    figures = subparsers.add_parser(
        "build-figures", help="build annual regional trend tables and static SVG figures"
    )
    _add_pipeline_arguments(figures)
    figures.add_argument("--flows", default="data/processed/region_flows_year.parquet", type=Path)
    figures.add_argument(
        "--graph-metrics", default="data/processed/graph_metrics_year.parquet", type=Path
    )
    figures.add_argument("--output", default="data/processed/trend_series_year.parquet", type=Path)
    figures.add_argument("--trend-figure", default="figures/annual_region_trends.svg", type=Path)
    figures.add_argument("--comparison-figure", default="figures/view_comparison.svg", type=Path)
    figures.add_argument(
        "--summary", default="data/reference/annual_trends_summary.json", type=Path
    )
    figures.add_argument("--duckdb-memory-limit", default="4GB")
    figures.add_argument("--duckdb-threads", default=1, type=int)
    figures.set_defaults(handler=_build_figures)

    matrix = subparsers.add_parser(
        "build-matrix", help="build stable region matrices and geographic drilldown tables"
    )
    _add_pipeline_arguments(matrix)
    matrix.add_argument("--flows", default="data/processed/region_flows_year.parquet", type=Path)
    matrix.add_argument(
        "--output", default="data/processed/collaboration_matrix_year.parquet", type=Path
    )
    matrix.add_argument("--figure", default="figures/region_matrix.svg", type=Path)
    matrix.add_argument(
        "--summary", default="data/reference/collaboration_matrix_summary.json", type=Path
    )
    matrix.add_argument("--duckdb-memory-limit", default="4GB")
    matrix.add_argument("--duckdb-threads", default=1, type=int)
    matrix.set_defaults(handler=_build_matrix)

    map_data = subparsers.add_parser(
        "build-map-data", help="build coordinate-grounded thresholded geographic map data"
    )
    _add_pipeline_arguments(map_data)
    map_data.add_argument("--nodes", default="data/processed/nodes_year.parquet", type=Path)
    map_data.add_argument("--edges", default="data/processed/edges_metrics_year.parquet", type=Path)
    map_data.add_argument(
        "--nodes-output", default="data/processed/map_nodes_year.parquet", type=Path
    )
    map_data.add_argument(
        "--edges-output", default="data/processed/map_edges_year.parquet", type=Path
    )
    map_data.add_argument(
        "--coverage-output", default="data/processed/map_coverage_year.parquet", type=Path
    )
    map_data.add_argument(
        "--summary", default="data/reference/geographic_map_summary.json", type=Path
    )
    map_data.add_argument("--edge-limit", default=500, type=int)
    map_data.add_argument("--node-limit", default=1000, type=int)
    map_data.add_argument("--duckdb-memory-limit", default="4GB")
    map_data.add_argument("--duckdb-threads", default=1, type=int)
    map_data.set_defaults(handler=_build_map_data)

    network_view = subparsers.add_parser(
        "build-network-view", help="build fixed-coordinate institutional network view data"
    )
    _add_pipeline_arguments(network_view)
    network_view.add_argument("--nodes", default="data/processed/nodes_year.parquet", type=Path)
    network_view.add_argument(
        "--edges", default="data/processed/edges_metrics_year.parquet", type=Path
    )
    network_view.add_argument(
        "--communities", default="data/processed/communities_year.parquet", type=Path
    )
    network_view.add_argument(
        "--layout", default="data/processed/network_layout.parquet", type=Path
    )
    network_view.add_argument(
        "--nodes-output",
        default="data/processed/network_view_nodes_year.parquet",
        type=Path,
    )
    network_view.add_argument(
        "--edges-output",
        default="data/processed/network_view_edges_year.parquet",
        type=Path,
    )
    network_view.add_argument(
        "--accessibility-output",
        default="data/processed/network_accessibility_year.parquet",
        type=Path,
    )
    network_view.add_argument(
        "--summary", default="data/reference/network_view_summary.json", type=Path
    )
    network_view.add_argument("--edge-limit", default=1000, type=int)
    network_view.add_argument("--duckdb-memory-limit", default="4GB")
    network_view.add_argument("--duckdb-threads", default=1, type=int)
    network_view.set_defaults(handler=_build_network_view)

    dashboard_data = subparsers.add_parser(
        "build-dashboard-data", help="build the compact processed-data-only public dashboard bundle"
    )
    _add_pipeline_arguments(dashboard_data)
    dashboard_data.add_argument("--output-directory", default="dashboard/data", type=Path)
    dashboard_data.add_argument("--metadata", default="dashboard/data/metadata.json", type=Path)
    dashboard_data.add_argument(
        "--summary", default="data/reference/dashboard_bundle_summary.json", type=Path
    )
    dashboard_data.add_argument("--duckdb-memory-limit", default="4GB")
    dashboard_data.add_argument("--duckdb-threads", default=1, type=int)
    dashboard_data.set_defaults(handler=_build_dashboard_data)

    pipeline = subparsers.add_parser(
        "run-pipeline", help="validate and resume the complete manifest-aware pipeline"
    )
    _add_pipeline_arguments(pipeline)
    pipeline.add_argument("--output", default="data/reference/pipeline_run_summary.json", type=Path)
    pipeline.set_defaults(handler=_run_pipeline)

    report = subparsers.add_parser(
        "report", help="generate the validated methodology report from processed artifacts"
    )
    _add_pipeline_arguments(report)
    report.add_argument("--topic-registry", default="config/topic_registry.yml", type=Path)
    report.add_argument("--regions", default="config/regions.yml", type=Path)
    report.add_argument("--output", default="outputs/reports/methodology.md", type=Path)
    report.add_argument(
        "--summary", default="data/reference/methodology_report_summary.json", type=Path
    )
    report.set_defaults(handler=_build_methodology_report)

    dictionary = subparsers.add_parser(
        "build-data-dictionary",
        help="document every public table column and its manifest provenance",
    )
    _add_pipeline_arguments(dictionary)
    dictionary.add_argument("--data-directory", default="dashboard/data", type=Path)
    dictionary.add_argument("--metadata", default="dashboard/data/metadata.json", type=Path)
    dictionary.add_argument(
        "--dictionary", default="data/reference/data_dictionary.json", type=Path
    )
    dictionary.add_argument("--report", default="outputs/reports/data_dictionary.md", type=Path)
    dictionary.add_argument(
        "--summary", default="data/reference/data_dictionary_summary.json", type=Path
    )
    dictionary.set_defaults(handler=_build_data_dictionary)

    for name in _NOT_IMPLEMENTED_COMMANDS:
        command = subparsers.add_parser(name, help="reserved by the execution backlog")
        _add_pipeline_arguments(command)
        command.set_defaults(handler=_not_implemented)
    return parser


def _add_pipeline_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", default="config/project.yml", type=Path)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--corpus", choices=("strict", "broad", "all"), default="all")
    parser.add_argument("--hierarchy", choices=("organization", "umbrella", "all"), default="all")


def _status(args: argparse.Namespace) -> int:
    try:
        state = ProjectStateStore(args.state).load()
        backlog = BacklogStore(args.backlog).load()
        config = load_project_config(args.config)
    except (InvalidStateError, OSError, ValidationError, ValueError) as exc:
        print(f"State validation failed: {exc}", file=sys.stderr)
        return 2
    counts = Counter(task.status.value for task in backlog.tasks)
    payload = {
        "project_version": state.project_version,
        "analysis_years": [config.analysis.start_year, config.analysis.end_year],
        "active_run_id": state.active_run_id,
        "current_task_id": state.current_task_id,
        "last_successful_run_id": state.last_successful_run_id,
        "completed_task_count": len(state.completed_task_ids),
        "blocked_task_ids": state.blocked_task_ids,
        "task_counts": dict(sorted(counts.items())),
        "project_config_hash": config_file_hash(args.config),
    }
    if args.as_json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"GIS collaboration network {payload['project_version']}")
        print(f"Analysis window: {config.analysis.start_year}-{config.analysis.end_year}")
        print(f"Active run: {state.active_run_id or 'none'}")
        print(f"Current task: {state.current_task_id or 'none'}")
        print("Task states: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
        print(f"Project config hash: {payload['project_config_hash']}")
    return 0


def _next_task(args: argparse.Namespace) -> int:
    try:
        store = BacklogStore(args.backlog)
        backlog = store.load()
    except InvalidStateError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    active = next((task for task in backlog.tasks if task.status == TaskStatus.IN_PROGRESS), None)
    task = active or store.next_unblocked()
    if task is None:
        print("No unblocked task is currently schedulable.")
        return 0
    print(f"{task.id} ({task.priority}, {task.status.value})")
    if task.dependencies:
        print("Dependencies: " + ", ".join(task.dependencies))
    return 0


def _check_env(args: argparse.Namespace) -> int:
    key = get_openalex_api_key()
    if not key:
        print(
            "No OpenAlex API key found. Set OPENALEX_API_KEY (preferred) or openalex_api.",
            file=sys.stderr,
        )
        return 2
    if args.offline:
        print("OpenAlex API key is present; network authentication was not requested.")
        return 0
    try:
        config = load_project_config(args.config)
        with OpenAlexClient(config.openalex, api_key=key) as client:
            response = client.get("/works", select="id", per_page=1)
    except AuthenticationError:
        print(
            "OpenAlex authentication failed; the configured key was not displayed.",
            file=sys.stderr,
        )
        return 3
    except RateLimitError:
        print(
            "OpenAlex rate limit is currently exhausted; retry later with --resume.",
            file=sys.stderr,
        )
        return 4
    except NetworkError:
        print(
            "OpenAlex network check failed; local offline tasks remain available.",
            file=sys.stderr,
        )
        return 5
    except (ResponseError, OSError, ValidationError, ValueError):
        print("OpenAlex environment check failed without exposing credentials.", file=sys.stderr)
        return 6
    remaining = response.rate_limit.get("x-ratelimit-remaining")
    suffix = f" Rate-limit remaining: {remaining}." if remaining is not None else ""
    print(f"OpenAlex authenticated request succeeded.{suffix}")
    return 0


def _validate_regions(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
        registry = load_region_registry(project.geography.mapping_path)
        if args.write_csv:
            write_mapping_csv(registry, args.write_csv)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Region validation failed: {exc}", file=sys.stderr)
        return 2
    macro_counts = Counter(country.macro_region for country in registry.countries)
    print(f"Validated {len(registry.countries)} country/territory rules.")
    print("Macro-region counts: " + ", ".join(f"{k}={v}" for k, v in sorted(macro_counts.items())))
    print(f"Mapping hash: {registry.semantic_hash}")
    if args.write_csv:
        print(f"Wrote validated CSV: {args.write_csv}")
    return 0


def _profile_institution_types(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
        policy = load_institution_type_policy(args.policy)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Institution-type configuration failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"Would issue one grouped institution request and write {args.output}.")
        return 0
    if not get_openalex_api_key():
        print("Institution-type profiling requires an OpenAlex API key.", file=sys.stderr)
        return 2
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-011"):
            cache = RawResponseCache(project.openalex.cache_directory)
            with OpenAlexClient(project.openalex) as client:
                payload = profile_institution_types(client, cache, policy, force=args.force)
            command = (
                "python -m gisnet.cli profile-institution-types "
                f"--policy {args.policy} --output {args.output}"
            )
            write_institution_type_profile(
                payload,
                path=args.output,
                policy_path=args.policy,
                run_id=run_id,
                command=command,
            )
            _register_manifest(
                "institution_type_profile", ".agent/manifests/institution_type_profile.json"
            )
    except (OpenAlexError, OSError, ValueError) as exc:
        print(f"Institution-type profiling failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Profiled {payload['observed_type_count']} institution types; "
        f"unmapped={len(payload['unmapped_observed_types'])}."
    )
    return 0


def _discover_topics(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
        terms = load_discovery_terms(args.terms)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Topic discovery configuration failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"Would issue {len(terms.terms)} bounded Topic searches and write {args.output}.")
        return 0
    if not get_openalex_api_key():
        print(
            "Topic discovery requires an OpenAlex API key; offline tasks remain available.",
            file=sys.stderr,
        )
        return 2
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-031"):
            cache = RawResponseCache(project.openalex.cache_directory)
            with OpenAlexClient(project.openalex) as client:
                payload = discover_candidate_topics(
                    terms,
                    client,
                    cache,
                    max_results_per_term=args.max_results_per_term,
                    force=args.force,
                )
            command = (
                "python -m gisnet.cli discover-topics "
                f"--terms {args.terms} --output {args.output} "
                f"--max-results-per-term {args.max_results_per_term}"
            )
            write_candidate_artifact(
                payload,
                path=args.output,
                run_id=run_id,
                terms_path=args.terms,
                command=command,
            )
            _register_manifest("topic_candidates", ".agent/manifests/topic_candidates.json")
    except (OpenAlexError, OSError, ValueError) as exc:
        print(f"Topic discovery failed safely: {exc}", file=sys.stderr)
        return 3
    print(f"Discovered {payload['candidate_count']} unique candidate Topics in {args.output}.")
    return 0


def _sample_topic_works(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
        candidates = load_candidate_payload(args.candidates)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Topic sampling inputs failed validation: {exc}", file=sys.stderr)
        return 2
    candidate_count = len(candidates.get("candidates", []))
    if args.dry_run:
        print(
            f"Would issue at most {candidate_count * 6} bounded work-sample requests "
            f"and write {args.output}."
        )
        return 0
    if not get_openalex_api_key():
        print(
            "Topic sampling requires an OpenAlex API key; inputs were not changed.", file=sys.stderr
        )
        return 2
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-032"):
            cache = RawResponseCache(project.openalex.cache_directory)
            with OpenAlexClient(project.openalex) as client:
                samples = sample_candidate_works(candidates, client, cache, force=args.force)
            command = (
                "python -m gisnet.cli sample-topic-works "
                f"--candidates {args.candidates} --output {args.output} --report {args.report}"
            )
            write_sample_artifacts(
                candidates,
                samples,
                path=args.output,
                report_path=args.report,
                run_id=run_id,
                candidate_manifest=".agent/manifests/topic_candidates.json",
                command=command,
            )
            _register_manifest("topic_work_samples", ".agent/manifests/topic_work_samples.json")
    except (OpenAlexError, OSError, ValueError) as exc:
        print(f"Topic sampling failed safely: {exc}", file=sys.stderr)
        return 3
    insufficient = sum(
        review["review_status"] == "insufficient_sample_data" for review in samples["topic_reviews"]
    )
    print(
        f"Stored {samples['sample_count']} work samples for {candidate_count} Topics; "
        f"insufficient={insufficient}."
    )
    print(f"Review report: {args.report}")
    return 0


def _freeze_topics(args: argparse.Namespace) -> int:
    try:
        candidates = load_candidate_payload(args.candidates)
        samples = load_candidate_payload(args.samples)
        decisions = load_topic_decisions(args.decisions)
        registry = freeze_topic_registry(candidates, samples, decisions)
    except (OSError, ValidationError, ValueError, json.JSONDecodeError) as exc:
        print(f"Topic freeze inputs failed validation: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(
            f"Validated {len(registry['topics'])} Topic decisions: "
            f"strict={len(registry['strict_topic_ids'])}, "
            f"broad={len(registry['broad_topic_ids'])}, "
            f"uncertain={len(registry['uncertain_topic_ids'])}."
        )
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-033"):
            command = (
                "python -m gisnet.cli freeze-topics "
                f"--candidates {args.candidates} --samples {args.samples} "
                f"--decisions {args.decisions} --output {args.output}"
            )
            write_frozen_topic_registry(
                registry,
                path=args.output,
                run_id=run_id,
                decisions_path=args.decisions,
                command=command,
            )
            _register_manifest("topic_registry", ".agent/manifests/topic_registry.json")
    except (OSError, ValueError) as exc:
        print(f"Topic freeze failed safely: {exc}", file=sys.stderr)
        return 3
    limitation = (
        "provisional; no human review" if registry["review_status"] == "provisional" else "reviewed"
    )
    print(
        f"Frozen {len(registry['topics'])} Topics to {args.output}; "
        f"strict={len(registry['strict_topic_ids'])}, broad={len(registry['broad_topic_ids'])}; "
        f"status={limitation}."
    )
    return 0


def _validate_corpus_boundary(args: argparse.Namespace) -> int:
    try:
        registry = load_yaml(args.registry)
        if not isinstance(registry, dict):
            raise ValueError("Topic registry must be a mapping")
        samples = load_candidate_payload(args.samples)
        known_positives = load_known_positives(args.known_positives)
        project = load_project_config(args.config)
        records = build_boundary_sample(
            registry,
            samples,
            seed=project.random_seed,
            per_group=args.per_group,
            existing_annotation_path=args.annotations,
        )
        metrics = evaluate_boundary(records, known_positives, registry, samples)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Corpus-boundary validation inputs failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(
            f"Validated boundary inputs; would write {len(records)} annotation rows, "
            f"precision={metrics['precision']['status']}."
        )
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-034"):
            write_annotation_sheet(records, args.annotations)
            command = (
                "python -m gisnet.cli validate-corpus-boundary "
                f"--registry {args.registry} --samples {args.samples} "
                f"--known-positives {args.known_positives} --annotations {args.annotations}"
            )
            write_boundary_artifacts(
                metrics,
                records,
                metrics_path=args.metrics,
                report_path=args.report,
                run_id=run_id,
                registry_path=args.registry,
                known_positive_path=args.known_positives,
                command=command,
            )
            _register_manifest(
                "corpus_boundary_validation",
                ".agent/manifests/corpus_boundary_validation.json",
            )
    except (OSError, ValueError) as exc:
        print(f"Corpus-boundary validation failed safely: {exc}", file=sys.stderr)
        return 3
    recall = metrics["known_positive_recall"]
    print(
        f"Wrote {len(records)} annotation rows; precision={metrics['precision']['status']}; "
        f"known-positive recall={recall['status']} ({recall['recovered_count']}/"
        f"{recall['reference_count']})."
    )
    return 0


def _profile_work_types(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
        policy = load_work_type_policy(args.policy)
        registry = load_yaml(args.registry)
        if not isinstance(registry, dict):
            raise ValueError("Topic registry must be a mapping")
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Work-type profile inputs failed: {exc}", file=sys.stderr)
        return 2
    start_year = args.start_year or project.analysis.start_year
    end_year = args.end_year or project.analysis.end_year
    if args.dry_run:
        print(
            f"Would issue two grouped profiles and six inspection queries for "
            f"{start_year}-{end_year}; output={args.output}."
        )
        return 0
    if not get_openalex_api_key():
        print("Work-type profiling requires an OpenAlex API key.", file=sys.stderr)
        return 2
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-040"):
            cache = RawResponseCache(project.openalex.cache_directory)
            with OpenAlexClient(project.openalex) as client:
                payload = profile_work_types(
                    client,
                    cache,
                    policy,
                    registry,
                    start_year=start_year,
                    end_year=end_year,
                    force=args.force,
                )
            command = (
                "python -m gisnet.cli profile-work-types "
                f"--policy {args.policy} --registry {args.registry} "
                f"--start-year {start_year} --end-year {end_year} --output {args.output}"
            )
            write_work_type_profile(
                payload,
                path=args.output,
                policy_path=args.policy,
                registry_path=args.registry,
                run_id=run_id,
                command=command,
            )
            _register_manifest("work_type_profile", ".agent/manifests/work_type_profile.json")
    except (OpenAlexError, OSError, ValueError) as exc:
        print(f"Work-type profiling failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Profiled {len(payload['records'])} corpus/type rows and "
        f"{len(payload['inspection_samples'])} inspection samples; "
        f"unmapped={len(payload['unmapped_observed_types'])}."
    )
    return 0


def _plan_download(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
        planner_config = load_download_planner_config(args.download_config)
        registry = load_yaml(args.registry)
        if not isinstance(registry, dict):
            raise ValueError("Topic registry must be a mapping")
        region_registry = load_region_registry(args.regions)
        start_year = args.start_year or project.analysis.start_year
        end_year = args.end_year or project.analysis.end_year
        plan = build_query_plan(
            registry,
            region_registry,
            planner_config,
            start_year=start_year,
            end_year=end_year,
            corpus=args.corpus,
        )
        validate_query_plan(plan, planner_config)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Download-plan inputs failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(
            f"Validated {plan['query_count']} deterministic query shards for "
            f"{len(plan['topic_ids'])} Topics, {len(plan['target_country_codes'])} countries, "
            f"and {end_year - start_year + 1} years; no request or write performed."
        )
        return 0
    if not args.skip_preview and not get_openalex_api_key():
        print("Download-plan preview requires an OpenAlex API key.", file=sys.stderr)
        return 2
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-041"):
            if not args.skip_preview:
                cache = RawResponseCache(project.openalex.cache_directory)
                with OpenAlexClient(project.openalex) as client:
                    preview_query_plan(plan, client, cache, planner_config, force=args.force)
            validate_query_plan(plan, planner_config)
            command = (
                "python -m gisnet.cli plan-download "
                f"--download-config {args.download_config} --registry {args.registry} "
                f"--regions {args.regions} --start-year {start_year} --end-year {end_year} "
                f"--corpus {args.corpus} --output {args.output}"
            )
            if args.skip_preview:
                command += " --skip-preview"
            write_query_plan(
                plan,
                path=args.output,
                run_id=run_id,
                download_config_path=args.download_config,
                topic_registry_path=args.registry,
                region_registry_path=args.regions,
                command=command,
            )
            _register_manifest("download_plan", ".agent/manifests/download_plan.json")
    except (OpenAlexError, OSError, ValueError) as exc:
        print(f"Download planning failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Wrote {plan['query_count']} query shards to {args.output}; "
        f"preview={plan['preview_status']}, "
        f"predicted records including duplicates="
        f"{plan['predicted_result_volume_including_duplicates']}, "
        f"requests={plan['predicted_request_count']}, "
        f"estimated bulk cost USD={plan['estimated_bulk_cost_usd']}."
    )
    return 0


def _download_works(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
        plan = load_download_plan(args.plan)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Raw-work download inputs failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        predicted_pages = plan.get("predicted_request_count")
        print(
            f"Would execute {plan['query_count']} resumable query shards and approximately "
            f"{predicted_pages} raw pages; no request or write performed."
        )
        return 0
    if not get_openalex_api_key():
        print("Raw-work download requires an OpenAlex API key.", file=sys.stderr)
        return 2
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-042"):
            cache = RawResponseCache(project.openalex.cache_directory)
            with OpenAlexClient(project.openalex) as client:
                payload = execute_download_plan(
                    plan,
                    client,
                    cache,
                    checkpoint_directory=project.openalex.checkpoint_directory,
                    status_path=args.status,
                    resume=args.resume or not args.force,
                    force=args.force,
                    max_queries=args.max_queries,
                    workers=args.workers,
                )
            command = (
                "python -m gisnet.cli download-works "
                f"--plan {args.plan} --status {args.status} --resume"
            )
            write_download_status_manifest(
                payload,
                status_path=args.status,
                plan_path=args.plan,
                download_config_path=args.download_config,
                run_id=run_id,
                command=command,
            )
            _register_manifest(
                "raw_works_download_status",
                ".agent/manifests/raw_works_download_status.json",
            )
    except (OpenAlexError, OSError, ValueError) as exc:
        print(f"Raw-work download failed safely: {exc}", file=sys.stderr)
        return 3
    counts = payload["status_counts"]
    print(
        f"Raw-work download status={payload['status']}; complete={counts['complete']}, "
        f"blocked={counts['blocked']}, failed={counts['failed']}; "
        f"pages={payload['actual_page_count']}, "
        f"records including duplicates={payload['actual_result_count_including_duplicates']}."
    )
    return 0 if payload["status"] == "complete" else 4


def _normalize_works(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
        plan = load_download_plan(args.plan)
        topic_registry = load_yaml(args.registry)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Work-normalization inputs failed: {exc}", file=sys.stderr)
        return 2
    start_year = args.start_year or project.analysis.start_year
    end_year = args.end_year or project.analysis.end_year
    if args.dry_run:
        print(
            f"Would validate and normalize {plan['query_count']} raw query checkpoints for "
            f"{start_year}-{end_year}; no raw page or output is changed."
        )
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-043"):
            summary = normalize_raw_works(
                plan,
                RawResponseCache(project.openalex.cache_directory),
                topic_registry,
                checkpoint_directory=args.raw_checkpoints,
                staging_path=args.staging,
                normalization_checkpoint_path=args.checkpoint,
                output_directory=args.output_directory,
                start_year=start_year,
                end_year=end_year,
                resume=args.resume or not args.force,
                force=args.force,
                batch_size=args.batch_size,
                duckdb_memory_limit=args.duckdb_memory_limit,
                duckdb_threads=args.duckdb_threads,
            )
            command = (
                "python -m gisnet.cli normalize-works "
                f"--plan {args.plan} --output-directory {args.output_directory} --resume "
                f"--duckdb-memory-limit {args.duckdb_memory_limit} "
                f"--duckdb-threads {args.duckdb_threads}"
            )
            write_normalization_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                download_plan_path=args.plan,
                command=command,
            )
            for name in ("works", "work_topics", "work_malformed", "works_normalization_summary"):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Work normalization failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Normalized {summary['work_count']} unique Works and "
        f"{summary['work_topic_count']} work-Topic rows; "
        f"duplicate source occurrences={summary['duplicate_source_occurrence_count']}, "
        f"malformed={summary['malformed_record_count']}."
    )
    return 0


def _extract_institutions(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
    except (OSError, ValueError) as exc:
        print(f"Institution-extraction inputs failed: {exc}", file=sys.stderr)
        return 2
    start_year = args.start_year or project.analysis.start_year
    end_year = args.end_year or project.analysis.end_year
    if args.dry_run:
        print(
            f"Would extract distinct institution assertions from {args.works} for "
            f"{start_year}-{end_year}; no output is changed."
        )
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-050"):
            summary = extract_work_institutions(
                args.works,
                extracted_path=args.extracted,
                unresolved_path=args.unresolved,
                start_year=start_year,
                end_year=end_year,
                batch_size=args.batch_size,
                force=args.force,
            )
            command = (
                "python -m gisnet.cli extract-institutions "
                f"--works {args.works} --extracted {args.extracted} "
                f"--unresolved {args.unresolved} --resume"
            )
            write_extraction_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in (
                "work_institutions_extracted",
                "work_institutions_unresolved",
                "institution_extraction_summary",
            ):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (OSError, ValueError) as exc:
        print(f"Institution extraction failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Extracted {summary['work_institution_count']} distinct Work-institution rows across "
        f"{summary['resolved_work_count']} Works; unresolved Works="
        f"{summary['unresolved_work_count']}."
    )
    return 0


def _build_institutions(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
        policy = load_institution_type_policy(args.institution_types)
    except (OSError, ValueError) as exc:
        print(f"Institution-master inputs failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        mode = "offline" if args.offline else "cached/live stable-ID completion"
        print(f"Would build the institution master from {args.extracted} using {mode}.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-051"):
            if args.offline:
                summary = build_institution_master(
                    args.extracted,
                    policy,
                    master_path=args.institutions,
                    qa_path=args.qa,
                    lookup_batch_size=args.lookup_batch_size,
                    force=args.force,
                )
            else:
                with OpenAlexClient(project.openalex) as client:
                    summary = build_institution_master(
                        args.extracted,
                        policy,
                        master_path=args.institutions,
                        qa_path=args.qa,
                        client=client,
                        cache=RawResponseCache(project.openalex.cache_directory),
                        lookup_batch_size=args.lookup_batch_size,
                        force=args.force,
                    )
            command = (
                "python -m gisnet.cli build-institutions "
                f"--extracted {args.extracted} --institutions {args.institutions} --resume"
            )
            write_institution_master_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                institution_type_path=args.institution_types,
                command=command,
            )
            for name in ("institutions", "institution_metadata_qa", "institution_master_summary"):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (OSError, ValueError) as exc:
        print(f"Institution master failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Built {summary['institution_count']} institutions; metadata QA="
        f"{summary['metadata_qa_count']}, lookup matches={summary['lookup_found_count']}."
    )
    return 0


def _apply_geography(args: argparse.Namespace) -> int:
    try:
        regions = load_region_registry(args.regions)
        overrides = InstitutionOverrideRegistry.load(args.institution_overrides)
    except (OSError, ValueError) as exc:
        print(f"Institution-geography inputs failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"Would apply {len(regions.countries)} frozen country rules to {args.institutions}.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-053"):
            summary = apply_institution_geography(
                args.institutions,
                regions,
                overrides,
                output_path=args.output,
                qa_path=args.qa,
            )
            command = (
                "python -m gisnet.cli apply-geography "
                f"--institutions {args.institutions} --regions {args.regions} --resume"
            )
            write_geography_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                regions_path=args.regions,
                overrides_path=args.institution_overrides,
                command=command,
            )
            for name in (
                "institutions_geographic",
                "institution_geography_qa",
                "institution_geography_summary",
            ):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (OSError, ValueError) as exc:
        print(f"Institution geography failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Mapped {summary['institution_count']} institutions; geography QA="
        f"{summary['geography_qa_count']}, manual overrides={summary['manual_override_count']}."
    )
    return 0


def _enrich_institutions(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(
            f"Would enrich {args.institutions} using ROR mode={args.ror_mode}, "
            f"max-lookups={args.max_ror_lookups}; no output is changed."
        )
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-052"):
            summary = enrich_institutions_with_ror(
                args.institutions,
                output_path=args.output,
                qa_path=args.qa,
                cache_directory=args.ror_cache,
                mode=args.ror_mode,
                dump_path=args.ror_dump,
                dump_version=args.ror_dump_version,
                max_lookups=args.max_ror_lookups,
            )
            command = (
                "python -m gisnet.cli enrich-institutions "
                f"--institutions {args.institutions} --ror-mode {args.ror_mode} "
                f"--max-ror-lookups {args.max_ror_lookups} --resume"
            )
            write_ror_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in ("institutions_ror", "institution_ror_qa", "institution_ror_summary"):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (OSError, ValueError) as exc:
        print(f"ROR enrichment failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"ROR records={summary['record_count']}/{summary['unique_valid_ror_id_count']}; "
        f"enriched={summary['status_counts'].get('enriched', 0)}, "
        f"missing-id={summary['status_counts'].get('missing_ror_id', 0)}."
    )
    return 0


def _build_hierarchy(args: argparse.Namespace) -> int:
    try:
        overrides = InstitutionOverrideRegistry.load(args.institution_overrides)
    except (OSError, ValueError) as exc:
        print(f"Institution-hierarchy inputs failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(
            f"Would build organization and umbrella views from {args.institutions} using "
            f"{len(overrides.rules)} explicit rules; no output is changed."
        )
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-054"):
            summary = build_institution_hierarchy(
                args.institutions,
                overrides,
                hierarchy_path=args.output,
                audit_path=args.audit,
                candidates_path=args.candidates,
            )
            command = (
                "python -m gisnet.cli build-hierarchy "
                f"--institutions {args.institutions} --output {args.output} --resume"
            )
            write_hierarchy_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                overrides_path=args.institution_overrides,
                command=command,
            )
            for name in (
                "institution_hierarchy",
                "institution_canonicalization_audit",
                "institution_hierarchy_candidates",
                "institution_hierarchy_summary",
            ):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (OSError, ValueError) as exc:
        print(f"Institution hierarchy failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Built {summary['hierarchy_row_count']} hierarchy rows; explicit collapses="
        f"{summary['explicit_collapse_count']}, relationship candidates="
        f"{summary['relationship_candidate_count']}."
    )
    return 0


def _diagnose_versions(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"Would diagnose DOI/version families for {args.works}; no output is changed.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-044"):
            summary = build_version_diagnostics(
                args.works,
                diagnostics_path=args.output,
                duplicate_doi_path=args.duplicate_dois,
                ambiguous_path=args.ambiguous,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = (
                "python -m gisnet.cli diagnose-versions "
                f"--works {args.works} --duckdb-memory-limit {args.duckdb_memory_limit} "
                f"--duckdb-threads {args.duckdb_threads} --resume"
            )
            write_version_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in (
                "work_version_diagnostics",
                "work_duplicate_doi_diagnostics",
                "work_ambiguous_version_candidates",
                "work_version_diagnostics_summary",
            ):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Work version diagnostics failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Diagnosed {summary['work_count']} Works; exact DOI families="
        f"{summary['exact_doi_family_count']}, ambiguous possible families="
        f"{summary['ambiguous_possible_family_count']}."
    )
    return 0


def _build_corpus(args: argparse.Namespace) -> int:
    try:
        policy = load_work_type_policy(args.work_types)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Corpus policy failed validation: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"Would build Strict/Broad corpus flags for {args.works}; no output is changed.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-060"):
            summary = build_work_corpus(
                args.works,
                args.work_topics,
                args.versions,
                policy,
                corpus_path=args.output,
                annual_counts_path=args.annual_counts,
                topic_family_counts_path=args.topic_family_counts,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = (
                "python -m gisnet.cli build-corpus "
                f"--works {args.works} --work-topics {args.work_topics} --resume"
            )
            write_corpus_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                topic_registry_path=args.topic_registry,
                work_type_path=args.work_types,
                command=command,
            )
            for name in (
                "work_corpus",
                "corpus_annual_counts",
                "corpus_topic_family_counts",
                "work_corpus_summary",
            ):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Corpus construction failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Corpus Works={summary['work_count']}; strict={summary['strict_primary_count']}, "
        f"broad={summary['broad_primary_count']}."
    )
    return 0


def _build_work_institutions(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(
            f"Would build normalized organization/umbrella Work institutions from "
            f"{args.extracted}; no output is changed."
        )
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-061"):
            summary = build_normalized_work_institutions(
                args.extracted,
                args.work_corpus,
                args.institutions,
                args.hierarchy_map,
                output_path=args.output,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = (
                "python -m gisnet.cli build-work-institutions "
                f"--extracted {args.extracted} --output {args.output} --resume"
            )
            write_work_institution_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in ("work_institutions", "work_institutions_summary"):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Normalized Work institutions failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Built {summary['row_count']} Work-institution rows; organization Works="
        f"{summary['organization_work_count']}, umbrella Works={summary['umbrella_work_count']}."
    )
    return 0


def _build_edges(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Edge configuration failed validation: {exc}", file=sys.stderr)
        return 2
    corpora = list(project.corpus_views) if args.corpus == "all" else [args.corpus]
    hierarchies = list(project.hierarchy_views) if args.hierarchy == "all" else [args.hierarchy]
    if args.dry_run:
        print(
            f"Would build {corpora} x {hierarchies} annual edges from "
            f"{args.work_institutions}; no output is changed."
        )
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-062"):
            summary = build_collaboration_edges(
                args.work_institutions,
                work_edges_path=args.work_edges,
                edges_year_path=args.output,
                diagnostics_path=args.diagnostics,
                warning_institution_count=project.consortium.warning_institution_count,
                exclusion_institution_count=project.consortium.exclusion_institution_count,
                corpus_views=corpora,
                hierarchy_views=hierarchies,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = (
                "python -m gisnet.cli build-edges "
                f"--work-institutions {args.work_institutions} --corpus {args.corpus} "
                f"--hierarchy {args.hierarchy} --resume"
            )
            write_edge_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in (
                "work_edges",
                "edges_year",
                "edge_work_diagnostics",
                "collaboration_edges_summary",
            ):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Collaboration edges failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Built {summary['work_edge_count']} Work-edge contributions and "
        f"{summary['annual_edge_count']} annual edges."
    )
    return 0


def _build_outputs(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Output configuration failed validation: {exc}", file=sys.stderr)
        return 2
    corpora = list(project.corpus_views) if args.corpus == "all" else [args.corpus]
    hierarchies = list(project.hierarchy_views) if args.hierarchy == "all" else [args.hierarchy]
    if args.dry_run:
        print(
            f"Would build {corpora} x {hierarchies} institutional outputs from "
            f"{args.work_institutions}; no output is changed."
        )
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-063"):
            summary = build_institution_outputs(
                args.work_institutions,
                outputs_year_path=args.output,
                reconciliation_path=args.reconciliation,
                corpus_views=corpora,
                hierarchy_views=hierarchies,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = (
                "python -m gisnet.cli build-outputs "
                f"--work-institutions {args.work_institutions} --corpus {args.corpus} "
                f"--hierarchy {args.hierarchy} --resume"
            )
            write_output_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in (
                "institution_outputs_year",
                "institution_output_reconciliation",
                "institution_outputs_summary",
            ):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Institutional outputs failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Built {summary['node_year_count']} node-year outputs; zero-edge output rows="
        f"{summary['zero_edge_output_node_year_count']}."
    )
    return 0


def _build_region_flows(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"Would aggregate geographic flows from {args.work_edges}; no output is changed.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-065"):
            summary = build_geographic_flows(
                args.work_edges,
                flows_path=args.output,
                reconciliation_path=args.reconciliation,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = (
                "python -m gisnet.cli build-region-flows "
                f"--work-edges {args.work_edges} --output {args.output} --resume"
            )
            write_flow_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in ("region_flows_year", "region_flow_reconciliation", "region_flows_summary"):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Geographic flows failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Built {summary['flow_row_count']} geographic flow rows; Asia countries="
        f"{summary['asia_country_count']}, Americas countries={summary['americas_country_count']}."
    )
    return 0


def _validate_outputs(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Validation configuration failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"Would validate edge arithmetic in {args.work_edges}; no output is changed.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-080"):
            payload = validate_edge_arithmetic(
                args.work_edges,
                args.edges,
                args.edge_diagnostics,
                warning_institution_count=project.consortium.warning_institution_count,
                exclusion_institution_count=project.consortium.exclusion_institution_count,
            )
            command = (
                f"python -m gisnet.cli validate --work-edges {args.work_edges} --edges {args.edges}"
            )
            write_edge_validation_artifact(
                payload,
                path=args.output,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            _register_manifest(
                "edge_arithmetic_validation", ".agent/manifests/edge_arithmetic_validation.json"
            )
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Stored-data validation failed: {exc}", file=sys.stderr)
        return 3
    print(
        f"Edge arithmetic passed {len(payload['checks'])} checks across "
        f"{payload['work_edge_count']} Work-edge contributions."
    )
    return 0


def _verify_reproducibility(args: argparse.Namespace) -> int:
    if args.dry_run:
        print("Would compare core datasets with manifests and check for incomplete temp outputs.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-083"):
            payload = verify_reproducibility()
            command = "python -m gisnet.cli verify-reproducibility"
            write_reproducibility_artifact(
                payload,
                path=args.output,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            _register_manifest(
                "reproducibility_validation", ".agent/manifests/reproducibility_validation.json"
            )
    except (OSError, ValueError) as exc:
        print(f"Reproducibility validation failed: {exc}", file=sys.stderr)
        return 3
    print(
        f"Reproducibility passed for {payload['dataset_check_count']} core datasets; "
        "no incomplete temp output remains."
    )
    return 0


def _compute_edge_intensity(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Intensity configuration failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"Would compute intensity/persistence for {args.edges}; no output is changed.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-064"):
            summary = build_edge_intensity(
                args.edges,
                args.institution_outputs,
                output_path=args.output,
                analysis_start_year=project.analysis.start_year,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = (
                "python -m gisnet.cli compute-edge-intensity "
                f"--edges {args.edges} --institution-outputs {args.institution_outputs} --resume"
            )
            write_intensity_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in ("edges_metrics_year", "edge_intensity_summary"):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Edge intensity failed safely: {exc}", file=sys.stderr)
        return 3
    invalid_persistence = (
        summary["invalid_persistence_3y_count"] + summary["invalid_persistence_5y_count"]
    )
    print(
        f"Computed intensity/persistence for {summary['edge_year_count']} annual edges; "
        f"invalid persistence={invalid_persistence}."
    )
    return 0


def _build_graphs(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Graph configuration failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"Would build annual graph catalogues from {args.edges}; no output is changed.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-070"):
            summary = build_annual_graph_catalogue(
                args.edges,
                args.institution_outputs,
                summary_path=args.output,
                minimum_fractional_weight=project.network.minimum_fractional_weight,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = (
                "python -m gisnet.cli build-graphs "
                f"--edges {args.edges} --institution-outputs {args.institution_outputs} --resume"
            )
            write_graph_artifacts(
                summary,
                catalogue_path=args.catalogue,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in ("graph_summary_year", "annual_graph_catalogue"):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Annual graph build failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Built {summary['graph_count']} annual graph catalogues with "
        f"{summary['isolated_output_node_count']} retained isolated node observations."
    )
    return 0


def _compute_metrics(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Metric configuration failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"Would compute annual network metrics from {args.edges}; no output is changed.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-071"):
            summary = build_network_metrics(
                args.edges,
                args.institution_outputs,
                nodes_metrics_path=args.nodes_output,
                graph_metrics_path=args.graphs_output,
                approximate_betweenness_threshold=(
                    project.network.approximate_betweenness_threshold
                ),
                random_seed=project.random_seed,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = (
                "python -m gisnet.cli compute-metrics "
                f"--edges {args.edges} --institution-outputs {args.institution_outputs} --resume"
            )
            write_metric_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in ("nodes_year", "graph_metrics_year", "network_metrics_summary"):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Network metric computation failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Computed {summary['node_metric_row_count']} node-year and "
        f"{summary['graph_metric_row_count']} graph-year metric rows."
    )
    return 0


def _detect_communities(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Community configuration failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"Would detect annual communities from {args.edges}; no output is changed.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-072"):
            summary = build_annual_communities(
                args.edges,
                args.nodes,
                communities_path=args.output,
                sensitivity_path=args.sensitivity,
                random_seed=project.random_seed,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = (
                "python -m gisnet.cli detect-communities "
                f"--edges {args.edges} --nodes {args.nodes} --resume"
            )
            write_community_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in (
                "communities_year",
                "community_sensitivity_year",
                "community_detection_summary",
            ):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Community detection failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Detected communities for {summary['community_node_row_count']} node-years at "
        f"{len(summary['resolutions'])} resolutions."
    )
    return 0


def _match_communities(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"Would match adjacent-year communities from {args.communities}.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    command = "python -m gisnet.cli match-communities --resume"
    try:
        with RunLock(run_id=run_id, task_id="GISNET-073"):
            summary = build_community_continuity(
                args.communities,
                continuity_output=args.continuity_output,
                transitions_output=args.transitions_output,
                confident_match_threshold=args.confident_match_threshold,
                event_overlap_threshold=args.event_overlap_threshold,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            write_continuity_artifacts(
                summary,
                summary_path=args.summary,
                continuity_path=args.continuity_output,
                transitions_path=args.transitions_output,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in (
                "community_continuity_year",
                "community_transitions_year",
                "community_continuity_summary",
            ):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Community continuity failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Matched {summary['selected_match_count']} adjacent-year communities; "
        f"uncertain={summary['uncertain_match_count']}, "
        f"transition rows={summary['transition_row_count']}."
    )
    return 0


def _build_layout(args: argparse.Namespace) -> int:
    try:
        project = load_project_config(args.config)
    except (OSError, ValidationError, ValueError) as exc:
        print(f"Layout configuration failed: {exc}", file=sys.stderr)
        return 2
    if args.dry_run:
        print(f"Would build a fixed aggregate layout from {args.edges}; no output is changed.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-074"):
            summary = build_fixed_layout(
                args.edges,
                args.nodes,
                output_path=args.output,
                random_seed=project.random_seed,
                core_size=args.core_size,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = (
                "python -m gisnet.cli build-layout "
                f"--edges {args.edges} --nodes {args.nodes} --core-size {args.core_size} --resume"
            )
            write_layout_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in ("network_layout", "network_layout_summary"):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Fixed layout build failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Built fixed coordinates for {summary['institution_count']} institutions; "
        f"core={summary['core_institution_count']}, "
        f"fallback={summary['fallback_institution_count']}."
    )
    return 0


def _audit_top_entities(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"Would audit top entities from {args.nodes}; no output is changed.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-081"):
            summary = build_top_entity_audit(
                args.nodes,
                args.edges,
                args.work_institutions,
                args.institutions,
                args.hierarchy_path,
                institution_output_path=args.institution_output,
                edge_output_path=args.edge_output,
                sample_size=args.sample_size,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = "python -m gisnet.cli audit-top-entities --resume"
            write_audit_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in (
                "top_institution_audit",
                "top_edge_audit",
                "top_entity_audit_summary",
            ):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Top-entity audit failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Audited {summary['institution_audit_row_count']} institutions and "
        f"{summary['edge_audit_row_count']} cross-region edges."
    )
    return 0


def _run_sensitivity(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(
            f"Would build the sensitivity matrix from {args.graph_metrics}; no output is changed."
        )
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-082"):
            summary = build_sensitivity_matrix(
                args.graph_metrics,
                args.edges,
                args.work_edges,
                args.nodes,
                args.work_institutions,
                args.work_corpus,
                args.topic_registry,
                output_path=args.output,
                scope_output_path=args.scope_output,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = "python -m gisnet.cli run-sensitivity --resume"
            write_sensitivity_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in (
                "sensitivity_matrix",
                "institution_scope_sensitivity_year",
                "sensitivity_summary",
            ):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Sensitivity matrix failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Completed {summary['completed_comparison_count']} of "
        f"{summary['comparison_count']} sensitivity comparisons; "
        f"major changes={summary['major_change_count']}."
    )
    return 0


def _build_figures(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"Would build regional trend figures from {args.flows}; no output is changed.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-090"):
            summary = build_annual_trends(
                args.flows,
                args.graph_metrics,
                output_path=args.output,
                trend_figure_path=args.trend_figure,
                comparison_figure_path=args.comparison_figure,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = "python -m gisnet.cli build-figures --resume"
            write_trend_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in ("trend_series_year", "annual_trends_summary"):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Trend figure build failed safely: {exc}", file=sys.stderr)
        return 3
    print(f"Built {summary['trend_row_count']} annual trend rows and two publication SVG figures.")
    return 0


def _build_matrix(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"Would build collaboration matrices from {args.flows}; no output is changed.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-091"):
            summary = build_collaboration_matrix(
                args.flows,
                output_path=args.output,
                figure_path=args.figure,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = "python -m gisnet.cli build-matrix --resume"
            write_matrix_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in ("collaboration_matrix_year", "collaboration_matrix_summary"):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Collaboration matrix failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Built {summary['matrix_and_drilldown_row_count']} matrix/drilldown rows; "
        f"reconciliation failures={summary['reconciliation_failure_count']}."
    )
    return 0


def _build_map_data(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"Would build geographic map data from {args.nodes}; no output is changed.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-092"):
            summary = build_map_data(
                args.nodes,
                args.edges,
                map_nodes_path=args.nodes_output,
                map_edges_path=args.edges_output,
                coverage_path=args.coverage_output,
                edge_limit_per_view=args.edge_limit,
                node_limit_per_view=args.node_limit,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = "python -m gisnet.cli build-map-data --resume"
            write_map_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in (
                "map_nodes_year",
                "map_edges_year",
                "map_coverage_year",
                "geographic_map_summary",
            ):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Geographic map data failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Built {summary['map_node_row_count']} map nodes and "
        f"{summary['map_edge_row_count']} thresholded map edges; invented coordinates=0."
    )
    return 0


def _build_network_view(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"Would build fixed-layout network data from {args.nodes}; no output is changed.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    try:
        with RunLock(run_id=run_id, task_id="GISNET-093"):
            summary = build_network_view(
                args.nodes,
                args.edges,
                args.communities,
                args.layout,
                nodes_output_path=args.nodes_output,
                edges_output_path=args.edges_output,
                accessibility_output_path=args.accessibility_output,
                edge_limit_per_view=args.edge_limit,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = "python -m gisnet.cli build-network-view --resume"
            write_network_view_artifacts(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            for name in (
                "network_view_nodes_year",
                "network_view_edges_year",
                "network_accessibility_year",
                "network_view_summary",
            ):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Fixed-layout network view failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Built {summary['network_node_row_count']} fixed network nodes and "
        f"{summary['network_edge_row_count']} visible edges."
    )
    return 0


def _build_dashboard_data(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"Would build the public dashboard bundle in {args.output_directory}.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    sources: dict[str, str | Path] = {
        "trends": "data/processed/trend_series_year.parquet",
        "matrix": "data/processed/collaboration_matrix_year.parquet",
        "map_nodes": "data/processed/map_nodes_year.parquet",
        "map_edges": "data/processed/map_edges_year.parquet",
        "map_coverage": "data/processed/map_coverage_year.parquet",
        "network_nodes": "data/processed/network_view_nodes_year.parquet",
        "network_edges": "data/processed/network_view_edges_year.parquet",
        "network_accessibility": "data/processed/network_accessibility_year.parquet",
        "graph_metrics": "data/processed/graph_metrics_year.parquet",
        "sensitivity": "data/processed/sensitivity_matrix.parquet",
        "community_continuity": "data/processed/community_continuity_year.parquet",
        "community_transitions": "data/processed/community_transitions_year.parquet",
        "institution_hierarchy": "data/processed/institution_hierarchy.parquet",
        "institutions": "data/processed/institutions.parquet",
        "complete_nodes": "data/processed/nodes_year.parquet",
    }
    try:
        with RunLock(run_id=run_id, task_id="GISNET-095"):
            summary = build_dashboard_bundle(
                sources=sources,
                output_directory=args.output_directory,
                metadata_path=args.metadata,
                memory_limit=args.duckdb_memory_limit,
                threads=args.duckdb_threads,
            )
            command = "python -m gisnet.cli build-dashboard-data --resume"
            write_dashboard_artifact(
                summary,
                summary_path=args.summary,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            _register_manifest(
                "dashboard_bundle_summary", ".agent/manifests/dashboard_bundle_summary.json"
            )
    except (duckdb.Error, OSError, ValueError) as exc:
        print(f"Dashboard data build failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Built {summary['table_count']} dashboard tables with {summary['row_count']} rows; "
        "ordinary viewing makes zero API requests."
    )
    return 0


def _run_pipeline(args: argparse.Namespace) -> int:
    run_id = _resolve_run_id(args.run_id)
    config = load_project_config(args.config)
    start_year = args.start_year or config.analysis.start_year
    end_year = args.end_year or config.analysis.end_year
    summary = run_pipeline(
        stages=DEFAULT_STAGES,
        runner=main,
        run_id=run_id,
        config_path=args.config,
        start_year=start_year,
        end_year=end_year,
        corpus=args.corpus,
        hierarchy=args.hierarchy,
        resume=args.resume,
        force=args.force,
        dry_run=args.dry_run,
    )
    for stage in summary["stages"]:
        print(f"{stage['status']}: {stage['stage']} ({stage['reason']})")
    if args.dry_run:
        return 0
    command = (
        "python -m gisnet.cli run-pipeline "
        f"--start-year {start_year} --end-year {end_year} "
        f"--corpus {args.corpus} --hierarchy {args.hierarchy} --resume"
    )
    try:
        with RunLock(run_id=run_id, task_id="GISNET-102"):
            write_pipeline_artifact(
                summary,
                path=args.output,
                run_id=run_id,
                project_config_path=args.config,
                command=command,
            )
            _register_manifest("pipeline_run_summary", ".agent/manifests/pipeline_run_summary.json")
    except (OSError, ValueError) as exc:
        print(f"Pipeline summary write failed safely: {exc}", file=sys.stderr)
        return 3
    if not summary["success"]:
        print(f"Pipeline stopped safely at {summary['failed_stage']}.", file=sys.stderr)
        print(f"Next recovery command: {summary['recovery_command']}", file=sys.stderr)
        return 3
    counts = summary["status_counts"]
    print(
        f"Pipeline complete: {counts.get('skipped_valid', 0)} valid stages skipped; "
        f"{sum(value for key, value in counts.items() if key != 'skipped_valid')} stages executed."
    )
    return 0


def _build_methodology_report(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"Would generate the methodology report at {args.output} from validated summaries.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    command = "python -m gisnet.cli report --resume"
    try:
        with RunLock(run_id=run_id, task_id="GISNET-100"):
            summary = build_methodology_report(
                project_path=args.config,
                topic_registry_path=args.topic_registry,
                regions_path=args.regions,
                output_path=args.output,
            )
            write_methodology_artifacts(
                summary,
                summary_path=args.summary,
                report_path=args.output,
                run_id=run_id,
                project_path=args.config,
                topic_registry_path=args.topic_registry,
                regions_path=args.regions,
                command=command,
            )
            _register_manifest("methodology_report", ".agent/manifests/methodology_report.json")
            _register_manifest(
                "methodology_report_summary",
                ".agent/manifests/methodology_report_summary.json",
            )
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"Methodology report failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Generated {summary['present_section_count']} methodology sections and "
        f"validated {summary['figure_count']} processed-data figures."
    )
    return 0


def _build_data_dictionary(args: argparse.Namespace) -> int:
    if args.dry_run:
        print(f"Would document every public table in {args.data_directory}.")
        return 0
    run_id = _resolve_run_id(args.run_id)
    command = "python -m gisnet.cli build-data-dictionary --resume"
    try:
        with RunLock(run_id=run_id, task_id="GISNET-101"):
            summary = build_public_data_dictionary(
                data_directory=args.data_directory,
                metadata_path=args.metadata,
                output_json=args.dictionary,
                output_markdown=args.report,
            )
            write_data_dictionary_artifacts(
                summary,
                summary_path=args.summary,
                dictionary_path=args.dictionary,
                report_path=args.report,
                run_id=run_id,
                project_path=args.config,
                command=command,
            )
            for name in (
                "public_data_dictionary",
                "data_provenance_report",
                "data_dictionary_summary",
            ):
                _register_manifest(name, f".agent/manifests/{name}.json")
    except (OSError, KeyError, TypeError, ValueError) as exc:
        print(f"Data dictionary failed safely: {exc}", file=sys.stderr)
        return 3
    print(
        f"Documented {summary['documented_table_count']} released tables and "
        f"{summary['column_entry_count']} table-column entries."
    )
    return 0


def _resolve_run_id(value: str | None) -> str:
    if value:
        return value
    try:
        active = ProjectStateStore().load().active_run_id
    except InvalidStateError:
        active = None
    return active or make_run_id()


def _register_manifest(dataset_name: str, manifest_path: str) -> None:
    store = ProjectStateStore()
    state = store.load()
    state.dataset_manifests[dataset_name] = manifest_path
    store.save(state)


def _not_implemented(args: argparse.Namespace) -> int:
    print(
        f"{args.command} is defined by the backlog but is not implemented in the current task set.",
        file=sys.stderr,
    )
    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
