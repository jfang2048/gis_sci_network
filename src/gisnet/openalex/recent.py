"""Incremental OpenAlex acquisition for fully completed calendar months.

This module deliberately keeps recent, partial-year data separate from the frozen
2010-2025 annual corpus.  Publication-date month shards are appended to a ledger
only after every query page for that month has been validated.
"""

from __future__ import annotations

import calendar
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import load_json_object, write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import write_parquet_manifest

_PLAN_VERSION = "recent-publication-months-2026-08-28-v1"
_LEDGER_VERSION = "recent-publication-month-ledger-2026-08-28-v1"
_MAX_SUPPORTED_PER_PAGE = 100


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _month_end(value: date) -> date:
    return date(value.year, value.month, calendar.monthrange(value.year, value.month)[1])


def _next_month(value: date) -> date:
    return date(value.year + (value.month == 12), 1 if value.month == 12 else value.month + 1, 1)


def latest_completed_month(as_of: date | datetime | None = None) -> tuple[date, date]:
    """Return the first and last day of the latest fully completed UTC month."""
    if as_of is None:
        current = datetime.now(UTC).date()
    elif isinstance(as_of, datetime):
        current = as_of.astimezone(UTC).date() if as_of.tzinfo else as_of.date()
    else:
        current = as_of
    end = date(current.year, current.month, 1) - timedelta(days=1)
    return end.replace(day=1), end


def load_recent_ledger(
    path: str | Path,
    *,
    historical_plan: dict[str, Any],
) -> dict[str, Any] | None:
    source = Path(path)
    if not source.exists():
        return None
    ledger = load_json_object(source)
    _validate_ledger(ledger, historical_plan=historical_plan)
    return ledger


def _historical_end(historical_plan: dict[str, Any]) -> date:
    end_year = historical_plan.get("end_year")
    if not isinstance(end_year, int):
        raise ValueError("historical download plan lacks an integer end_year")
    return date(end_year, 12, 31)


def _base_shards(historical_plan: dict[str, Any]) -> list[dict[str, Any]]:
    queries = historical_plan.get("queries")
    if not isinstance(queries, list) or not queries:
        raise ValueError("historical download plan has no query shards")
    start_year = historical_plan.get("start_year")
    candidates = [query for query in queries if query.get("year") == start_year]
    if not candidates:
        raise ValueError("historical download plan lacks base-year query shards")
    shards: list[dict[str, Any]] = []
    seen: set[tuple[int, int]] = set()
    for query in candidates:
        topic_index = query.get("topic_shard_index")
        country_index = query.get("country_shard_index")
        topics = query.get("topic_ids")
        countries = query.get("country_codes")
        parameters = query.get("parameters")
        if (
            not isinstance(topic_index, int)
            or not isinstance(country_index, int)
            or not isinstance(topics, list)
            or not topics
            or not isinstance(countries, list)
            or not countries
            or not isinstance(parameters, dict)
            or not isinstance(parameters.get("select"), str)
        ):
            raise ValueError("historical download plan contains an invalid query shard")
        key = (topic_index, country_index)
        if key in seen:
            raise ValueError("historical base-year query shards are not unique")
        seen.add(key)
        shards.append(
            {
                "topic_shard_index": topic_index,
                "country_shard_index": country_index,
                "topic_ids": sorted(map(str, topics)),
                "country_codes": sorted(map(str, countries)),
                "select": str(parameters["select"]),
            }
        )
    return sorted(
        shards,
        key=lambda item: (item["topic_shard_index"], item["country_shard_index"]),
    )


def _month_starts(first: date, last: date) -> list[date]:
    if first.day != 1 or last != _month_end(last):
        raise ValueError("recent retrieval boundaries must cover complete calendar months")
    values: list[date] = []
    cursor = first
    while cursor <= last:
        values.append(cursor)
        cursor = _next_month(cursor)
    return values


