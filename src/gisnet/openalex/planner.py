"""Deterministic year/Topic/country query planning and count-only previews."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, load_yaml, semantic_hash
from gisnet.corpus.topics import cached_get
from gisnet.geography import RegionRegistry
from gisnet.openalex.cache import RawResponseCache
from gisnet.openalex.client import OpenAlexClient


class DownloadPlannerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    plan_version: str
    topic_shard_size: int = Field(ge=1)
    country_shard_size: int = Field(ge=1)
    source_max_or_values: int = Field(ge=1, le=100)
    per_page: int = Field(ge=1, le=200)
    estimated_page_cost_usd: float = Field(ge=0)
    target_macro_regions: list[str]
    select_fields: list[str]

    @model_validator(mode="after")
    def validate_source_bounds(self) -> DownloadPlannerConfig:
        if self.topic_shard_size > self.source_max_or_values:
            raise ValueError("topic_shard_size exceeds source_max_or_values")
        if self.country_shard_size > self.source_max_or_values:
            raise ValueError("country_shard_size exceeds source_max_or_values")
        if len(self.target_macro_regions) != len(set(self.target_macro_regions)):
            raise ValueError("target macro-regions must be unique")
        if (
            len(self.select_fields) != len(set(self.select_fields))
            or "id" not in self.select_fields
        ):
            raise ValueError("select_fields must be unique and include id")
        return self


def load_download_planner_config(
    path: str | Path = "config/download.yml",
) -> DownloadPlannerConfig:
    return DownloadPlannerConfig.model_validate(load_yaml(path))


def _chunks(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _query_id(year: int, topic_index: int, country_index: int, filter_value: str) -> str:
    suffix = hashlib.sha256(filter_value.encode()).hexdigest()[:10]
    return f"W{year}_T{topic_index:02d}_C{country_index:02d}_{suffix}"


def build_query_plan(
    topic_registry: dict[str, Any],
    region_registry: RegionRegistry,
    config: DownloadPlannerConfig,
    *,
    start_year: int,
    end_year: int,
    corpus: str = "all",
) -> dict[str, Any]:
    if end_year < start_year:
        raise ValueError("end_year must not precede start_year")
    if corpus not in {"strict", "broad", "all"}:
        raise ValueError("corpus must be strict, broad, or all")
    strict_ids = sorted(set(map(str, topic_registry.get("strict_topic_ids", []))))
    broad_ids = sorted(set(map(str, topic_registry.get("broad_topic_ids", []))))
    topic_ids = strict_ids if corpus == "strict" else broad_ids
    if not topic_ids:
        raise ValueError("selected Topic set is empty")
    target_countries = sorted(
        country.country_code
        for country in region_registry.countries
        if country.macro_region in config.target_macro_regions
    )
    if not target_countries:
        raise ValueError("target country coverage is empty")
    topic_shards = _chunks(topic_ids, config.topic_shard_size)
    country_shards = _chunks(target_countries, config.country_shard_size)
    queries: list[dict[str, Any]] = []
    for year in range(start_year, end_year + 1):
        for topic_index, topics in enumerate(topic_shards, start=1):
            for country_index, countries in enumerate(country_shards, start=1):
                filter_value = (
                    f"publication_year:{year},topics.id:{'|'.join(topics)},"
                    f"authorships.institutions.country_code:{'|'.join(countries)}"
                )
                parameters = {
                    "filter": filter_value,
                    "select": ",".join(config.select_fields),
                    "per-page": config.per_page,
                    "cursor": "*",
                }
                queries.append(
                    {
                        "query_id": _query_id(year, topic_index, country_index, filter_value),
                        "year": year,
                        "topic_shard_index": topic_index,
                        "country_shard_index": country_index,
                        "topic_ids": topics,
                        "country_codes": countries,
                        "parameters": parameters,
                        "query_hash": semantic_hash(parameters),
                        "preview_status": "not_run",
                        "predicted_result_count": None,
                        "predicted_page_count": None,
                        "preview_retrieved_at": None,
                        "preview_cost_usd": None,
                    }
                )
    base = {
        "schema_version": 1,
        "plan_version": config.plan_version,
        "start_year": start_year,
        "end_year": end_year,
        "requested_corpus": corpus,
        "covers_corpus_views": ["strict"] if corpus == "strict" else ["strict", "broad"],
        "topic_registry_hash": topic_registry.get("registry_hash"),
        "target_macro_regions": config.target_macro_regions,
        "target_country_codes": target_countries,
        "topic_ids": topic_ids,
        "topic_shard_size": config.topic_shard_size,
        "country_shard_size": config.country_shard_size,
        "source_max_or_values": config.source_max_or_values,
        "per_page": config.per_page,
        "query_count": len(queries),
        "duplicate_coverage_expected": True,
        "deduplication_key": "work_id",
        "duplicate_coverage_reason": (
            "Works may contain multiple included Topics and institutions in multiple "
            "country shards; normalization must union source_query_ids and deduplicate "
            "by OpenAlex Work ID."
        ),
        "preview_status": "not_run",
        "predicted_result_volume_including_duplicates": None,
        "predicted_request_count": None,
        "estimated_bulk_cost_usd": None,
        "queries": queries,
    }
    base["logical_plan_hash"] = semantic_hash(base)
    return base


def preview_query_plan(
    plan: dict[str, Any],
    client: OpenAlexClient,
    cache: RawResponseCache,
    config: DownloadPlannerConfig,
    *,
    force: bool = False,
) -> dict[str, Any]:
    retrieval_times: list[str] = []
    predicted_results = 0
    predicted_pages = 0
    preview_cost = 0.0
    for query in plan["queries"]:
        preview_parameters = {
            "filter": query["parameters"]["filter"],
            "select": "id",
            "per-page": 1,
        }
        data, retrieved_at = cached_get(client, cache, "/works", preview_parameters, force=force)
        retrieval_times.append(retrieved_at)
        raw_meta = data.get("meta")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        count = meta.get("count")
        if not isinstance(count, int) or count < 0:
            raise ValueError(f"query preview lacks a valid count: {query['query_id']}")
        pages = math.ceil(count / config.per_page)
        source_cost = meta.get("cost_usd")
        query["preview_status"] = "complete"
        query["predicted_result_count"] = count
        query["predicted_page_count"] = pages
        query["preview_retrieved_at"] = retrieved_at
        query["preview_cost_usd"] = source_cost if isinstance(source_cost, (int, float)) else None
        predicted_results += count
        predicted_pages += pages
        if isinstance(source_cost, (int, float)):
            preview_cost += float(source_cost)
    plan["preview_status"] = "complete"
    plan["preview_retrieved_at"] = max(retrieval_times) if retrieval_times else None
    plan["predicted_result_volume_including_duplicates"] = predicted_results
    plan["predicted_request_count"] = predicted_pages
    plan["observed_preview_cost_usd"] = round(preview_cost, 6)
    plan["estimated_bulk_cost_usd"] = round(predicted_pages * config.estimated_page_cost_usd, 6)
    plan["logical_plan_hash"] = semantic_hash(
        {key: value for key, value in plan.items() if key != "logical_plan_hash"}
    )
    return plan


def validate_query_plan(plan: dict[str, Any], config: DownloadPlannerConfig) -> None:
    query_ids = [query["query_id"] for query in plan.get("queries", [])]
    if len(query_ids) != len(set(query_ids)):
        raise ValueError("query IDs must be unique")
    for query in plan.get("queries", []):
        if len(query["topic_ids"]) > config.topic_shard_size:
            raise ValueError(f"Topic shard exceeds bound: {query['query_id']}")
        if len(query["country_codes"]) > config.country_shard_size:
            raise ValueError(f"country shard exceeds bound: {query['query_id']}")
        if max(len(query["topic_ids"]), len(query["country_codes"])) > config.source_max_or_values:
            raise ValueError(f"query exceeds source OR limit: {query['query_id']}")
    covered_countries = {
        code for query in plan.get("queries", []) for code in query["country_codes"]
    }
    if covered_countries != set(plan.get("target_country_codes", [])):
        raise ValueError("query plan does not cover every configured target country")
    covered_topics = {
        identifier for query in plan.get("queries", []) for identifier in query["topic_ids"]
    }
    if covered_topics != set(plan.get("topic_ids", [])):
        raise ValueError("query plan does not cover every selected Topic")


def write_query_plan(
    plan: dict[str, Any],
    *,
    path: str | Path,
    run_id: str,
    download_config_path: str | Path,
    topic_registry_path: str | Path,
    region_registry_path: str | Path,
    command: str,
) -> None:
    write_json_artifact(
        path=path,
        dataset_name="download_plan",
        payload=plan,
        records=plan["queries"],
        primary_key=["query_id"],
        run_id=run_id,
        config_hashes={
            "download": config_file_hash(download_config_path),
            "topic_registry": config_file_hash(topic_registry_path),
            "regions": config_file_hash(region_registry_path),
        },
        source_versions={"openalex_works_preview": "retrieved-2026-08-05"},
        source_manifests=[
            ".agent/manifests/topic_registry.json",
            ".agent/manifests/country_regions.json",
        ],
        command=command,
    )
