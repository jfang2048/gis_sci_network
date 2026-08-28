"""Plan-driven raw OpenAlex Works acquisition with query/page checkpoints."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gisnet.artifacts import load_json_object, write_json_artifact
from gisnet.config import config_file_hash
from gisnet.openalex.cache import CacheCorruptionError, RawResponseCache
from gisnet.openalex.client import OpenAlexClient, OpenAlexError, RateLimitError
from gisnet.openalex.pagination import CursorPaginator, PaginationError

_TERMINAL = {"complete", "blocked", "failed"}


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def load_download_plan(path: str | Path = "data/reference/download_plan.json") -> dict[str, Any]:
    plan = load_json_object(path)
    queries = plan.get("queries")
    if not isinstance(queries, list) or len(queries) != plan.get("query_count"):
        raise ValueError("download plan query_count does not match its query records")
    if len({query.get("query_id") for query in queries}) != len(queries):
        raise ValueError("download plan query IDs must be unique")
    return plan


def _initial_records(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "query_id": query["query_id"],
            "status": "blocked",
            "status_reason": "not_started",
            "predicted_result_count": query.get("predicted_result_count"),
            "predicted_page_count": query.get("predicted_page_count"),
            "actual_result_count_including_duplicates": 0,
            "actual_page_count": 0,
            "first_retrieved_at_utc": None,
            "last_retrieved_at_utc": None,
            "source_updated_date_min": None,
            "source_updated_date_max": None,
            "raw_page_checksums_validated": False,
            "failure_type": None,
            "updated_at_utc": None,
        }
        for query in plan["queries"]
    ]


def _load_existing_records(plan: dict[str, Any], status_path: str | Path) -> list[dict[str, Any]]:
    path = Path(status_path)
    initial = _initial_records(plan)
    if not path.exists():
        return initial
    existing = load_json_object(path)
    if existing.get("logical_plan_hash") != plan.get("logical_plan_hash"):
        raise ValueError("existing download status belongs to a different logical plan")
    records = existing.get("queries")
    if not isinstance(records, list):
        raise ValueError("existing download status lacks query records")
    by_id = {record.get("query_id"): record for record in records if isinstance(record, dict)}
    if set(by_id) != {record["query_id"] for record in initial}:
        raise ValueError("existing download status query coverage differs from the plan")
    return [dict(by_id[record["query_id"]]) for record in initial]


def _source_update_range(data: dict[str, Any]) -> tuple[str | None, str | None]:
    results = data.get("results")
    if not isinstance(results, list):
        return None, None
    values = sorted(
        str(record["updated_date"])
        for record in results
        if isinstance(record, dict) and record.get("updated_date")
    )
    return (values[0], values[-1]) if values else (None, None)


def _merge_minimum(current: str | None, candidate: str | None) -> str | None:
    values = [value for value in (current, candidate) if value]
    return min(values) if values else None


def _merge_maximum(current: str | None, candidate: str | None) -> str | None:
    values = [value for value in (current, candidate) if value]
    return max(values) if values else None


def _status_payload(plan: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {status: sum(record["status"] == status for record in records) for status in _TERMINAL}
    non_terminal = len(records) - sum(counts.values())
    if counts["failed"]:
        overall = "failed"
    elif counts["blocked"] or non_terminal:
        overall = "blocked"
    else:
        overall = "complete"
    return {
        "schema_version": 1,
        "logical_plan_hash": plan.get("logical_plan_hash"),
        "query_count": len(records),
        "status": overall,
        "status_counts": {**counts, "non_terminal": non_terminal},
        "actual_result_count_including_duplicates": sum(
            int(record["actual_result_count_including_duplicates"]) for record in records
        ),
        "actual_page_count": sum(int(record["actual_page_count"]) for record in records),
        "all_raw_page_checksums_validated": all(
            bool(record["raw_page_checksums_validated"])
            for record in records
            if record["status"] == "complete"
        ),
        "updated_at_utc": _timestamp(),
        "queries": records,
    }


def execute_download_plan(
    plan: dict[str, Any],
    client: OpenAlexClient,
    cache: RawResponseCache,
    *,
    checkpoint_directory: str | Path,
    status_path: str | Path,
    resume: bool = True,
    force: bool = False,
    max_queries: int | None = None,
    workers: int = 1,
) -> dict[str, Any]:
    if max_queries is not None and max_queries < 1:
        raise ValueError("max_queries must be positive")
    if not 1 <= workers <= 16:
        raise ValueError("workers must be between 1 and 16")
    records = (
        _load_existing_records(plan, status_path)
        if resume and not force
        else _initial_records(plan)
    )
    by_id = {record["query_id"]: record for record in records}
    paginator = CursorPaginator(client, cache, checkpoint_directory=checkpoint_directory)
    candidates = []
    for query in plan["queries"]:
        record = by_id[query["query_id"]]
        if record["status"] == "complete" and resume and not force:
            continue
        candidates.append(query)
    selected = candidates[:max_queries] if max_queries is not None else candidates
    selected_ids = {query["query_id"] for query in selected}
    if max_queries is not None:
        for query in candidates:
            if query["query_id"] not in selected_ids:
                by_id[query["query_id"]].update(
                    {
                        "status": "blocked",
                        "status_reason": "not_started_after_max_queries",
                        "updated_at_utc": _timestamp(),
                    }
                )

    def download_one(query: dict[str, Any]) -> str | None:
        record = by_id[query["query_id"]]
        record.update(
            {
                "status": "in_progress",
                "status_reason": "downloading",
                "failure_type": None,
                "updated_at_utc": _timestamp(),
            }
        )
        page_parameters = {
            key: value
            for key, value in query["parameters"].items()
            if key not in {"cursor", "per-page"}
        }

        def update_source_dates(
            data: dict[str, Any],
            page: dict[str, Any],
            current_record: dict[str, Any] = record,
        ) -> None:
            minimum, maximum = _source_update_range(data)
            retrieved = str(page["retrieved_at_utc"])
            current_record["first_retrieved_at_utc"] = _merge_minimum(
                current_record.get("first_retrieved_at_utc"), retrieved
            )
            current_record["last_retrieved_at_utc"] = _merge_maximum(
                current_record.get("last_retrieved_at_utc"), retrieved
            )
            current_record["source_updated_date_min"] = _merge_minimum(
                current_record.get("source_updated_date_min"), minimum
            )
            current_record["source_updated_date_max"] = _merge_maximum(
                current_record.get("source_updated_date_max"), maximum
            )

        try:
            checkpoint = paginator.download(
                query_id=query["query_id"],
                endpoint="/works",
                parameters=page_parameters,
                per_page=int(plan["per_page"]),
                resume=resume,
                force=force,
                page_callback=update_source_dates,
            )
            for page in checkpoint["pages"]:
                entry = cache.validate(page["cache_key"], page["checksum_sha256"])
                update_source_dates(entry.data, page)
            record.update(
                {
                    "status": "complete",
                    "status_reason": "all_pages_validated",
                    "actual_result_count_including_duplicates": checkpoint["result_count"],
                    "actual_page_count": checkpoint["page_count"],
                    "raw_page_checksums_validated": True,
                    "updated_at_utc": _timestamp(),
                }
            )
            return None
        except RateLimitError:
            record.update(
                {
                    "status": "blocked",
                    "status_reason": "rate_limit",
                    "failure_type": "RateLimitError",
                    "updated_at_utc": _timestamp(),
                }
            )
            return "rate_limit"
        except (OpenAlexError, PaginationError, CacheCorruptionError) as exc:
            record.update(
                {
                    "status": "failed",
                    "status_reason": "query_failure",
                    "failure_type": type(exc).__name__,
                    "updated_at_utc": _timestamp(),
                }
            )
            return None

    if workers == 1:
        for query in selected:
            stop_reason = download_one(query)
            _write_status_json(_status_payload(plan, records), status_path)
            if stop_reason == "rate_limit":
                remaining = selected[selected.index(query) + 1 :]
                for blocked_query in remaining:
                    by_id[blocked_query["query_id"]].update(
                        {
                            "status": "blocked",
                            "status_reason": "not_started_after_rate_limit",
                            "updated_at_utc": _timestamp(),
                        }
                    )
                break
    else:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="openalex") as executor:
            futures = {executor.submit(download_one, query): query for query in selected}
            for future in as_completed(futures):
                future.result()
                _write_status_json(_status_payload(plan, records), status_path)
    payload = _status_payload(plan, records)
    _write_status_json(payload, status_path)
    return payload


def _write_status_json(payload: dict[str, Any], path: str | Path) -> None:
    from gisnet.atomic import atomic_write_json

    atomic_write_json(path, payload)


def write_download_status_manifest(
    payload: dict[str, Any],
    *,
    status_path: str | Path,
    plan_path: str | Path,
    download_config_path: str | Path,
    run_id: str,
    command: str,
) -> None:
    # Rewrite the already validated payload so the standard artifact helper emits provenance.
    write_json_artifact(
        path=status_path,
        dataset_name="raw_works_download_status",
        payload=payload,
        records=payload["queries"],
        primary_key=["query_id"],
        run_id=run_id,
        config_hashes={
            "download": config_file_hash(download_config_path),
            "download_plan": config_file_hash(plan_path),
        },
        source_versions={"openalex_works": "retrieved-2026-08-05"},
        source_manifests=[".agent/manifests/download_plan.json"],
        command=command,
    )
