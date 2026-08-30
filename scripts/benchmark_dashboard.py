"""Measure complete-school dashboard query latency and returned-frame memory."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd

from gisnet.visualization.school_ego_map import query_school_ego_partners
from gisnet.visualization.school_profile import (
    query_school_profiles,
    query_school_topics_for_schools,
)

INDEX_MEDIAN_BUDGET_MS = 250.0
INDEX_MEMORY_BUDGET_BYTES = 32 * 1024 * 1024
QUERY_MEDIAN_BUDGET_MS = 100.0
QUERY_MEMORY_BUDGET_BYTES = 128 * 1024
EGO_ROW_LIMIT = 50


def _frame_memory(frame: pd.DataFrame) -> int:
    return int(frame.memory_usage(index=True, deep=True).sum())


def _benchmark_frame(
    operation: Callable[[], pd.DataFrame],
    *,
    samples: int,
) -> dict[str, Any]:
    if samples <= 0:
        raise ValueError("benchmark samples must be positive")
    timings: list[float] = []
    frame = pd.DataFrame()
    for _ in range(samples):
        started = time.perf_counter()
        frame = operation()
        timings.append((time.perf_counter() - started) * 1000)
    return {
        "median_ms": round(statistics.median(timings), 3),
        "maximum_ms": round(max(timings), 3),
        "rows": len(frame),
        "result_memory_bytes": _frame_memory(frame),
        "samples_ms": [round(value, 3) for value in timings],
    }


def _representative_school_ids(data_directory: Path) -> list[str]:
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """
            SELECT school_id
            FROM read_parquet(?)
            WHERE has_retained_ego_partners
            ORDER BY recent_24m_work_count DESC, school_id
            LIMIT 4
            """,
            [str(data_directory / "school_index.parquet")],
        ).fetchall()
    finally:
        connection.close()
    values = [str(row[0]) for row in rows]
    if len(values) != 4:
        raise ValueError("dashboard benchmark requires four schools with retained partner evidence")
    return values


def _within_budget(name: str, result: dict[str, Any]) -> bool:
    if name == "school_index_full_load":
        return bool(
            result["median_ms"] < INDEX_MEDIAN_BUDGET_MS
            and result["result_memory_bytes"] < INDEX_MEMORY_BUDGET_BYTES
        )
    row_limit_ok = name != "ego_one" or result["rows"] <= EGO_ROW_LIMIT
    return bool(
        result["median_ms"] < QUERY_MEDIAN_BUDGET_MS
        and result["result_memory_bytes"] < QUERY_MEMORY_BUDGET_BYTES
        and row_limit_ok
    )


def run_benchmark(data_directory: Path, *, samples: int) -> dict[str, Any]:
    school_ids = _representative_school_ids(data_directory)
    profile_path = data_directory / "school_profiles.parquet"
    topic_path = data_directory / "school_topic_profiles.parquet"
    partner_path = data_directory / "school_ego_partners.parquet"
    results = {
        "school_index_full_load": _benchmark_frame(
            lambda: pd.read_parquet(data_directory / "school_index.parquet"),
            samples=min(samples, 3),
        ),
        "profile_one": _benchmark_frame(
            lambda: query_school_profiles(
                profile_path,
                school_ids=school_ids[:1],
                corpus_view="broad",
                window_months=24,
            ),
            samples=samples,
        ),
        "profile_four": _benchmark_frame(
            lambda: query_school_profiles(
                profile_path,
                school_ids=school_ids,
                corpus_view="broad",
                window_months=24,
            ),
            samples=samples,
        ),
        "topics_one": _benchmark_frame(
            lambda: query_school_topics_for_schools(
                topic_path,
                school_ids=school_ids[:1],
                corpus_view="broad",
                window_months=24,
            ),
            samples=samples,
        ),
        "topics_four": _benchmark_frame(
            lambda: query_school_topics_for_schools(
                topic_path,
                school_ids=school_ids,
                corpus_view="broad",
                window_months=24,
            ),
            samples=samples,
        ),
        "ego_one": _benchmark_frame(
            lambda: query_school_ego_partners(
                partner_path,
                school_id=school_ids[0],
                corpus_view="broad",
                period_key="rolling_24m",
            ),
            samples=samples,
        ),
    }
    checks = {name: _within_budget(name, result) for name, result in results.items()}
    return {
        "schema_version": 1,
        "data_directory": data_directory.as_posix(),
        "samples_per_predicate_query": samples,
        "representative_school_ids": school_ids,
        "budgets": {
            "school_index_median_ms": INDEX_MEDIAN_BUDGET_MS,
            "school_index_memory_bytes": INDEX_MEMORY_BUDGET_BYTES,
            "predicate_query_median_ms": QUERY_MEDIAN_BUDGET_MS,
            "predicate_result_memory_bytes": QUERY_MEMORY_BUDGET_BYTES,
            "ego_partner_row_limit": EGO_ROW_LIMIT,
        },
        "results": results,
        "checks": checks,
        "all_budgets_passed": all(checks.values()),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-directory", type=Path, default=Path("dashboard/data"))
    parser.add_argument("--samples", type=int, default=9)
    args = parser.parse_args(argv)
    payload = run_benchmark(args.data_directory, samples=args.samples)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload["all_budgets_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