def _query_id(month: date, topic_index: int, country_index: int, filter_value: str) -> str:
    suffix = semantic_hash(filter_value)[:10]
    return f"RM{month:%Y%m}_T{topic_index:02d}_C{country_index:02d}_{suffix}"


def build_recent_query_plan(
    historical_plan: dict[str, Any],
    ledger: dict[str, Any] | None = None,
    *,
    as_of: date | datetime | None = None,
    per_page: int = _MAX_SUPPORTED_PER_PAGE,
) -> dict[str, Any]:
    """Build query shards only for completed months absent from the ledger."""
    if not 1 <= per_page <= _MAX_SUPPORTED_PER_PAGE:
        raise ValueError("recent OpenAlex per_page must be between 1 and 100")
    if ledger is not None:
        _validate_ledger(ledger, historical_plan=historical_plan)
    if as_of is None:
        as_of_date = datetime.now(UTC).date()
    elif isinstance(as_of, datetime):
        as_of_date = as_of.astimezone(UTC).date() if as_of.tzinfo else as_of.date()
    else:
        as_of_date = as_of
    _, completed_end = latest_completed_month(as_of_date)
    historical_end = _historical_end(historical_plan)
    completed_months = {
        str(item["month"])
        for item in (ledger or {}).get("completed_ranges", [])
        if isinstance(item, dict) and isinstance(item.get("month"), str)
    }
    if completed_months and max(completed_months) > completed_end.strftime("%Y-%m"):
        raise ValueError("as_of date predates completed recent-ledger coverage")
    first_recent = _next_month(historical_end)
    month_starts = (
        _month_starts(first_recent, completed_end) if first_recent <= completed_end else []
    )
    missing_months = [
        month for month in month_starts if month.strftime("%Y-%m") not in completed_months
    ]
    shards = _base_shards(historical_plan)
    queries: list[dict[str, Any]] = []
    ranges: list[dict[str, Any]] = []
    for month in missing_months:
        range_end = _month_end(month)
        query_ids: list[str] = []
        for shard in shards:
            filter_value = (
                f"from_publication_date:{month.isoformat()},"
                f"to_publication_date:{range_end.isoformat()},"
                f"topics.id:{'|'.join(shard['topic_ids'])},"
                "authorships.institutions.country_code:"
                f"{'|'.join(shard['country_codes'])}"
            )
            query_id = _query_id(
                month,
                int(shard["topic_shard_index"]),
                int(shard["country_shard_index"]),
                filter_value,
            )
            query_ids.append(query_id)
            parameters = {
                "filter": filter_value,
                "select": shard["select"],
                "per-page": per_page,
                "cursor": "*",
            }
            queries.append(
                {
                    "query_id": query_id,
                    "month": month.strftime("%Y-%m"),
                    "range_start": month.isoformat(),
                    "range_end": range_end.isoformat(),
                    "topic_shard_index": shard["topic_shard_index"],
                    "country_shard_index": shard["country_shard_index"],
                    "topic_ids": shard["topic_ids"],
                    "country_codes": shard["country_codes"],
                    "parameters": parameters,
                    "query_hash": semantic_hash(parameters),
                    "predicted_result_count": None,
                    "predicted_page_count": None,
                }
            )
        ranges.append(
            {
                "month": month.strftime("%Y-%m"),
                "range_start": month.isoformat(),
                "range_end": range_end.isoformat(),
                "query_ids": query_ids,
            }
        )
    plan = {
        "schema_version": 1,
        "plan_version": _PLAN_VERSION,
        "historical_plan_hash": historical_plan.get("logical_plan_hash"),
        "historical_coverage_end": historical_end.isoformat(),
        "as_of_date": as_of_date.isoformat(),
        "latest_completed_month": completed_end.strftime("%Y-%m"),
        "latest_completed_month_end": completed_end.isoformat(),
        "date_filter_field": "publication_date",
        "date_filter_semantics": "complete_calendar_months",
        "late_index_backfill_policy": "not_inferred; rerun a range explicitly if policy changes",
        "per_page": per_page,
        "query_count": len(queries),
        "date_range_count": len(ranges),
        "date_ranges": ranges,
        "duplicate_coverage_expected": True,
        "deduplication_key": "work_id",
        "raw_partial_year_comparison_allowed": False,
        "queries": queries,
    }
    plan["logical_plan_hash"] = semantic_hash(
        {
            "plan_version": plan["plan_version"],
            "historical_plan_hash": plan["historical_plan_hash"],
            "historical_coverage_end": plan["historical_coverage_end"],
            "per_page": plan["per_page"],
            "date_ranges": plan["date_ranges"],
            "queries": plan["queries"],
        }
    )
    validate_recent_query_plan(plan)
    return plan


