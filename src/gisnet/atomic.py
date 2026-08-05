"""Atomic filesystem writes used by state, cache, checkpoints, and manifests."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any


def atomic_write_bytes(
    path: str | Path,
    payload: bytes,
    *,
    validate: Callable[[Path], None] | None = None,
) -> None:
    """Write *payload* beside *path* and replace the destination after validation."""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if validate is not None:
            validate(temporary)
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def atomic_write_text(
    path: str | Path,
    payload: str,
    *,
    validate: Callable[[Path], None] | None = None,
) -> None:
    atomic_write_bytes(path, payload.encode("utf-8"), validate=validate)


def atomic_write_json(path: str | Path, payload: Any) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

    def validate_json(temporary: Path) -> None:
        with temporary.open(encoding="utf-8") as handle:
            json.load(handle)

    atomic_write_text(path, serialized, validate=validate_json)


def _fsync_directory(path: Path) -> None:
    """Best-effort directory fsync so a rename survives a sudden interruption."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)
