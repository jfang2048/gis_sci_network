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
from gisnet.geography import load_region_registry, write_mapping_csv
from gisnet.openalex.client import (
    AuthenticationError,
    NetworkError,
    OpenAlexClient,
    RateLimitError,
    ResponseError,
)
from gisnet.secrets import get_openalex_api_key
from gisnet.state import BacklogStore, InvalidStateError, ProjectStateStore, TaskStatus

_NOT_IMPLEMENTED_COMMANDS = (
    "discover-topics",
    "sample-topic-works",
    "freeze-topics",
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