def validate_recent_query_plan(plan: dict[str, Any]) -> None:
    if plan.get("per_page", 0) not in range(1, _MAX_SUPPORTED_PER_PAGE + 1):
        raise ValueError("recent query plan exceeds the supported page size")
    queries = plan.get("queries")
    ranges = plan.get("date_ranges")
    if not isinstance(queries, list) or len(queries) != plan.get("query_count"):
        raise ValueError("recent query_count does not match query records")
    if not isinstance(ranges, list) or len(ranges) != plan.get("date_range_count"):
        raise ValueError("recent date_range_count does not match range records")
    query_ids = [query.get("query_id") for query in queries]
    if len(query_ids) != len(set(query_ids)):
        raise ValueError("recent query IDs must be unique")
    by_id = {query["query_id"]: query for query in queries}
    referenced_query_ids: list[str] = []
    historical_end = date.fromisoformat(str(plan["historical_coverage_end"]))
    latest_end = date.fromisoformat(str(plan["latest_completed_month_end"]))
    for date_range in ranges:
        start = date.fromisoformat(str(date_range["range_start"]))
        end = date.fromisoformat(str(date_range["range_end"]))
        unsafe = (
            start.day != 1
            or end != _month_end(start)
            or start <= historical_end
            or end > latest_end
        )
        if unsafe:
            raise ValueError("recent plan contains an unsafe calendar-month range")
        expected_ids = date_range.get("query_ids")
        if not isinstance(expected_ids, list) or not expected_ids:
            raise ValueError("recent date range has no query shards")
        for query_id in expected_ids:
            referenced_query_ids.append(str(query_id))
            query = by_id.get(query_id)
            if query is None:
                raise ValueError("recent date range references an unknown query")
            expected_prefix = (
                f"from_publication_date:{start.isoformat()},to_publication_date:{end.isoformat()},"
            )
            if not str(query["parameters"]["filter"]).startswith(expected_prefix):
                raise ValueError("recent query does not use its exact publication-date range")
            if query["parameters"].get("per-page") != plan["per_page"]:
                raise ValueError("recent query page size differs from the plan")
    if referenced_query_ids != query_ids:
        raise ValueError("recent query records do not match ordered date-range coverage")


