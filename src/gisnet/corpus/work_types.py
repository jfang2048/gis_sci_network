"""Selected-corpus work-type profiling and versioned inclusion policies."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, load_yaml
from gisnet.corpus.topics import cached_get, short_openalex_id, topic_id
from gisnet.openalex.cache import RawResponseCache
from gisnet.openalex.client import OpenAlexClient

CorpusView = Literal["strict", "broad"]


class WorkTypeRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary: bool
    preprint_sensitivity: bool
    expanded_sensitivity: bool
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("work type rules require a reason")
        return value

    @model_validator(mode="after")
    def validate_nested_views(self) -> WorkTypeRule:
        if self.primary and not (self.preprint_sensitivity and self.expanded_sensitivity):
            raise ValueError("primary types must also exist in both sensitivity views")
        return self


class WorkTypePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    policy_version: str
    review_status: Literal["provisional", "reviewed"]
    unknown_policy: WorkTypeRule
    types: dict[str, WorkTypeRule]

    def map_type(self, source_type: str | None) -> WorkTypeRule:
        if not source_type:
            return self.unknown_policy
        return self.types.get(source_type.casefold(), self.unknown_policy)


def load_work_type_policy(path: str | Path = "config/work_types.yml") -> WorkTypePolicy:
    return WorkTypePolicy.model_validate(load_yaml(path))


def _work_sample(raw: dict[str, Any], corpus_view: str, inspected_type: str) -> dict[str, Any]:
    raw_primary = raw.get("primary_topic")
    primary = raw_primary if isinstance(raw_primary, dict) else {}
    raw_location = raw.get("primary_location")
    location = raw_location if isinstance(raw_location, dict) else {}
    raw_source = location.get("source")
    source = raw_source if isinstance(raw_source, dict) else {}
    return {
        "corpus_view": corpus_view,
        "inspected_type": inspected_type,
        "work_id": short_openalex_id(raw.get("id"), "W"),
        "title": raw.get("title"),
        "publication_year": raw.get("publication_year"),
        "work_type": raw.get("type"),
        "is_retracted": raw.get("is_retracted"),
        "is_paratext": raw.get("is_paratext"),
        "primary_topic_id": topic_id(primary.get("id")),
        "source_name": source.get("display_name"),
    }


def profile_work_types(
    client: OpenAlexClient,
    cache: RawResponseCache,
    policy: WorkTypePolicy,
    topic_registry: dict[str, Any],
    *,
    start_year: int = 2010,
    end_year: int = 2025,
    force: bool = False,
) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    samples: list[dict[str, Any]] = []
    retrieval_times: list[str] = []
    topic_sets = {
        "strict": topic_registry.get("strict_topic_ids", []),
        "broad": topic_registry.get("broad_topic_ids", []),
    }
    for corpus_view, identifiers in topic_sets.items():
        if not identifiers:
            raise ValueError(f"{corpus_view} Topic set is empty")
        base_filter = (
            f"from_publication_date:{start_year}-01-01,to_publication_date:{end_year}-12-31,"
            f"topics.id:{'|'.join(identifiers)}"
        )
        group_parameters = {"filter": base_filter, "group_by": "type", "per-page": 200}
        data, retrieved_at = cached_get(client, cache, "/works", group_parameters, force=force)
        retrieval_times.append(retrieved_at)
        groups = data.get("group_by", [])
        if not isinstance(groups, list):
            raise ValueError(f"{corpus_view} work-type response lacks group_by")
        for group in groups:
            if not isinstance(group, dict) or not isinstance(group.get("key_display_name"), str):
                continue
            source_type = str(group["key_display_name"])
            rule = policy.map_type(source_type)
            records.append(
                {
                    "corpus_view": corpus_view,
                    "source_type_id": group.get("key"),
                    "source_type": source_type,
                    "work_count": group.get("count"),
                    "primary": rule.primary,
                    "preprint_sensitivity": rule.preprint_sensitivity,
                    "expanded_sensitivity": rule.expanded_sensitivity,
                    "mapping_reason": rule.reason,
                    "is_explicitly_configured": source_type in policy.types,
                }
            )
        for inspected_type in ("conference-paper", "conference-abstract", "preprint"):
            parameters = {
                "filter": f"{base_filter},type:{inspected_type}",
                "select": (
                    "id,title,publication_year,type,is_retracted,is_paratext,"
                    "primary_topic,primary_location"
                ),
                "sort": "cited_by_count:desc",
                "per-page": 3,
            }
            sample_data, sample_retrieved_at = cached_get(
                client, cache, "/works", parameters, force=force
            )
            retrieval_times.append(sample_retrieved_at)
            for raw in sample_data.get("results", []):
                if isinstance(raw, dict):
                    samples.append(_work_sample(raw, corpus_view, inspected_type))
    records.sort(key=lambda row: (row["corpus_view"], row["source_type"]))
    samples.sort(key=lambda row: (row["corpus_view"], row["inspected_type"], row["work_id"] or ""))
    observed = {str(record["source_type"]) for record in records}
    return {
        "schema_version": 1,
        "profile_version": "openalex-work-types-2026-08-05-v1",
        "retrieved_at_utc": max(retrieval_times),
        "policy_version": policy.policy_version,
        "start_year": start_year,
        "end_year": end_year,
        "unmapped_observed_types": sorted(observed - set(policy.types)),
        "records": records,
        "inspection_samples": samples,
    }


def write_work_type_profile(
    payload: dict[str, Any],
    *,
    path: str | Path,
    policy_path: str | Path,
    registry_path: str | Path,
    run_id: str,
    command: str,
) -> None:
    write_json_artifact(
        path=path,
        dataset_name="work_type_profile",
        payload=payload,
        records=payload["records"],
        primary_key=["corpus_view", "source_type"],
        run_id=run_id,
        config_hashes={
            "work_types": config_file_hash(policy_path),
            "topic_registry": config_file_hash(registry_path),
        },
        source_versions={"openalex_works": "retrieved-2026-08-05"},
        source_manifests=[".agent/manifests/topic_registry.json"],
        command=command,
    )
