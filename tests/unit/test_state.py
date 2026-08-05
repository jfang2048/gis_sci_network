import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest

from gisnet.state import (
    BacklogStore,
    InvalidStateError,
    LockHeldError,
    ProjectState,
    ProjectStateStore,
    RunLock,
    TaskStatus,
    output_is_stale,
)


def test_state_round_trip_and_invalid_backup(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    store = ProjectStateStore(path)
    state = ProjectState(active_run_id="run-1")
    store.save(state)
    assert store.load().active_run_id == "run-1"

    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(InvalidStateError, match="backup"):
        store.load()
    backups = list(tmp_path.glob("state.json.invalid-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{broken"


def test_legacy_state_is_migrated_without_losing_task_ids(tmp_path: Path) -> None:
    path = tmp_path / "legacy-state.json"
    path.write_text(
        json.dumps({"schema_version": 0, "current_task": "GISNET-001", "completed": ["A"]}),
        encoding="utf-8",
    )
    state = ProjectStateStore(path).load()
    assert state.schema_version == 1
    assert state.current_task_id == "GISNET-001"
    assert state.completed_task_ids == ["A"]


def test_concurrent_run_lock_fails_safely(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    first = RunLock(path, run_id="one", task_id="GISNET-002").acquire()
    try:
        with pytest.raises(LockHeldError, match="run one"):
            RunLock(path, run_id="two", task_id="GISNET-002").acquire()
    finally:
        first.release()
    assert not path.exists()


def test_absent_process_lock_is_recovered(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"
    path.write_text(
        json.dumps(
            {
                "run_id": "dead",
                "pid": 999_999_999,
                "hostname": socket.gethostname(),
                "started_at_utc": datetime.now(UTC).isoformat(),
                "task_id": "GISNET-002",
            }
        ),
        encoding="utf-8",
    )
    lock = RunLock(path, run_id="replacement", task_id="GISNET-002").acquire()
    try:
        assert json.loads(path.read_text(encoding="utf-8"))["run_id"] == "replacement"
        assert list(tmp_path.glob("run.lock.stale-*"))
    finally:
        lock.release()


def test_backlog_transition_records_reason_and_timestamp(tmp_path: Path) -> None:
    path = tmp_path / "backlog.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source": "test",
                "tasks": [
                    {
                        "id": "GISNET-001",
                        "priority": "P0",
                        "dependencies": [],
                        "status": "TODO",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    task = BacklogStore(path).transition(
        "GISNET-001", TaskStatus.IN_PROGRESS, reason="test run", run_id="run-1"
    )
    assert task.status == TaskStatus.IN_PROGRESS
    assert task.history[-1]["reason"] == "test run"
    assert task.history[-1]["changed_at_utc"]


def test_output_staleness_uses_schema_config_and_source_versions() -> None:
    manifest = {
        "schema_version": 1,
        "config_hashes": {"project": "a"},
        "source_versions": {"openalex": "b"},
    }
    assert not output_is_stale(
        manifest,
        schema_version=1,
        config_hashes={"project": "a"},
        source_versions={"openalex": "b"},
    )
    assert output_is_stale(
        manifest,
        schema_version=1,
        config_hashes={"project": "changed"},
        source_versions={"openalex": "b"},
    )


def test_scheduler_prefers_research_definition_before_raw_acquisition(tmp_path: Path) -> None:
    path = tmp_path / "backlog.json"
    path.write_text(
        json.dumps(
            {
                "source": "test",
                "tasks": [
                    {"id": "GISNET-023", "priority": "P1", "status": "TODO"},
                    {"id": "GISNET-034", "priority": "P1", "status": "TODO"},
                ],
            }
        ),
        encoding="utf-8",
    )
    assert BacklogStore(path).next_unblocked().id == "GISNET-034"  # type: ignore[union-attr]