def update_recent_ledger(
    historical_plan: dict[str, Any],
    previous: dict[str, Any] | None,
    plan: dict[str, Any],
    status: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Append only fully validated months and return their YYYY-MM identifiers."""
    validate_recent_query_plan(plan)
    if previous is not None:
        _validate_ledger(previous, historical_plan=historical_plan)
    if status.get("logical_plan_hash") != plan.get("logical_plan_hash"):
        raise ValueError("recent download status belongs to a different query plan")
    records = status.get("queries")
    if not isinstance(records, list):
        raise ValueError("recent download status lacks query records")
    by_id = {record.get("query_id"): record for record in records if isinstance(record, dict)}
    completed = [dict(item) for item in (previous or {}).get("completed_ranges", [])]
    completed_months = {str(item["month"]) for item in completed}
    newly_completed: list[str] = []
    query_by_id = {query["query_id"]: query for query in plan["queries"]}
    for date_range in plan["date_ranges"]:
        month = str(date_range["month"])
        if month in completed_months:
            continue
        query_ids = list(map(str, date_range["query_ids"]))
        possible_records = [by_id.get(query_id) for query_id in query_ids]
        if any(record is None or record.get("status") != "complete" for record in possible_records):
            # Coverage is a contiguous prefix. Later completed months remain in raw
            # cache/checkpoints and will be admitted after this gap is recovered.
            break
        range_records = [record for record in possible_records if record is not None]
        if any(not record.get("raw_page_checksums_validated") for record in range_records):
            raise ValueError("complete recent query lacks validated raw page checksums")
        retrievals = sorted(
            str(value)
            for record in range_records
            for value in (record.get("first_retrieved_at_utc"), record.get("last_retrieved_at_utc"))
            if value
        )
        completed.append(
            {
                **date_range,
                "completed_at_utc": _timestamp(),
                "first_retrieved_at_utc": retrievals[0] if retrievals else None,
                "last_retrieved_at_utc": retrievals[-1] if retrievals else None,
                "queries": [query_by_id[query_id] for query_id in query_ids],
            }
        )
        completed_months.add(month)
        newly_completed.append(month)
    completed.sort(key=lambda item: str(item["month"]))
    retrievals = sorted(
        str(value)
        for item in completed
        for value in (item.get("first_retrieved_at_utc"), item.get("last_retrieved_at_utc"))
        if value
    )
    coverage_end = completed[-1]["range_end"] if completed else None
    as_of_year = int(str(plan["as_of_date"])[:4])
    partial = bool(coverage_end and int(str(coverage_end)[:4]) == as_of_year)
    ledger = {
        "schema_version": 1,
        "ledger_version": _LEDGER_VERSION,
        "historical_plan_hash": historical_plan.get("logical_plan_hash"),
        "historical_coverage_end": _historical_end(historical_plan).isoformat(),
        "retrieval_date": retrievals[-1][:10] if retrievals else None,
        "first_retrieved_at_utc": retrievals[0] if retrievals else None,
        "last_retrieved_at_utc": retrievals[-1] if retrievals else None,
        "coverage_start": completed[0]["range_start"] if completed else None,
        "coverage_end": coverage_end,
        "window_end": coverage_end,
        "date_coverage": "completed_calendar_months",
        "completed_month_count": len(completed),
        "completed_months": [item["month"] for item in completed],
        "latest_completed_month_at_run": plan["latest_completed_month"],
        "is_partial_current_year": partial,
        "current_year_state": "partial_through_completed_month" if partial else "not_available",
        "raw_partial_year_comparison_allowed": False,
        "completed_ranges": completed,
        "updated_at_utc": _timestamp(),
    }
    logical_ledger = {
        key: value for key, value in ledger.items() if key not in {"ledger_hash", "updated_at_utc"}
    }
    ledger["ledger_hash"] = semantic_hash(logical_ledger)
    _validate_ledger(ledger, historical_plan=historical_plan)
    return ledger, newly_completed


def _validate_ledger(ledger: dict[str, Any], *, historical_plan: dict[str, Any]) -> None:
    if ledger.get("ledger_version") != _LEDGER_VERSION:
        raise ValueError("recent ledger version is unsupported")
    if ledger.get("historical_plan_hash") != historical_plan.get("logical_plan_hash"):
        raise ValueError("recent ledger belongs to a different historical download plan")
    if ledger.get("historical_coverage_end") != _historical_end(historical_plan).isoformat():
        raise ValueError("recent ledger historical boundary differs from the plan")
    ranges = ledger.get("completed_ranges")
    if not isinstance(ranges, list):
        raise ValueError("recent ledger lacks completed ranges")
    months: list[str] = []
    query_ids: list[str] = []
    previous_end = _historical_end(historical_plan)
    for item in ranges:
        if not isinstance(item, dict):
            raise ValueError("recent ledger range is not an object")
        start = date.fromisoformat(str(item["range_start"]))
        end = date.fromisoformat(str(item["range_end"]))
        month = str(item["month"])
        if start.day != 1 or end != _month_end(start) or month != start.strftime("%Y-%m"):
            raise ValueError("recent ledger range is not a complete calendar month")
        if start != _next_month(previous_end):
            raise ValueError("recent ledger ranges must be contiguous and ordered")
        previous_end = end
        queries = item.get("queries")
        if not isinstance(queries, list) or not queries:
            raise ValueError("recent ledger completed range lacks its query records")
        expected_query_ids = item.get("query_ids")
        actual_query_ids = [str(query.get("query_id")) for query in queries]
        if expected_query_ids != actual_query_ids:
            raise ValueError("recent ledger range query IDs differ from its query records")
        expected_prefix = (
            f"from_publication_date:{start.isoformat()},to_publication_date:{end.isoformat()},"
        )
        if any(
            not str(query.get("parameters", {}).get("filter", "")).startswith(expected_prefix)
            for query in queries
        ):
            raise ValueError("recent ledger query exceeds its publication-date range")
        months.append(month)
        query_ids.extend(actual_query_ids)
    if len(months) != len(set(months)) or len(query_ids) != len(set(query_ids)):
        raise ValueError("recent ledger contains duplicate months or query IDs")
    if ledger.get("completed_month_count") != len(ranges):
        raise ValueError("recent ledger month count does not match its ranges")
    if ledger.get("completed_months") != months:
        raise ValueError("recent ledger completed-month labels differ from its ranges")
    expected_start = ranges[0]["range_start"] if ranges else None
    expected_end = ranges[-1]["range_end"] if ranges else None
    if ledger.get("coverage_start") != expected_start or ledger.get("coverage_end") != expected_end:
        raise ValueError("recent ledger coverage labels differ from its ranges")
    if ledger.get("window_end") != expected_end:
        raise ValueError("recent ledger window end differs from completed coverage")
    logical_ledger = {
        key: value for key, value in ledger.items() if key not in {"ledger_hash", "updated_at_utc"}
    }
    if ledger.get("ledger_hash") != semantic_hash(logical_ledger):
        raise ValueError("recent ledger hash does not match its logical content")


def completed_recent_plan(ledger: dict[str, Any]) -> dict[str, Any]:
    queries = [query for item in ledger["completed_ranges"] for query in item["queries"]]
    plan = {
        "schema_version": 1,
        "plan_version": ledger["ledger_version"],
        "logical_plan_hash": ledger["ledger_hash"],
        "query_count": len(queries),
        "per_page": _MAX_SUPPORTED_PER_PAGE,
        "queries": queries,
    }
    return plan


def label_recent_normalization(
    summary: dict[str, Any],
    ledger: dict[str, Any],
) -> dict[str, Any]:
    """Validate exact-date coverage and attach safe partial-year labels."""
    ranges = [
        (date.fromisoformat(item["range_start"]), date.fromisoformat(item["range_end"]))
        for item in ledger["completed_ranges"]
    ]
    works_path = Path(summary["outputs"]["works"])
    connection = duckdb.connect()
    try:
        invalid_date_row = connection.execute(
            """
            SELECT count(*)
            FROM read_parquet(?)
            WHERE try_cast(publication_date AS DATE) IS NULL
            """,
            [str(works_path)],
        ).fetchone()
        predicates = " OR ".join(
            "try_cast(publication_date AS DATE) BETWEEN cast(? AS DATE) AND cast(? AS DATE)"
            for _ in ranges
        )
        parameters: list[Any] = [str(works_path)]
        for start, end in ranges:
            parameters.extend([start.isoformat(), end.isoformat()])
        outside_row = connection.execute(
            f"SELECT count(*) FROM read_parquet(?) WHERE NOT ({predicates})",
            parameters,
        ).fetchone()
    finally:
        connection.close()
    if invalid_date_row is None or invalid_date_row[0]:
        raise ValueError("recent normalized Works contain missing or invalid publication dates")
    if outside_row is None or outside_row[0]:
        raise ValueError("recent normalized Works exceed completed-month date coverage")
    return {
        **summary,
        "retrieval_date": ledger["retrieval_date"],
        "window_end": ledger["window_end"],
        "coverage_start": ledger["coverage_start"],
        "coverage_end": ledger["coverage_end"],
        "date_coverage": ledger["date_coverage"],
        "completed_months": ledger["completed_months"],
        "is_partial_current_year": ledger["is_partial_current_year"],
        "current_year_state": ledger["current_year_state"],
        "raw_partial_year_comparison_allowed": False,
        "historical_complete_year_outputs_modified": False,
    }


def write_recent_plan(
    plan: dict[str, Any],
    *,
    path: str | Path,
    historical_plan_path: str | Path,
    run_id: str,
    command: str,
) -> None:
    write_json_artifact(
        path=path,
        dataset_name="recent_download_plan",
        payload=plan,
        records=plan["queries"],
        primary_key=["query_id"],
        run_id=run_id,
        config_hashes={"historical_download_plan": config_file_hash(historical_plan_path)},
        source_versions={"openalex_date_filter": "publication_date-completed-calendar-months"},
        source_manifests=[".agent/manifests/download_plan.json"],
        command=command,
    )


def write_recent_status(
    status: dict[str, Any],
    *,
    path: str | Path,
    plan: dict[str, Any],
    run_id: str,
    command: str,
) -> None:
    write_json_artifact(
        path=path,
        dataset_name="recent_raw_works_download_status",
        payload=status,
        records=status["queries"],
        primary_key=["query_id"],
        run_id=run_id,
        config_hashes={"recent_download_plan": str(plan["logical_plan_hash"])},
        source_versions={"openalex_works": "retrieved-at-runtime"},
        source_manifests=[".agent/manifests/recent_download_plan.json"],
        command=command,
    )


def write_recent_outputs(
    summary: dict[str, Any],
    ledger: dict[str, Any],
    *,
    ledger_path: str | Path,
    summary_path: str | Path,
    project_config_path: str | Path,
    run_id: str,
    command: str,
) -> None:
    config_hashes = {
        "project": config_file_hash(project_config_path),
        "recent_ledger": str(ledger["ledger_hash"]),
    }
    write_json_artifact(
        path=ledger_path,
        dataset_name="recent_retrieval_ledger",
        payload=ledger,
        records=ledger["completed_ranges"],
        primary_key=["month"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions={"openalex_works": "retrieved-at-runtime"},
        source_manifests=[".agent/manifests/recent_raw_works_download_status.json"],
        command=command,
    )
    write_json_artifact(
        path=summary_path,
        dataset_name="recent_works_normalization_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions={"openalex_works": "retrieved-at-runtime"},
        source_manifests=[".agent/manifests/recent_retrieval_ledger.json"],
        command=command,
    )
    contracts = {
        "works": ("recent_works", ["work_id"], {"work_id", "publication_date"}, "publication_year"),
        "work_topics": (
            "recent_work_topics",
            ["work_id", "topic_id"],
            {"work_id", "topic_id"},
            None,
        ),
        "work_malformed": (
            "recent_work_malformed",
            ["record_key"],
            {"record_key", "reason"},
            None,
        ),
    }
    for output_name, raw_path in summary["outputs"].items():
        dataset_name, primary_key, required, year_column = contracts[output_name]
        write_parquet_manifest(
            path=raw_path,
            dataset_name=dataset_name,
            primary_key=primary_key,
            required_columns=required,
            year_column=year_column,
            run_id=run_id,
            config_hashes=config_hashes,
            source_manifests=[".agent/manifests/recent_retrieval_ledger.json"],
            source_versions={"openalex_works": "retrieved-at-runtime"},
            command=command,
        )
