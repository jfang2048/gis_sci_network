"""Compressed raw OpenAlex response cache with validation and quarantine."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gisnet.atomic import atomic_write_bytes, atomic_write_json
from gisnet.config import canonicalize
from gisnet.secrets import non_secret_parameters


class CacheCorruptionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CacheEntry:
    key: str
    data: dict[str, Any]
    metadata: dict[str, Any]


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class RawResponseCache:
    def __init__(self, root: str | Path = "data/cache/openalex") -> None:
        self.root = Path(root)
        self.pages = self.root / "pages"
        self.quarantine = self.root / "quarantine"

    @staticmethod
    def make_key(endpoint: str, parameters: Mapping[str, Any] | None = None) -> str:
        identity = {
            "endpoint": "/" + endpoint.lstrip("/"),
            "parameters": canonicalize(non_secret_parameters(parameters)),
        }
        payload = json.dumps(identity, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def put(
        self,
        *,
        endpoint: str,
        parameters: Mapping[str, Any],
        data: dict[str, Any],
        status_code: int,
        retrieved_at_utc: str,
        rate_limit: Mapping[str, str] | None = None,
    ) -> CacheEntry:
        if not isinstance(data, dict):
            raise TypeError("raw response cache accepts only JSON objects")
        key = self.make_key(endpoint, parameters)
        raw = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        checksum = hashlib.sha256(raw).hexdigest()
        meta = data.get("meta", {})
        next_cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
        metadata: dict[str, Any] = {
            "schema_version": 1,
            "cache_key": key,
            "endpoint": "/" + endpoint.lstrip("/"),
            "parameters": canonicalize(non_secret_parameters(parameters)),
            "status_code": status_code,
            "retrieved_at_utc": retrieved_at_utc,
            "query_hash": key,
            "checksum_sha256": checksum,
            "uncompressed_bytes": len(raw),
            "next_cursor": next_cursor,
            "rate_limit": dict(rate_limit or {}),
        }
        data_path, metadata_path = self._paths(key)

        def validate_gzip(temporary: Path) -> None:
            decoded = gzip.decompress(temporary.read_bytes())
            if hashlib.sha256(decoded).hexdigest() != checksum:
                raise CacheCorruptionError("temporary cache checksum validation failed")
            if not isinstance(json.loads(decoded), dict):
                raise CacheCorruptionError("temporary cache JSON is not an object")

        atomic_write_bytes(data_path, gzip.compress(raw, mtime=0), validate=validate_gzip)
        atomic_write_json(metadata_path, metadata)
        entry = self.get(key, quarantine_on_error=False)
        if entry is None:
            raise CacheCorruptionError(f"new cache entry failed validation: {key}")
        return entry

    def get(self, key: str, *, quarantine_on_error: bool = True) -> CacheEntry | None:
        data_path, metadata_path = self._paths(key)
        if not data_path.exists() and not metadata_path.exists():
            return None
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata.get("cache_key") != key:
                raise CacheCorruptionError("cache key metadata mismatch")
            raw = gzip.decompress(data_path.read_bytes())
            if hashlib.sha256(raw).hexdigest() != metadata.get("checksum_sha256"):
                raise CacheCorruptionError("raw response checksum mismatch")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise CacheCorruptionError("cached JSON is not an object")
            return CacheEntry(key=key, data=data, metadata=metadata)
        except (OSError, gzip.BadGzipFile, json.JSONDecodeError, CacheCorruptionError) as exc:
            if quarantine_on_error:
                self._quarantine(key, reason=type(exc).__name__)
                return None
            if isinstance(exc, CacheCorruptionError):
                raise
            raise CacheCorruptionError(f"invalid cache entry {key}: {type(exc).__name__}") from exc

    def validate(self, key: str, expected_checksum: str | None = None) -> CacheEntry:
        entry = self.get(key, quarantine_on_error=False)
        if entry is None:
            raise CacheCorruptionError(f"cache entry does not exist: {key}")
        if expected_checksum and entry.metadata.get("checksum_sha256") != expected_checksum:
            raise CacheCorruptionError(f"checkpoint checksum mismatch for cache entry: {key}")
        return entry

    def _paths(self, key: str) -> tuple[Path, Path]:
        directory = self.pages / key[:2]
        return directory / f"{key}.json.gz", directory / f"{key}.meta.json"

    def _quarantine(self, key: str, *, reason: str) -> None:
        self.quarantine.mkdir(parents=True, exist_ok=True)
        suffix = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        data_path, metadata_path = self._paths(key)
        for source in (data_path, metadata_path):
            if source.exists():
                target = self.quarantine / f"{source.name}.{suffix}.{reason}"
                os.replace(source, target)
