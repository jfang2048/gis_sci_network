from pathlib import Path
from typing import Any

import pytest

from gisnet.cli import main
from gisnet.geography import load_region_registry
from gisnet.openalex.cache import RawResponseCache
from gisnet.openalex.client import OpenAlexResponse
from gisnet.openalex.planner import (
    DownloadPlannerConfig,
    build_query_plan,
    preview_query_plan,
    validate_query_plan,
)


class CountClient:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, _: str, *, params: dict[str, Any]) -> OpenAlexResponse:
        self.calls += 1
        count = 201 if "publication_year:2020" in str(params["filter"]) else 0
        return OpenAlexResponse(
            data={"meta": {"count": count, "cost_usd": 0.0001}, "results": []},
            status_code=200,
            retrieved_at_utc="2026-08-05T00:00:00Z",
            rate_limit={},
        )


def planner_config() -> DownloadPlannerConfig:
    return DownloadPlannerConfig(
        plan_version="test-v1",
        topic_shard_size=1,
        country_shard_size=25,
        source_max_or_values=50,
        per_page=200,
        estimated_page_cost_usd=0.001,
        target_macro_regions=["Europe"],
        select_fields=["id", "topics", "authorships"],
    )


def registry() -> dict[str, Any]:
    return {
        "registry_hash": "registry-test",
        "strict_topic_ids": ["T1"],
        "broad_topic_ids": ["T2", "T1"],
    }


def test_query_plan_is_deterministic_bounded_and_complete() -> None:
    regions = load_region_registry()
    config = planner_config()
    first = build_query_plan(
        registry(), regions, config, start_year=2020, end_year=2021, corpus="all"
    )
    second = build_query_plan(
        registry(), regions, config, start_year=2020, end_year=2021, corpus="all"
    )

    assert first == second
    assert first["covers_corpus_views"] == ["strict", "broad"]
    expected_country_shards = (
        len(first["target_country_codes"]) + config.country_shard_size - 1
    ) // config.country_shard_size
    assert first["query_count"] == 2 * 2 * expected_country_shards
    assert len({query["query_id"] for query in first["queries"]}) == first["query_count"]
    assert all(len(query["topic_ids"]) <= 1 for query in first["queries"])
    assert all(len(query["country_codes"]) <= 25 for query in first["queries"])
    validate_query_plan(first, config)


def test_preview_counts_pages_and_reuses_cache(tmp_path: Path) -> None:
    config = planner_config()
    plan = build_query_plan(
        registry(), load_region_registry(), config, start_year=2020, end_year=2021
    )
    client = CountClient()
    cache = RawResponseCache(tmp_path / "cache")

    preview_query_plan(plan, client, cache, config)

    expected_2020_queries = plan["query_count"] // 2
    assert plan["preview_status"] == "complete"
    assert plan["predicted_result_volume_including_duplicates"] == expected_2020_queries * 201
    assert plan["predicted_request_count"] == expected_2020_queries * 2
    assert plan["estimated_bulk_cost_usd"] == expected_2020_queries * 2 * 0.001
    assert client.calls == plan["query_count"]

    second_plan = build_query_plan(
        registry(), load_region_registry(), config, start_year=2020, end_year=2021
    )
    preview_query_plan(second_plan, client, cache, config)
    assert client.calls == plan["query_count"]
    assert second_plan["logical_plan_hash"] == plan["logical_plan_hash"]


def test_plan_validation_detects_missing_country() -> None:
    config = planner_config()
    plan = build_query_plan(
        registry(), load_region_registry(), config, start_year=2020, end_year=2020
    )
    missing = plan["target_country_codes"][0]
    for query in plan["queries"]:
        query["country_codes"] = [code for code in query["country_codes"] if code != missing]
    with pytest.raises(ValueError, match="target country"):
        validate_query_plan(plan, config)


def test_plan_download_dry_run_has_no_write(tmp_path: Path, capsys: object) -> None:
    output = tmp_path / "plan.json"
    assert main(["plan-download", "--dry-run", "--output", str(output)]) == 0
    assert not output.exists()
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "no request or write performed" in captured.out
