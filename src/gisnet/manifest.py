"""Validated provenance manifests for generated datasets."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gisnet.atomic import atomic_write_json


class DatasetManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset_name: str
    schema_version: int = 1
    created_at_utc: str
    run_id: str
    git_commit: str
    config_hashes: dict[str, str]
    source_manifests: list[str] = Field(default_factory=list)
    source_versions: dict[str, str] = Field(default_factory=dict)
    row_count: int
    column_count: int
    primary_key: list[str]
    min_year: int | None = None
    max_year: int | None = None
    null_counts: dict[str, int]
    checksum_sha256: str
    command: str
    status: str = "valid"

    @model_validator(mode="after")
    def require_provenance(self) -> DatasetManifest:
        if not self.config_hashes:
            raise ValueError("Every dataset manifest requires at least one configuration hash")
        if self.status != "valid":
            raise ValueError("Only validated datasets may receive a final manifest")
        return self

    def write(self, path: str) -> None:
        atomic_write_json(path, self.model_dump(mode="json"))
