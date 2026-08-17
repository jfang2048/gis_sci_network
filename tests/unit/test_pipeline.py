from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import pytest

from gisnet.atomic import atomic_write_json
from gisnet.config import config_file_hash
from gisnet.dataset import file_sha256
from gisnet.pipeline import (
    CONFIG_INPUTS,
    DEFAULT_STAGES,
    PipelineStage,
    StageOutput,
    run_pipeline,
    validate_stage,
)


def _write_manifest(
    manifest: Path,
    data: Path,
    *,
    created: str = "2026-08-05T12:00:00Z",
    config_hashes: dict[str, str] | None = None,
    source_manifests: list[str] | None = None,
    source_versions: dict[str, str] | None = None,
) -> None:
    atomic_write_json(
        manifest,
        {
            "status": "valid",
            "created_at_utc": created,
            "checksum_sha256": file_sha256(data),
            "config_hashes": config_hashes or {},
            "source_manifests": source_manifests or [],
            "source_versions": source_versions or {},
        },
    )


def test_validate_stage_detects_output_config_and_source_changes(tmp_path: Path) -> None:
    config = tmp_path / "policy.yml"
    config.write_text("value: one\n", encoding="utf-8")
    data = tmp_path / "data.json"
    data.write_text("{}\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    source = tmp_path / "source.json"
    atomic_write_json(source, {"status": "valid", "created_at_utc": "2026-08-05T11:00:00Z"})
    _write_manifest(
        manifest,
        data,
        config_hashes={"policy": config_file_hash(config)},
        source_manifests=[str(source)],
    )
    stage = PipelineStage("example", (StageOutput(manifest, data),))

    assert validate_stage(stage, config_inputs={"policy": config}).valid
    config.write_text("value: two\n", encoding="utf-8")
    assert validate_stage(stage, config_inputs={"policy": config}).reason.startswith(
        "config changed"
    )
    config.write_text("value: one\n", encoding="utf-8")
    data.write_text('{"changed": true}\n', encoding="utf-8")
    assert validate_stage(stage, config_inputs={"policy": config}).reason.startswith(
        "checksum changed"
    )
    data.write_text("{}\n", encoding="utf-8")
    atomic_write_json(source, {"status": "valid", "created_at_utc": "2026-08-05T13:00:00Z"})
    assert validate_stage(stage, config_inputs={"policy": config}).reason.startswith(
        "source manifest is newer"
    )


def test_validate_stage_invalidates_changed_policy_version(tmp_path: Path) -> None:
    data = tmp_path / "data.json"
    data.write_text("{}\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        data,
        source_versions={"example_policy": "v1"},
    )
    stage = PipelineStage(
        "example",
        (StageOutput(manifest, data),),
        policy_versions=(("example_policy", "v2"),),
    )

    validation = validate_stage(stage)
    assert validation.valid is False
    assert validation.reason.startswith("stage policy changed")

    _write_manifest(
        manifest,
        data,
        source_versions={"example_policy": "v2"},
    )
    assert validate_stage(stage).valid


def test_pipeline_skips_valid_stage_and_resumes_failed_raw_stage(tmp_path: Path) -> None:
    config = tmp_path / "project.yml"
    config.write_text("analysis: {}\n", encoding="utf-8")
    data = tmp_path / "data.json"
    data.write_text("{}\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    _write_manifest(manifest, data, config_hashes={"project": config_file_hash(config)})
    valid = PipelineStage("valid-stage", (StageOutput(manifest, data),))
    raw_data = tmp_path / "raw-status.json"
    raw_manifest = tmp_path / "raw-manifest.json"
    raw = PipelineStage(
        "download-stage",
        (StageOutput(raw_manifest, raw_data),),
        preserves_raw_data=True,
    )
    calls: list[list[str]] = []

    def runner(arguments: Sequence[str]) -> int:
        calls.append(list(arguments))
        return 0 if arguments[0] == "valid-stage" else 9

    summary = run_pipeline(
        stages=(valid, raw),
        runner=runner,
        run_id="run-1",
        config_path=config,
        start_year=2010,
        end_year=2025,
        corpus="all",
        hierarchy="all",
        resume=False,
        force=True,
        dry_run=False,
    )

    assert not summary["success"]
    assert summary["failed_stage"] == "download-stage"
    assert summary["raw_data_deleted"] is False
    assert calls[0][-1] == "--force"
    assert "--resume" in calls[1]
    assert "--force" not in calls[1]
    assert "--resume" in summary["recovery_command"]


def test_pipeline_dry_run_never_calls_stage_runner(tmp_path: Path) -> None:
    config = tmp_path / "project.yml"
    config.write_text("analysis: {}\n", encoding="utf-8")
    stage = PipelineStage(
        "missing-stage",
        (StageOutput(tmp_path / "missing-manifest.json", tmp_path / "missing-data.json"),),
    )

    def runner(_: Sequence[str]) -> int:
        raise AssertionError("dry-run must not execute a stage")

    summary = run_pipeline(
        stages=(stage,),
        runner=runner,
        run_id="run-2",
        config_path=config,
        start_year=2010,
        end_year=2025,
        corpus="all",
        hierarchy="all",
        resume=True,
        force=False,
        dry_run=True,
    )

    assert summary["success"]
    assert summary["status_counts"] == {"would_run": 1}


def test_config_change_rebuilds_only_affected_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project.yml"
    project.write_text("analysis: {}\n", encoding="utf-8")
    policy = tmp_path / "policy.yml"
    policy.write_text("version: one\n", encoding="utf-8")
    monkeypatch.setitem(CONFIG_INPUTS, "test_policy", policy)

    upstream_data = tmp_path / "upstream.json"
    upstream_data.write_text("{}\n", encoding="utf-8")
    upstream_manifest = tmp_path / "upstream-manifest.json"
    _write_manifest(
        upstream_manifest,
        upstream_data,
        created="2026-08-05T10:00:00Z",
        config_hashes={"test_policy": config_file_hash(policy)},
    )
    downstream_data = tmp_path / "downstream.json"
    downstream_data.write_text("{}\n", encoding="utf-8")
    downstream_manifest = tmp_path / "downstream-manifest.json"
    _write_manifest(
        downstream_manifest,
        downstream_data,
        created="2026-08-05T11:00:00Z",
        source_manifests=[str(upstream_manifest)],
    )
    independent_data = tmp_path / "independent.json"
    independent_data.write_text("{}\n", encoding="utf-8")
    independent_manifest = tmp_path / "independent-manifest.json"
    _write_manifest(independent_manifest, independent_data)
    stages = (
        PipelineStage("upstream", (StageOutput(upstream_manifest, upstream_data),)),
        PipelineStage("downstream", (StageOutput(downstream_manifest, downstream_data),)),
        PipelineStage("independent", (StageOutput(independent_manifest, independent_data),)),
    )
    policy.write_text("version: two\n", encoding="utf-8")

    def runner(arguments: Sequence[str]) -> int:
        if arguments[0] == "upstream":
            _write_manifest(
                upstream_manifest,
                upstream_data,
                created="2026-08-05T13:00:00Z",
                config_hashes={"test_policy": config_file_hash(policy)},
            )
        elif arguments[0] == "downstream":
            _write_manifest(
                downstream_manifest,
                downstream_data,
                created="2026-08-05T14:00:00Z",
                source_manifests=[str(upstream_manifest)],
            )
        return 0

    summary = run_pipeline(
        stages=stages,
        runner=runner,
        run_id="run-3",
        config_path=project,
        start_year=2010,
        end_year=2025,
        corpus="all",
        hierarchy="all",
        resume=True,
        force=False,
        dry_run=False,
    )

    assert summary["success"]
    assert [row["status"] for row in summary["stages"]] == [
        "executed",
        "rebuilt_stale",
        "skipped_valid",
    ]


def test_publication_date_qa_precedes_annual_network_stages() -> None:
    commands = [stage.command for stage in DEFAULT_STAGES]
    date_index = commands.index("build-publication-date-qa")
    assert commands[date_index - 1] == "build-work-institutions"
    assert commands[date_index + 1] == "build-edges"
    date_outputs = {output.data.name for output in DEFAULT_STAGES[date_index].outputs}
    assert date_outputs == {
        "work_publication_dates.parquet",
        "publication_date_coverage_corpus.parquet",
        "publication_date_coverage_year.parquet",
        "publication_date_coverage_institution.parquet",
        "publication_date_coverage_topic_family.parquet",
        "publication_date_qa_summary.json",
    }


def test_subannual_facts_follow_annual_edge_and_output_inputs() -> None:
    commands = [stage.command for stage in DEFAULT_STAGES]
    subannual_index = commands.index("build-subannual-facts")
    assert commands[subannual_index - 1] == "build-outputs"
    assert commands[subannual_index + 1] == "build-region-flows"
    outputs = {output.data.name for output in DEFAULT_STAGES[subannual_index].outputs}
    assert outputs == {
        "institution_outputs_month.parquet",
        "institution_outputs_quarter.parquet",
        "collaboration_edges_month.parquet",
        "collaboration_edges_quarter.parquet",
        "subannual_reconciliation.parquet",
        "subannual_sparsity.parquet",
        "subannual_temporal_summary.json",
    }
