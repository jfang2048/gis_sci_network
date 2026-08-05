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
from gisnet.corpus.work_types import (
    load_work_type_policy,
    profile_work_types,
    write_work_type_profile,
)
from gisnet.geography import load_region_registry, write_mapping_csv
from gisnet.institutions.extract import extract_work_institutions, write_extraction_artifacts
from gisnet.institutions.geography import apply_institution_geography, write_geography_artifacts
from gisnet.institutions.master import build_institution_master, write_institution_master_artifacts
from gisnet.institutions.overrides import InstitutionOverrideRegistry
from gisnet.institutions.types import (
    load_institution_type_policy,
    profile_institution_types,
    write_institution_type_profile,
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
from gisnet.secrets import get_openalex_api_key
from gisnet.state import (
    BacklogStore,
    InvalidStateError,
    ProjectStateStore,
    RunLock,
    TaskStatus,
    make_run_id,
)

_NOT_IMPLEMENTED_COMMANDS = (
    "enrich-institutions",
    "build-corpus",
    "build-edges",
    "compute-metrics",
    "build-region-flows",
    "detect-communities",
    "match-communities",
    "validate",
    "build-figures",
    "build-dashboard-data",
    "run-pipeline",
    "report",
)


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
    build_institutions.add_argument("--lookup-batch-size", default=25, type=int)
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
