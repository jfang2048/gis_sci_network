import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest

from gisnet.cli import main
from gisnet.openalex.recent import (
    build_recent_query_plan,
    label_recent_normalization,
    latest_completed_month,
    update_recent_ledger,
    write_recent_outputs,
)


def historical_plan() -> dict[str, Any]:
    queries = []
    for country_index, countries in enumerate((["DE", "FR"], ["IT"]), start=1):
        queries.append(
            {
                "query_id": f"W2025_T01_C{country_index:02d}",
                "year": 2025,
                "topic_shard_index": 1,
                "country_shard_index": country_index,
                "topic_ids": ["T1", "T2"],
                "country_codes": countries,
                "parameters": {
                    "filter": "historical",
                    "select": "id,publication_year,publication_date,updated_date",
                    "per-page": 200,
                    "cursor": "*",
                },
            }
        )
    return {
        "schema_version": 1,
        "logical_plan_hash": "historical-2010-2025",
        "start_year": 2025,
        "end_year": 2025,
        "query_count": len(queries),
        "queries": queries,
    }


def download_status(plan: dict[str, Any], *, complete_months: set[str]) -> dict[str, Any]:
    records = []
    for query in plan["queries"]:
        complete = query["month"] in complete_months
        records.append(
            {
                "query_id": query["query_id"],
                "status": "complete" if complete else "blocked",
                "status_reason": "all_pages_validated" if complete else "not_started",
                "actual_result_count_including_duplicates": 1 if complete else 0,
                "actual_page_count": 1 if complete else 0,
                "first_retrieved_at_utc": "2026-03-15T10:00:00Z" if complete else None,
                "last_retrieved_at_utc": "2026-03-15T10:01:00Z" if complete else None,
                "source_updated_date_min": None,
                "source_updated_date_max": None,
                "raw_page_checksums_validated": complete,
                "failure_type": None,
                "updated_at_utc": "2026-03-15T10:01:00Z",
            }
        )
    return {
        "schema_version": 1,
        "logical_plan_hash": plan["logical_plan_hash"],
        "query_count": len(records),
        "status": "complete" if len(complete_months) == plan["date_range_count"] else "blocked",
        "queries": records,
    }


def test_latest_completed_month_handles_year_boundary() -> None:
    assert latest_completed_month(date(2026, 1, 1)) == (
        date(2025, 12, 1),
        date(2025, 12, 31),
    )
    assert latest_completed_month(date(2026, 8, 28)) == (
        date(2026, 7, 1),
        date(2026, 7, 31),
    )


def test_recent_plan_uses_exact_completed_months_and_supported_page_size() -> None:
    plan = build_recent_query_plan(historical_plan(), as_of=date(2026, 3, 15))

    assert plan["latest_completed_month"] == "2026-02"
    assert plan["date_range_count"] == 2
    assert plan["query_count"] == 4
    assert plan["per_page"] == 100
    assert plan["deduplication_key"] == "work_id"
    assert plan["raw_partial_year_comparison_allowed"] is False
    assert all(query["parameters"]["per-page"] == 100 for query in plan["queries"])
    assert all("publication_year" not in query["parameters"]["filter"] for query in plan["queries"])
    assert plan["queries"][0]["parameters"]["filter"].startswith(
        "from_publication_date:2026-01-01,to_publication_date:2026-01-31,"
    )

    with pytest.raises(ValueError, match="between 1 and 100"):
        build_recent_query_plan(historical_plan(), as_of=date(2026, 3, 15), per_page=200)


def test_ledger_records_only_complete_months_and_rerun_plans_only_missing_ranges() -> None:
    base = historical_plan()
    first_plan = build_recent_query_plan(base, as_of=date(2026, 3, 15))
    ledger, added = update_recent_ledger(
        base,
        None,
        first_plan,
        download_status(first_plan, complete_months={"2026-01"}),
    )

    assert added == ["2026-01"]
    assert ledger["coverage_start"] == "2026-01-01"
    assert ledger["window_end"] == "2026-01-31"
    assert ledger["retrieval_date"] == "2026-03-15"
    assert ledger["is_partial_current_year"] is True
    assert ledger["raw_partial_year_comparison_allowed"] is False

    second_plan = build_recent_query_plan(base, ledger, as_of=date(2026, 3, 20))
    assert [item["month"] for item in second_plan["date_ranges"]] == ["2026-02"]
    assert second_plan["query_count"] == 2
    assert {query["month"] for query in second_plan["queries"]} == {"2026-02"}
    assert "api_key" not in json.dumps(ledger).lower()


def test_ledger_never_advances_coverage_past_an_incomplete_month() -> None:
    base = historical_plan()
    plan = build_recent_query_plan(base, as_of=date(2026, 4, 15))
    ledger, added = update_recent_ledger(
        base,
        None,
        plan,
        download_status(plan, complete_months={"2026-01", "2026-03"}),
    )

    assert added == ["2026-01"]
    assert ledger["completed_months"] == ["2026-01"]
    assert ledger["coverage_end"] == "2026-01-31"

    corrupted = json.loads(json.dumps(ledger))
    corrupted["coverage_end"] = "2026-02-28"
    with pytest.raises(ValueError, match="coverage labels"):
        build_recent_query_plan(base, corrupted, as_of=date(2026, 4, 15))


