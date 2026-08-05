"""Cursor pagination that checkpoints only fully validated raw pages."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gisnet.atomic import atomic_write_json
from gisnet.openalex.cache import CacheCorruptionError, RawResponseCache
from gisnet.openalex.client import OpenAlexClient, OpenAlexError, RateLimitError
from gisnet.secrets import non_secret_parameters, redact_text


class PaginationError(RuntimeError):
    pass


class CursorLoopError(PaginationError):
    pass


class RepeatedPageError(PaginationError):
    pass


class CheckpointError(PaginationError):
    pass


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class CursorPaginator:
    def __init__(
        self,
        client: OpenAlexClient,
        cache: RawResponseCache,
        *,
        checkpoint_directory: str | Path = ".agent/checkpoints/openalex",
        failure_directory: str | Path = ".agent/failures",
    ) -> None:
        self.client = client
        self.cache = cache
        self.checkpoint_directory = Path(checkpoint_directory)
        self.failure_directory = Path(failure_directory)

    def download(
        self,
        *,
        query_id: str,
        endpoint: str,
        parameters: Mapping[str, Any] | None = None,
        initial_cursor: str = "*",
        per_page: int = 200,
        resume: bool = True,
        force: bool = False,
        page_callback: Callable[[dict[str, Any], dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        base_parameters = dict(non_secret_parameters(parameters))
        query_hash = self._query_hash(endpoint, base_parameters, per_page)
        checkpoint_path = self.checkpoint_directory / f"{query_id}.json"
        checkpoint = None if force or not resume else self._load_checkpoint(checkpoint_path)
        if checkpoint is not None:
            self._validate_checkpoint(checkpoint, query_id=query_id, query_hash=query_hash)
            if checkpoint.get("status") == "complete":
                return checkpoint
            self._validate_last_page(checkpoint)
            if checkpoint.get("status") == "blocked":
                checkpoint["status"] = "in_progress"
                checkpoint.pop("block_reason", None)
                checkpoint["updated_at_utc"] = _timestamp()
                self._save_checkpoint(checkpoint_path, checkpoint)
        else:
            checkpoint = {
                "schema_version": 1,
                "query_id": query_id,
                "query_hash": query_hash,
                "endpoint": "/" + endpoint.lstrip("/"),
                "parameters": base_parameters,
                "status": "in_progress",
                "initial_cursor": initial_cursor,
                "next_cursor": initial_cursor,
                "pages": [],
                "page_count": 0,
                "result_count": 0,
                "started_at_utc": _timestamp(),
                "updated_at_utc": _timestamp(),
            }
            self._save_checkpoint(checkpoint_path, checkpoint)

        seen_cursors = {page["cursor_used"] for page in checkpoint["pages"]}
        seen_result_hashes = {
            page["result_hash"] for page in checkpoint["pages"] if page.get("result_hash")
        }
        try:
            while checkpoint["next_cursor"]:
                cursor = str(checkpoint["next_cursor"])
                if cursor in seen_cursors:
                    raise CursorLoopError(f"cursor loop detected for query {query_id}")
                page_parameters = dict(base_parameters)
                page_parameters.update({"per-page": per_page, "cursor": cursor})
                cache_key = self.cache.make_key(endpoint, page_parameters)
                entry = self.cache.get(cache_key)
                if entry is None:
                    response = self.client.get(endpoint, params=page_parameters)
                    entry = self.cache.put(
                        endpoint=endpoint,
                        parameters=page_parameters,
                        data=response.data,
                        status_code=response.status_code,
                        retrieved_at_utc=response.retrieved_at_utc,
                        rate_limit=response.rate_limit,
                    )
                else:
                    self.cache.validate(cache_key)
                data = entry.data
                results = data.get("results")
                if not isinstance(results, list):
                    raise PaginationError(f"query {query_id} page lacks a results list")
                checksum = str(entry.metadata["checksum_sha256"])
                result_bytes = json.dumps(
                    results, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
                result_hash = hashlib.sha256(result_bytes).hexdigest()
                if result_hash in seen_result_hashes and results:
                    raise RepeatedPageError(
                        f"repeated non-empty page detected for query {query_id}"
                    )
                meta = data.get("meta", {})
                next_cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
                if next_cursor == cursor:
                    raise CursorLoopError(f"source repeated cursor for query {query_id}")
                page_record = {
                    "page_number": len(checkpoint["pages"]) + 1,
                    "cursor_used": cursor,
                    "next_cursor": next_cursor,
                    "cache_key": cache_key,
                    "checksum_sha256": checksum,
                    "result_hash": result_hash,
                    "result_count": len(results),
                    "retrieved_at_utc": entry.metadata["retrieved_at_utc"],
                }
                checkpoint["pages"].append(page_record)
                checkpoint["page_count"] = len(checkpoint["pages"])
                checkpoint["result_count"] += len(results)
                checkpoint["next_cursor"] = next_cursor
                checkpoint["updated_at_utc"] = _timestamp()
                if not next_cursor:
                    checkpoint["status"] = "complete"
                    checkpoint["completed_at_utc"] = _timestamp()
                self._save_checkpoint(checkpoint_path, checkpoint)
                if page_callback:
                    page_callback(data, page_record)
                seen_cursors.add(cursor)
                seen_result_hashes.add(result_hash)
            return checkpoint
        except (OpenAlexError, PaginationError, CacheCorruptionError) as exc:
            if isinstance(exc, RateLimitError):
                checkpoint["status"] = "blocked"
                checkpoint["block_reason"] = "rate_limit"
                checkpoint["updated_at_utc"] = _timestamp()
                self._save_checkpoint(checkpoint_path, checkpoint)
            failure = {
                "schema_version": 1,
                "query_id": query_id,
                "query_hash": query_hash,
                "failed_at_utc": _timestamp(),
                "failure_type": type(exc).__name__,
                "message": redact_text(exc),
                "next_cursor": checkpoint.get("next_cursor"),
                "page_count": checkpoint.get("page_count", 0),
                "recovery_command": "python -m gisnet.cli download-works --resume",
            }
            self.failure_directory.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.failure_directory / f"{query_id}.json", failure)
            raise

    @staticmethod
    def _query_hash(endpoint: str, parameters: Mapping[str, Any], per_page: int) -> str:
        payload = {
            "endpoint": "/" + endpoint.lstrip("/"),
            "parameters": non_secret_parameters(parameters),
            "per_page": per_page,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _load_checkpoint(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"invalid pagination checkpoint: {path}") from exc
        if not isinstance(value, dict):
            raise CheckpointError(f"pagination checkpoint is not an object: {path}")
        return value

    @staticmethod
    def _save_checkpoint(path: Path, checkpoint: dict[str, Any]) -> None:
        atomic_write_json(path, checkpoint)

    @staticmethod
    def _validate_checkpoint(checkpoint: dict[str, Any], *, query_id: str, query_hash: str) -> None:
        if checkpoint.get("query_id") != query_id or checkpoint.get("query_hash") != query_hash:
            raise CheckpointError(
                f"checkpoint identity mismatch for {query_id}; "
                "use force only after reviewing inputs"
            )
        if not isinstance(checkpoint.get("pages"), list):
            raise CheckpointError(f"checkpoint pages are invalid for {query_id}")

    def _validate_last_page(self, checkpoint: dict[str, Any]) -> None:
        pages = checkpoint.get("pages", [])
        if not pages:
            return
        last = pages[-1]
        try:
            self.cache.validate(last["cache_key"], last["checksum_sha256"])
        except (KeyError, CacheCorruptionError) as exc:
            raise CheckpointError(
                f"last raw page failed checkpoint validation for {checkpoint.get('query_id')}"
            ) from exc
