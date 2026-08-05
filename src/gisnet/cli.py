"""Single command-line entry point for the GIS collaboration pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from pydantic import ValidationError

from gisnet.config import config_file_hash, load_project_config
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
from gisnet.geography import load_region_registry, write_mapping_csv
from gisnet.openalex.cache import RawResponseCache
from gisnet.openalex.client import (
    AuthenticationError,
    NetworkError,
    OpenAlexClient,
    OpenAlexError,
    RateLimitError,
    ResponseError,
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
    "plan-download",
    "download-works",
    "normalize-works",
    "extract-institutions",
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