def test_recent_normalization_labels_and_rejects_dates_outside_coverage(
    tmp_path: Path,
) -> None:
    base = historical_plan()
    plan = build_recent_query_plan(base, as_of=date(2026, 2, 15))
    ledger, _ = update_recent_ledger(
        base,
        None,
        plan,
        download_status(plan, complete_months={"2026-01"}),
    )
    works = tmp_path / "works.parquet"
    connection = duckdb.connect()
    try:
        connection.execute(
            "COPY (SELECT 'W1'::VARCHAR AS work_id, 2026::INTEGER AS publication_year, "
            "'2026-01-12'::VARCHAR AS publication_date) TO ? (FORMAT PARQUET)",
            [str(works)],
        )
    finally:
        connection.close()
    summary = {
        "logical_input_hash": "recent-normalization",
        "outputs": {"works": str(works)},
        "work_count": 1,
    }

    labelled = label_recent_normalization(summary, ledger)
    assert labelled["window_end"] == "2026-01-31"
    assert labelled["date_coverage"] == "completed_calendar_months"
    assert labelled["historical_complete_year_outputs_modified"] is False

    connection = duckdb.connect()
    try:
        connection.execute(
            "COPY (SELECT 'W2'::VARCHAR AS work_id, 2026::INTEGER AS publication_year, "
            "'2026-02-01'::VARCHAR AS publication_date) TO ? (FORMAT PARQUET)",
            [str(works)],
        )
    finally:
        connection.close()
    with pytest.raises(ValueError, match="exceed completed-month"):
        label_recent_normalization(summary, ledger)


def test_recent_sync_dry_run_preserves_historical_plan(tmp_path: Path, capsys: Any) -> None:
    historical = tmp_path / "download_plan.json"
    registry = tmp_path / "topics.yml"
    recent_plan = tmp_path / "recent_plan.json"
    ledger = tmp_path / "ledger.json"
    historical.write_text(json.dumps(historical_plan()), encoding="utf-8")
    registry.write_text("topics: []\n", encoding="utf-8")
    before = hashlib.sha256(historical.read_bytes()).hexdigest()

    result = main(
        [
            "sync-recent-works",
            "--dry-run",
            "--as-of",
            "2026-03-15",
            "--plan",
            str(historical),
            "--registry",
            str(registry),
            "--recent-plan",
            str(recent_plan),
            "--ledger",
            str(ledger),
        ]
    )

    assert result == 0
    assert hashlib.sha256(historical.read_bytes()).hexdigest() == before
    assert not recent_plan.exists()
    assert not ledger.exists()
    assert "no request or write performed" in capsys.readouterr().out


def test_recent_sync_rejects_as_of_override_for_a_real_run(capsys: Any) -> None:
    assert main(["sync-recent-works", "--as-of", "2026-03-15"]) == 2
    assert "restricted to dry-run" in capsys.readouterr().err


def test_recent_output_writer_uses_distinct_manifests(tmp_path: Path, monkeypatch: Any) -> None:
    base = historical_plan()
    plan = build_recent_query_plan(base, as_of=date(2026, 2, 15))
    ledger, _ = update_recent_ledger(
        base,
        None,
        plan,
        download_status(plan, complete_months={"2026-01"}),
    )
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    works = outputs / "works.parquet"
    topics = outputs / "work_topics.parquet"
    malformed = outputs / "work_malformed.parquet"
    connection = duckdb.connect()
    try:
        connection.execute(
            "COPY (SELECT 'W1'::VARCHAR AS work_id, 2026::INTEGER AS publication_year, "
            "'2026-01-12'::VARCHAR AS publication_date) TO ? (FORMAT PARQUET)",
            [str(works)],
        )
        connection.execute(
            "COPY (SELECT 'W1'::VARCHAR AS work_id, 'T1'::VARCHAR AS topic_id) "
            "TO ? (FORMAT PARQUET)",
            [str(topics)],
        )
        connection.execute(
            "COPY (SELECT NULL::VARCHAR AS record_key, NULL::VARCHAR AS reason WHERE false) "
            "TO ? (FORMAT PARQUET)",
            [str(malformed)],
        )
    finally:
        connection.close()
    summary = {
        "logical_input_hash": "recent-normalization",
        "outputs": {
            "works": str(works),
            "work_topics": str(topics),
            "work_malformed": str(malformed),
        },
    }
    project = tmp_path / "project.yml"
    project.write_text("project_version: 0.1.0\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    write_recent_outputs(
        summary,
        ledger,
        ledger_path=tmp_path / "ledger.json",
        summary_path=tmp_path / "summary.json",
        project_config_path=project,
        run_id="test-run",
        command="test recent sync",
    )

    manifest_names = {path.name for path in (tmp_path / ".agent/manifests").glob("*.json")}
    assert {
        "recent_retrieval_ledger.json",
        "recent_works_normalization_summary.json",
        "recent_works.json",
        "recent_work_topics.json",
        "recent_work_malformed.json",
    } <= manifest_names
    assert "works.json" not in manifest_names
