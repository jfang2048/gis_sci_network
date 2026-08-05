"""Versioned OpenAlex institution-type profiling and analytical mapping."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, load_yaml
from gisnet.corpus.topics import cached_get
from gisnet.openalex.cache import RawResponseCache
from gisnet.openalex.client import OpenAlexClient

AnalyticalScope = Literal["primary", "secondary", "excluded", "unknown"]


class InstitutionTypeRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analytical_scope: AnalyticalScope
    normalized_category: str
    is_primary_research_scope: bool
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_required(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("institution type rules require a reason")
        return value

    @model_validator(mode="after")
    def primary_flag_matches_scope(self) -> InstitutionTypeRule:
        if self.is_primary_research_scope != (self.analytical_scope == "primary"):
            raise ValueError("primary flag must match analytical_scope=primary")
        return self


class InstitutionTypePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    policy_version: str
    review_status: Literal["provisional", "reviewed"]
    unknown_policy: InstitutionTypeRule
    types: dict[str, InstitutionTypeRule]

    @model_validator(mode="after")
    def validate_policy(self) -> InstitutionTypePolicy:
        if self.unknown_policy.analytical_scope != "unknown":
            raise ValueError("unknown_policy must use analytical_scope=unknown")
        if any(key != key.casefold() or not key for key in self.types):
            raise ValueError("source institution type keys must be lowercase and non-empty")
        return self

    def map_type(self, source_type: str | None) -> InstitutionTypeRule:
        if not source_type:
            return self.unknown_policy
        return self.types.get(source_type.casefold(), self.unknown_policy)


def load_institution_type_policy(
    path: str | Path = "config/institution_types.yml",
) -> InstitutionTypePolicy:
    return InstitutionTypePolicy.model_validate(load_yaml(path))


def profile_institution_types(
    client: OpenAlexClient,
    cache: RawResponseCache,
    policy: InstitutionTypePolicy,
    *,
    force: bool = False,
) -> dict[str, Any]:
    parameters = {"group_by": "type", "per-page": 200}
    data, retrieved_at = cached_get(client, cache, "/institutions", parameters, force=force)
    groups = data.get("group_by", [])
    if not isinstance(groups, list):
        raise ValueError("OpenAlex institution type profile lacks group_by results")
    records: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("key"), str):
            continue
        source_type = str(group["key"])
        rule = policy.map_type(source_type)
        records.append(
            {
                "source_type": source_type,
                "source_display_name": group.get("key_display_name"),
                "institution_count": group.get("count"),
                "analytical_scope": rule.analytical_scope,
                "normalized_category": rule.normalized_category,
                "is_primary_research_scope": rule.is_primary_research_scope,
                "mapping_reason": rule.reason,
                "is_explicitly_configured": source_type.casefold() in policy.types,
            }
        )
    records.sort(key=lambda row: row["source_type"])
    observed = {record["source_type"] for record in records}
    return {
        "schema_version": 1,
        "profile_version": "openalex-institution-types-2026-08-05",
        "retrieved_at_utc": retrieved_at,
        "policy_version": policy.policy_version,
        "observed_type_count": len(records),
        "unmapped_observed_types": sorted(observed - set(policy.types)),
        "records": records,
    }


def write_institution_type_profile(
    payload: dict[str, Any],
    *,
    path: str | Path,
    policy_path: str | Path,
    run_id: str,
    command: str,
) -> None:
    write_json_artifact(
        path=path,
        dataset_name="institution_type_profile",
        payload=payload,
        records=payload["records"],
        primary_key=["source_type"],
        run_id=run_id,
        config_hashes={"institution_types": config_file_hash(policy_path)},
        source_versions={"openalex_institutions": "retrieved-2026-08-05"},
        command=command,
    )
