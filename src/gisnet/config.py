"""Typed configuration loading, validation, and canonical semantic hashing."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalysisConfig(StrictModel):
    start_year: int = 2010
    end_year: int = 2025
    include_partial_current_year: bool = False

    @model_validator(mode="after")
    def validate_years(self) -> AnalysisConfig:
        if self.start_year < 1900:
            raise ValueError("start_year must be 1900 or later")
        if self.end_year < self.start_year:
            raise ValueError("end_year must not precede start_year")
        if not self.include_partial_current_year and self.end_year > 2025:
            raise ValueError("complete-year mode ends at 2025; enable partial mode explicitly")
        return self


class ConsortiumConfig(StrictModel):
    warning_institution_count: int = Field(default=25, ge=2)
    exclusion_institution_count: int = Field(default=100, ge=2)

    @model_validator(mode="after")
    def validate_thresholds(self) -> ConsortiumConfig:
        if self.exclusion_institution_count < self.warning_institution_count:
            raise ValueError("exclusion threshold must be at least the warning threshold")
        return self


class NetworkConfig(StrictModel):
    persistence_windows: list[int] = Field(default_factory=lambda: [3, 5])
    minimum_fractional_weight: float = Field(default=0.0, ge=0.0)
    approximate_betweenness_threshold: int = Field(default=10_000, ge=1)

    @field_validator("persistence_windows")
    @classmethod
    def validate_windows(cls, value: list[int]) -> list[int]:
        if not value or any(window < 2 for window in value):
            raise ValueError("persistence windows must contain integers of at least 2")
        if len(value) != len(set(value)):
            raise ValueError("persistence windows must be unique")
        return value


class OpenAlexConfig(StrictModel):
    base_url: str = "https://api.openalex.org"
    timeout_seconds: float = Field(default=30.0, gt=0)
    max_retries: int = Field(default=5, ge=0, le=20)
    backoff_base_seconds: float = Field(default=0.5, ge=0)
    backoff_max_seconds: float = Field(default=30.0, ge=0)
    jitter_seconds: float = Field(default=0.25, ge=0)
    per_page: int = Field(default=200, ge=1, le=200)
    cache_directory: Path = Path("data/cache/openalex")
    checkpoint_directory: Path = Path(".agent/checkpoints/openalex")

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        if not value.startswith("https://"):
            raise ValueError("OpenAlex base_url must use HTTPS")
        return value.rstrip("/")


class GeographyConfig(StrictModel):
    mapping_path: Path = Path("config/regions.yml")
    override_path: Path = Path("config/region_overrides.yml")


CorpusView = Literal["strict", "broad"]
HierarchyView = Literal["organization", "umbrella"]


def _default_corpus_views() -> list[CorpusView]:
    return ["strict", "broad"]


def _default_hierarchy_views() -> list[HierarchyView]:
    return ["organization", "umbrella"]


class ProjectConfig(StrictModel):
    project_version: str = "0.1.0"
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    corpus_views: list[CorpusView] = Field(default_factory=_default_corpus_views)
    hierarchy_views: list[HierarchyView] = Field(default_factory=_default_hierarchy_views)
    random_seed: int = 20250805
    consortium: ConsortiumConfig = Field(default_factory=ConsortiumConfig)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    openalex: OpenAlexConfig = Field(default_factory=OpenAlexConfig)
    geography: GeographyConfig = Field(default_factory=GeographyConfig)

    @field_validator("corpus_views", "hierarchy_views")
    @classmethod
    def validate_unique_views(cls, value: list[str]) -> list[str]:
        if not value or len(value) != len(set(value)):
            raise ValueError("view lists must be non-empty and unique")
        return value


def load_yaml(path: str | Path) -> Any:
    with Path(path).open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def load_project_config(path: str | Path = "config/project.yml") -> ProjectConfig:
    return ProjectConfig.model_validate(load_yaml(path))


def canonicalize(value: Any) -> Any:
    """Normalize model/path containers into deterministic JSON-compatible values."""
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): canonicalize(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [canonicalize(item) for item in value]
    return value


def semantic_hash(value: Any) -> str:
    canonical_json = json.dumps(
        canonicalize(value), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


def config_file_hash(path: str | Path) -> str:
    """Hash parsed YAML so comments and formatting do not change provenance."""
    return semantic_hash(load_yaml(path))
