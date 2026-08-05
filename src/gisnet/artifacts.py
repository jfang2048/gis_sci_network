"""Validated atomic JSON artifacts and their provenance manifests."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from gisnet.atomic import atomic_write_json, atomic_write_text
from gisnet.manifest import DatasetManifest


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def current_git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short=12", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "nogit"


def write_json_artifact(
    *,
    path: str | Path,
    dataset_name: str,
    payload: dict[str, Any],
    records: list[dict[str, Any]],
    primary_key: list[str],
    run_id: str,
    config_hashes: dict[str, str],
    source_versions: dict[str, str],
    source_manifests: list[str] | None = None,
    command: str,
    manifest_directory: str | Path = ".agent/manifests",
) -> DatasetManifest:
    """Write JSON, validate its logical shape, then atomically write a manifest."""
    destination = Path(path)
    atomic_write_json(destination, payload)
    decoded = json.loads(destination.read_text(encoding="utf-8"))
    if decoded != payload:
        raise ValueError(f"JSON artifact failed round-trip validation: {destination}")
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    columns = sorted({key for record in records for key in record})
    null_counts = {
        column: sum(record.get(column) is None for record in records) for column in columns
    }
    years = [
        int(record["publication_year"])
        for record in records
        if isinstance(record.get("publication_year"), int)
    ]
    manifest = DatasetManifest(
        dataset_name=dataset_name,
        created_at_utc=utc_timestamp(),
        run_id=run_id,
        git_commit=current_git_commit(),
        config_hashes=config_hashes,
        source_manifests=source_manifests or [],
        source_versions=source_versions,
        row_count=len(records),
        column_count=len(columns),
        primary_key=primary_key,
        min_year=min(years) if years else None,
        max_year=max(years) if years else None,
        null_counts=null_counts,
        checksum_sha256=checksum,
        command=command,
    )
    manifest_path = Path(manifest_directory) / f"{dataset_name}.json"
    manifest.write(str(manifest_path))
    return manifest


def load_json_object(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def write_yaml_artifact(
    *,
    path: str | Path,
    dataset_name: str,
    payload: dict[str, Any],
    records: list[dict[str, Any]],
    primary_key: list[str],
    run_id: str,
    config_hashes: dict[str, str],
    source_versions: dict[str, str],
    source_manifests: list[str] | None = None,
    command: str,
    manifest_directory: str | Path = ".agent/manifests",
) -> DatasetManifest:
    serialized = yaml.safe_dump(payload, sort_keys=False, allow_unicode=True, width=110)
    destination = Path(path)
    atomic_write_text(destination, serialized)
    decoded = yaml.safe_load(destination.read_text(encoding="utf-8"))
    if decoded != payload:
        raise ValueError(f"YAML artifact failed round-trip validation: {destination}")
    checksum = hashlib.sha256(destination.read_bytes()).hexdigest()
    columns = sorted({key for record in records for key in record})
    null_counts = {
        column: sum(record.get(column) is None for record in records) for column in columns
    }
    manifest = DatasetManifest(
        dataset_name=dataset_name,
        created_at_utc=utc_timestamp(),
        run_id=run_id,
        git_commit=current_git_commit(),
        config_hashes=config_hashes,
        source_manifests=source_manifests or [],
        source_versions=source_versions,
        row_count=len(records),
        column_count=len(columns),
        primary_key=primary_key,
        null_counts=null_counts,
        checksum_sha256=checksum,
        command=command,
    )
    manifest_path = Path(manifest_directory) / f"{dataset_name}.json"
    manifest.write(str(manifest_path))
    return manifest
