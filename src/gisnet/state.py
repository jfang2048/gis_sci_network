"""Persistent project state, backlog transitions, and exclusive run locking."""

from __future__ import annotations

import json
import os
import shutil
import socket
from contextlib import AbstractContextManager, suppress
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from gisnet.atomic import atomic_write_json, atomic_write_text


def utc_now() -> datetime:
    return datetime.now(UTC)


def utc_timestamp() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def make_run_id(git_sha: str | None = None, *, now: datetime | None = None) -> str:
    timestamp = (now or utc_now()).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}_{(git_sha or 'nogit')[:12]}"


class TaskStatus(StrEnum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    BLOCKED = "BLOCKED"
    DONE = "DONE"
    SKIPPED = "SKIPPED"
    STALE = "STALE"


class ProjectState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    project_version: str = "0.1.0"
    active_run_id: str | None = None
    last_successful_run_id: str | None = None
    current_task_id: str | None = None
    completed_task_ids: list[str] = Field(default_factory=list)
    blocked_task_ids: list[str] = Field(default_factory=list)
    config_hashes: dict[str, str] = Field(default_factory=dict)
    source_versions: dict[str, str] = Field(default_factory=dict)
    dataset_manifests: dict[str, str] = Field(default_factory=dict)
    download_checkpoints: dict[str, str] = Field(default_factory=dict)
    last_updated_utc: str | None = None


class InvalidStateError(RuntimeError):
    """State is unreadable; a backup was retained for diagnosis."""


class ProjectStateStore:
    def __init__(self, path: str | Path = ".agent/state.json") -> None:
        self.path = Path(path)

    def load(self) -> ProjectState:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return ProjectState.model_validate(_migrate_state(raw))
        except (OSError, json.JSONDecodeError, ValidationError) as exc:
            backup = self._backup_invalid()
            where = f"; backup: {backup}" if backup else ""
            raise InvalidStateError(f"Invalid project state at {self.path}{where}: {exc}") from exc

    def save(self, state: ProjectState) -> None:
        state.last_updated_utc = utc_timestamp()
        validated = ProjectState.model_validate(state.model_dump(mode="json"))
        atomic_write_json(self.path, validated.model_dump(mode="json"))

    def _backup_invalid(self) -> Path | None:
        if not self.path.exists():
            return None
        suffix = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
        backup = self.path.with_name(f"{self.path.name}.invalid-{suffix}")
        shutil.copy2(self.path, backup)
        return backup


def _migrate_state(raw: Any) -> dict[str, Any]:
    """Migrate explicitly supported historical schemas without discarding provenance."""
    if not isinstance(raw, dict):
        raise ValueError("project state must be a JSON object")
    version = raw.get("schema_version", 0)
    if version == 1:
        return raw
    if version == 0:
        migrated = {
            "schema_version": 1,
            "project_version": raw.get("project_version", "0.1.0"),
            "active_run_id": raw.get("active_run_id"),
            "last_successful_run_id": raw.get("last_successful_run_id"),
            "current_task_id": raw.get("current_task_id", raw.get("current_task")),
            "completed_task_ids": raw.get("completed_task_ids", raw.get("completed", [])),
            "blocked_task_ids": raw.get("blocked_task_ids", raw.get("blocked", [])),
            "config_hashes": raw.get("config_hashes", {}),
            "source_versions": raw.get("source_versions", {}),
            "dataset_manifests": raw.get("dataset_manifests", {}),
            "download_checkpoints": raw.get("download_checkpoints", {}),
            "last_updated_utc": raw.get("last_updated_utc"),
        }
        return migrated
    raise ValueError(f"unsupported project state schema_version: {version}")


class BacklogTask(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    priority: str
    dependencies: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.TODO
    status_updated_at_utc: str | None = None
    status_reason: str | None = None
    history: list[dict[str, Any]] = Field(default_factory=list)


class Backlog(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: int = 1
    source: str
    tasks: list[BacklogTask]
    last_updated_utc: str | None = None


_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.TODO: {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.SKIPPED},
    TaskStatus.IN_PROGRESS: {TaskStatus.TODO, TaskStatus.BLOCKED, TaskStatus.DONE},
    TaskStatus.BLOCKED: {TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.SKIPPED},
    TaskStatus.DONE: {TaskStatus.STALE},
    TaskStatus.SKIPPED: {TaskStatus.TODO, TaskStatus.IN_PROGRESS},
    TaskStatus.STALE: {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED},
}


class BacklogStore:
    def __init__(self, path: str | Path = ".agent/backlog.json") -> None:
        self.path = Path(path)

    def load(self) -> Backlog:
        try:
            return Backlog.model_validate_json(self.path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise InvalidStateError(f"Invalid backlog at {self.path}: {exc}") from exc

    def save(self, backlog: Backlog) -> None:
        backlog.last_updated_utc = utc_timestamp()
        atomic_write_json(self.path, backlog.model_dump(mode="json"))

    def transition(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        reason: str,
        run_id: str,
    ) -> BacklogTask:
        backlog = self.load()
        try:
            task = next(item for item in backlog.tasks if item.id == task_id)
        except StopIteration as exc:
            raise KeyError(f"Unknown task: {task_id}") from exc
        previous = task.status
        if status != previous and status not in _TRANSITIONS[previous]:
            raise ValueError(f"Invalid task transition: {task_id} {previous} -> {status}")
        changed_at = utc_timestamp()
        task.status = status
        task.status_updated_at_utc = changed_at
        task.status_reason = reason
        task.history.append(
            {
                "from": previous.value,
                "to": status.value,
                "reason": reason,
                "run_id": run_id,
                "changed_at_utc": changed_at,
            }
        )
        self.save(backlog)
        return task

    def next_unblocked(self) -> BacklogTask | None:
        backlog = self.load()
        done = {task.id for task in backlog.tasks if task.status == TaskStatus.DONE}
        priorities = {"P0": 0, "P1": 1, "P2": 2, "OPTIONAL": 3}
        candidates = [
            task
            for task in backlog.tasks
            if task.status in {TaskStatus.TODO, TaskStatus.STALE}
            and all(dependency in done for dependency in task.dependencies)
            and (task.priority != "OPTIONAL" or "GISNET-104" in done)
        ]
        return min(candidates, key=lambda task: priorities.get(task.priority, 99), default=None)


class LockHeldError(RuntimeError):
    """Another live writer owns the project lock."""


class RunLock(AbstractContextManager["RunLock"]):
    def __init__(
        self,
        path: str | Path = ".agent/locks/run.lock",
        *,
        run_id: str,
        task_id: str,
        stale_after: timedelta = timedelta(hours=12),
    ) -> None:
        self.path = Path(path)
        self.run_id = run_id
        self.task_id = task_id
        self.stale_after = stale_after
        self.hostname = socket.gethostname()
        self.pid = os.getpid()
        self.acquired = False

    def acquire(self) -> Self:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "run_id": self.run_id,
            "pid": self.pid,
            "hostname": self.hostname,
            "started_at_utc": utc_timestamp(),
            "task_id": self.task_id,
        }
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if not self._existing_lock_is_stale():
                owner = self._read_existing()
                raise LockHeldError(
                    f"Writer lock is held by run {owner.get('run_id', 'unknown')} "
                    f"on {owner.get('hostname', 'unknown')} pid {owner.get('pid', 'unknown')}"
                ) from None
            self._quarantine_stale_lock()
            return self.acquire()
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        self.acquired = True
        return self

    def release(self) -> None:
        if not self.acquired:
            return
        owner = self._read_existing()
        if owner.get("run_id") == self.run_id and owner.get("pid") == self.pid:
            self.path.unlink(missing_ok=True)
        self.acquired = False

    def __enter__(self) -> Self:
        return self.acquire()

    def __exit__(self, *_: object) -> None:
        self.release()

    def _read_existing(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _existing_lock_is_stale(self) -> bool:
        owner = self._read_existing()
        started_raw = owner.get("started_at_utc")
        try:
            started = datetime.fromisoformat(str(started_raw).replace("Z", "+00:00"))
            too_old = utc_now() - started > self.stale_after
        except (TypeError, ValueError):
            too_old = True
        same_host = owner.get("hostname") == self.hostname
        try:
            owner_pid = int(owner["pid"])
        except (KeyError, TypeError, ValueError):
            process_absent = True
        else:
            process_absent = same_host and not _pid_exists(owner_pid)
        return too_old or process_absent

    def _quarantine_stale_lock(self) -> None:
        suffix = utc_now().strftime("%Y%m%dT%H%M%S%fZ")
        target = self.path.with_name(f"{self.path.name}.stale-{suffix}")
        with suppress(FileNotFoundError):
            os.replace(self.path, target)


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def output_is_stale(
    manifest: dict[str, Any],
    *,
    config_hashes: dict[str, str],
    source_versions: dict[str, str],
    schema_version: int | None = None,
) -> bool:
    """Compare provenance that materially invalidates a derived output."""
    if schema_version is not None and manifest.get("schema_version") != schema_version:
        return True
    if manifest.get("config_hashes", {}) != config_hashes:
        return True
    return bool(manifest.get("source_versions", {}) != source_versions)


def append_run_log(path: str | Path, record: str) -> None:
    """Append through an atomic whole-file replacement."""
    destination = Path(path)
    existing = destination.read_text(encoding="utf-8") if destination.exists() else ""
    prefix = f"{existing.rstrip()}\n\n" if existing.strip() else ""
    atomic_write_text(destination, f"{prefix}{record.rstrip()}\n")
