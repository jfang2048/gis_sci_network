"""Manifest-aware, resumable orchestration for the complete GIS network pipeline."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256


@dataclass(frozen=True)
class StageOutput:
    """One material output and the manifest that validates it."""

    manifest: Path
    data: Path


@dataclass(frozen=True)
class PipelineStage:
    """A CLI stage plus the artifacts needed to prove that it can be skipped."""

    command: str
    outputs: tuple[StageOutput, ...]
    arguments: tuple[str, ...] = ()
    accepts_common_options: bool = True
    preserves_raw_data: bool = False
    bundle_metadata: Path | None = None
    policy_versions: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class StageValidation:
    valid: bool
    reason: str


CONFIG_INPUTS: dict[str, Path] = {
    "project": Path("config/project.yml"),
    "candidate_registry": Path("data/reference/topic_candidates.json"),
    "discovery_terms": Path("config/discovery_terms.yml"),
    "download": Path("config/download.yml"),
    "download_plan": Path("data/reference/download_plan.json"),
    "institution_overrides": Path("config/institution_overrides.csv"),
    "institution_types": Path("config/institution_types.yml"),
    "known_positive_works": Path("config/known_positive_works.csv"),
    "region_overrides": Path("config/region_overrides.yml"),
    "regions": Path("config/regions.yml"),
    "sample_registry": Path("data/interim/topic_work_samples.json"),
    "school_decision": Path("config/school_decision.yml"),
    "topic_decisions": Path("config/topic_decisions.yml"),
    "topic_registry": Path("config/topic_registry.yml"),
    "work_types": Path("config/work_types.yml"),
}


def _output(dataset_name: str, data: str) -> StageOutput:
    return StageOutput(Path(f".agent/manifests/{dataset_name}.json"), Path(data))


DEFAULT_STAGES: tuple[PipelineStage, ...] = (
    PipelineStage(
        "validate-regions",
        (_output("country_regions", "data/reference/country_regions.csv"),),
        ("--write-csv", "data/reference/country_regions.csv"),
        accepts_common_options=False,
    ),
    PipelineStage(
        "profile-institution-types",
        (_output("institution_type_profile", "data/reference/institution_type_profile.json"),),
    ),
    PipelineStage(
        "discover-topics",
        (_output("topic_candidates", "data/reference/topic_candidates.json"),),
    ),
    PipelineStage(
        "sample-topic-works",
        (_output("topic_work_samples", "data/interim/topic_work_samples.json"),),
    ),
    PipelineStage(
        "freeze-topics",
        (_output("topic_registry", "config/topic_registry.yml"),),
    ),
    PipelineStage(
        "validate-corpus-boundary",
        (_output("corpus_boundary_validation", "data/reference/corpus_boundary_validation.json"),),
        policy_versions=(("corpus_boundary_policy", "corpus-boundary-2026-08-06-v2"),),
    ),
    PipelineStage(
        "profile-work-types",
        (_output("work_type_profile", "data/reference/work_type_profile.json"),),
    ),
    PipelineStage(
        "plan-download",
        (_output("download_plan", "data/reference/download_plan.json"),),
    ),
    PipelineStage(
        "download-works",
        (_output("raw_works_download_status", "data/reference/raw_works_download_status.json"),),
        ("--workers", "4"),
        preserves_raw_data=True,
    ),
    PipelineStage(
        "normalize-works",
        (
            _output("works", "data/processed/works.parquet"),
            _output("work_topics", "data/processed/work_topics.parquet"),
            _output("work_malformed", "data/processed/work_malformed.parquet"),
        ),
    ),
    PipelineStage(
        "extract-institutions",
        (
            _output(
                "work_institutions_extracted",
                "data/processed/work_institutions_extracted.parquet",
            ),
            _output(
                "work_institutions_unresolved",
                "data/processed/work_institutions_unresolved.parquet",
            ),
        ),
    ),
    PipelineStage(
        "build-institutions",
        (
            _output("institutions", "data/processed/institutions.parquet"),
            _output("institution_metadata_qa", "data/processed/institution_metadata_qa.parquet"),
        ),
        policy_versions=(("institution_master_policy", "institution-master-2026-08-06-v2"),),
    ),
    PipelineStage(
        "apply-geography",
        (
            _output("institutions_geographic", "data/processed/institutions_geographic.parquet"),
            _output("institution_geography_qa", "data/processed/institution_geography_qa.parquet"),
        ),
    ),
    PipelineStage(
        "enrich-institutions",
        (
            _output("institutions_ror", "data/processed/institutions_ror.parquet"),
            _output("institution_ror_qa", "data/processed/institution_ror_qa.parquet"),
        ),
        ("--ror-mode", "cache", "--max-ror-lookups", "0"),
        policy_versions=(("institution_ror_policy", "institution-ror-2026-08-06-v2"),),
    ),
    PipelineStage(
        "build-hierarchy",
        (
            _output("institution_hierarchy", "data/processed/institution_hierarchy.parquet"),
            _output(
                "institution_hierarchy_candidates",
                "data/processed/institution_hierarchy_candidates.parquet",
            ),
            _output(
                "institution_canonicalization_audit",
                "data/processed/institution_canonicalization_audit.parquet",
            ),
        ),
    ),
    PipelineStage(
        "diagnose-versions",
        (
            _output("work_version_diagnostics", "data/processed/work_version_diagnostics.parquet"),
            _output(
                "work_duplicate_doi_diagnostics",
                "data/processed/work_duplicate_doi_diagnostics.parquet",
            ),
            _output(
                "work_ambiguous_version_candidates",
                "data/processed/work_ambiguous_version_candidates.parquet",
            ),
        ),
    ),
    PipelineStage(
        "build-corpus",
        (
            _output("work_corpus", "data/processed/work_corpus.parquet"),
            _output("corpus_annual_counts", "data/processed/corpus_annual_counts.parquet"),
            _output(
                "corpus_topic_family_counts",
                "data/processed/corpus_topic_family_counts.parquet",
            ),
        ),
    ),
    PipelineStage(
        "build-work-institutions",
        (_output("work_institutions", "data/processed/work_institutions.parquet"),),
    ),
    PipelineStage(
        "build-publication-date-qa",
        (
            _output("work_publication_dates", "data/processed/work_publication_dates.parquet"),
            _output(
                "publication_date_coverage_corpus",
                "data/processed/publication_date_coverage_corpus.parquet",
            ),
            _output(
                "publication_date_coverage_year",
                "data/processed/publication_date_coverage_year.parquet",
            ),
            _output(
                "publication_date_coverage_institution",
                "data/processed/publication_date_coverage_institution.parquet",
            ),
            _output(
                "publication_date_coverage_topic_family",
                "data/processed/publication_date_coverage_topic_family.parquet",
            ),
            _output(
                "publication_date_qa_summary",
                "data/reference/publication_date_qa_summary.json",
            ),
        ),
        policy_versions=(("publication_date_policy", "publication-date-qa-2026-08-17-v1"),),
    ),
    PipelineStage(
        "build-edges",
        (
            _output("work_edges", "data/processed/work_edges.parquet"),
            _output("edges_year", "data/processed/edges_year.parquet"),
            _output("edge_work_diagnostics", "data/processed/edge_work_diagnostics.parquet"),
        ),
    ),
    PipelineStage(
        "build-outputs",
        (
            _output("institution_outputs_year", "data/processed/institution_outputs_year.parquet"),
            _output(
                "institution_output_reconciliation",
                "data/processed/institution_output_reconciliation.parquet",
            ),
        ),
    ),
    PipelineStage(
        "build-subannual-facts",
        (
            _output(
                "institution_outputs_month",
                "data/processed/institution_outputs_month.parquet",
            ),
            _output(
                "institution_outputs_quarter",
                "data/processed/institution_outputs_quarter.parquet",
            ),
            _output(
                "collaboration_edges_month",
                "data/processed/collaboration_edges_month.parquet",
            ),
            _output(
                "collaboration_edges_quarter",
                "data/processed/collaboration_edges_quarter.parquet",
            ),
            _output(
                "subannual_reconciliation",
                "data/processed/subannual_reconciliation.parquet",
            ),
            _output("subannual_sparsity", "data/processed/subannual_sparsity.parquet"),
            _output(
                "subannual_temporal_summary",
                "data/reference/subannual_temporal_summary.json",
            ),
        ),
        policy_versions=(("subannual_fact_policy", "subannual-school-facts-2026-08-17-v1"),),
    ),
    PipelineStage(
        "build-rolling-facts",
        (
            _output(
                "institution_outputs_rolling",
                "data/processed/institution_outputs_rolling.parquet",
            ),
            _output(
                "collaboration_edge_window_intervals",
                "data/processed/collaboration_edge_window_intervals.parquet",
            ),
            _output(
                "rolling_window_coverage",
                "data/processed/rolling_window_coverage.parquet",
            ),
            _output(
                "rolling_reconciliation",
                "data/processed/rolling_reconciliation.parquet",
            ),
            _output(
                "rolling_temporal_summary",
                "data/reference/rolling_temporal_summary.json",
            ),
        ),
        policy_versions=(
            ("rolling_fact_policy", "rolling-school-facts-2026-08-17-v1"),
            ("rolling_corpus_views", "strict,broad"),
            ("rolling_hierarchy_views", "organization,umbrella"),
            ("rolling_observation_bounds", "2010-01:2025-12"),
        ),
    ),
    PipelineStage(
        "build-region-flows",
        (
            _output("region_flows_year", "data/processed/region_flows_year.parquet"),
            _output(
                "region_flow_reconciliation",
                "data/processed/region_flow_reconciliation.parquet",
            ),
        ),
    ),
    PipelineStage(
        "validate",
        (_output("edge_arithmetic_validation", "data/reference/edge_arithmetic_validation.json"),),
    ),
    PipelineStage(
        "verify-reproducibility",
        (_output("reproducibility_validation", "data/reference/reproducibility_validation.json"),),
        policy_versions=(("reproducibility_policy", "reproducibility-validation-2026-08-17-v4"),),
    ),
    PipelineStage(
        "compute-edge-intensity",
        (_output("edges_metrics_year", "data/processed/edges_metrics_year.parquet"),),
    ),
    PipelineStage(
        "build-graphs",
        (_output("graph_summary_year", "data/processed/graph_summary_year.parquet"),),
    ),
    PipelineStage(
        "compute-metrics",
        (
            _output("nodes_year", "data/processed/nodes_year.parquet"),
            _output("graph_metrics_year", "data/processed/graph_metrics_year.parquet"),
        ),
    ),
    PipelineStage(
        "detect-communities",
        (
            _output("communities_year", "data/processed/communities_year.parquet"),
            _output(
                "community_sensitivity_year", "data/processed/community_sensitivity_year.parquet"
            ),
        ),
    ),
    PipelineStage(
        "match-communities",
        (
            _output(
                "community_continuity_year",
                "data/processed/community_continuity_year.parquet",
            ),
            _output(
                "community_transitions_year",
                "data/processed/community_transitions_year.parquet",
            ),
        ),
    ),
    PipelineStage(
        "build-layout",
        (_output("network_layout", "data/processed/network_layout.parquet"),),
    ),
    PipelineStage(
        "audit-top-entities",
        (
            _output("top_institution_audit", "data/processed/top_institution_audit.parquet"),
            _output("top_edge_audit", "data/processed/top_edge_audit.parquet"),
        ),
    ),
    PipelineStage(
        "run-sensitivity",
        (
            _output("sensitivity_matrix", "data/processed/sensitivity_matrix.parquet"),
            _output(
                "institution_scope_sensitivity_year",
                "data/processed/institution_scope_sensitivity_year.parquet",
            ),
            _output("sensitivity_summary", "data/reference/sensitivity_summary.json"),
        ),
        policy_versions=(("sensitivity_policy", "required-sensitivity-matrix-2026-08-06-v2"),),
    ),
    PipelineStage(
        "build-figures",
        (_output("trend_series_year", "data/processed/trend_series_year.parquet"),),
    ),
    PipelineStage(
        "build-matrix",
        (
            _output(
                "collaboration_matrix_year",
                "data/processed/collaboration_matrix_year.parquet",
            ),
        ),
    ),
    PipelineStage(
        "build-map-data",
        (
            _output("map_nodes_year", "data/processed/map_nodes_year.parquet"),
            _output("map_edges_year", "data/processed/map_edges_year.parquet"),
            _output("map_coverage_year", "data/processed/map_coverage_year.parquet"),
        ),
    ),
    PipelineStage(
        "build-network-view",
        (
            _output("network_view_nodes_year", "data/processed/network_view_nodes_year.parquet"),
            _output("network_view_edges_year", "data/processed/network_view_edges_year.parquet"),
            _output(
                "network_accessibility_year",
                "data/processed/network_accessibility_year.parquet",
            ),
        ),
        policy_versions=(("network_view_policy", "fixed-layout-network-view-2026-08-06-v2"),),
    ),
    PipelineStage(
        "build-dashboard-data",
        (_output("dashboard_bundle_summary", "data/reference/dashboard_bundle_summary.json"),),
        bundle_metadata=Path("dashboard/data/metadata.json"),
        policy_versions=(("dashboard_bundle_policy", "public-dashboard-bundle-2026-08-28-v6"),),
    ),
)


def validate_stage(
    stage: PipelineStage,
    *,
    config_inputs: dict[str, Path] | None = None,
) -> StageValidation:
    """Check output hashes, current config hashes, and source-manifest ordering."""
    inputs = CONFIG_INPUTS if config_inputs is None else config_inputs
    for output in stage.outputs:
        if not output.manifest.is_file():
            return StageValidation(False, f"missing manifest: {output.manifest}")
        if not output.data.is_file():
            return StageValidation(False, f"missing output: {output.data}")
        try:
            manifest = _load_json(output.manifest)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            return StageValidation(False, f"invalid manifest {output.manifest}: {exc}")
        if manifest.get("status") != "valid":
            return StageValidation(False, f"manifest is not valid: {output.manifest}")
        expected_checksum = manifest.get("checksum_sha256")
        if expected_checksum != file_sha256(output.data):
            return StageValidation(False, f"checksum changed: {output.data}")
        for key, expected_hash in manifest.get("config_hashes", {}).items():
            input_path = inputs.get(str(key))
            if input_path is None:
                return StageValidation(False, f"unknown config provenance key: {key}")
            try:
                current_hash = _semantic_file_hash(input_path)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                return StageValidation(False, f"config input unavailable: {input_path}: {exc}")
            if current_hash != expected_hash:
                return StageValidation(False, f"config changed: {input_path}")
        manifest_versions = manifest.get("source_versions", {})
        if not isinstance(manifest_versions, dict):
            return StageValidation(
                False, f"manifest source_versions are invalid: {output.manifest}"
            )
        for key, expected_version in stage.policy_versions:
            if manifest_versions.get(key) != expected_version:
                return StageValidation(
                    False,
                    f"stage policy changed: {key} expected {expected_version}",
                )
        try:
            created = _parse_timestamp(manifest["created_at_utc"])
        except (KeyError, TypeError, ValueError):
            return StageValidation(False, f"manifest timestamp is invalid: {output.manifest}")
        for source_name in manifest.get("source_manifests", []):
            source_path = Path(str(source_name))
            try:
                source = _load_json(source_path)
                source_created = _parse_timestamp(source["created_at_utc"])
            except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                return StageValidation(False, f"source manifest unavailable: {source_path}: {exc}")
            if source.get("status") != "valid":
                return StageValidation(False, f"source manifest is not valid: {source_path}")
            if source_created > created:
                return StageValidation(False, f"source manifest is newer: {source_path}")
    if stage.bundle_metadata is not None:
        bundle = _validate_bundle(stage.bundle_metadata)
        if not bundle.valid:
            return bundle
    return StageValidation(True, "all manifests, configurations, sources, and hashes are valid")


def run_pipeline(
    *,
    stages: Sequence[PipelineStage],
    runner: Callable[[Sequence[str]], int],
    run_id: str,
    config_path: Path,
    start_year: int,
    end_year: int,
    corpus: str,
    hierarchy: str,
    resume: bool,
    force: bool,
    dry_run: bool,
) -> dict[str, Any]:
    """Validate and execute only missing or stale stages, stopping at the first failure."""
    config_inputs = dict(CONFIG_INPUTS)
    config_inputs["project"] = config_path
    stage_rows: list[dict[str, Any]] = []
    for stage in stages:
        before = validate_stage(stage, config_inputs=config_inputs)
        should_run = force or not before.valid
        command = _stage_arguments(
            stage,
            run_id=run_id,
            config_path=config_path,
            start_year=start_year,
            end_year=end_year,
            corpus=corpus,
            hierarchy=hierarchy,
            resume=resume,
            force=force,
        )
        if dry_run:
            stage_rows.append(
                {
                    "stage": stage.command,
                    "status": "would_run" if should_run else "would_skip",
                    "reason": before.reason,
                    "command": _display_command(command),
                }
            )
            continue
        if not should_run:
            stage_rows.append(
                {
                    "stage": stage.command,
                    "status": "skipped_valid",
                    "reason": before.reason,
                    "command": _display_command(command),
                }
            )
            continue
        exit_code = runner(command)
        if exit_code != 0:
            recovery = _display_command(_with_resume(command))
            stage_rows.append(
                {
                    "stage": stage.command,
                    "status": "failed",
                    "reason": f"stage exited with code {exit_code}",
                    "command": _display_command(command),
                    "recovery_command": recovery,
                }
            )
            return _pipeline_summary(
                run_id,
                stage_rows,
                success=False,
                failed_stage=stage.command,
                recovery_command=recovery,
            )
        after = validate_stage(stage, config_inputs=config_inputs)
        if not after.valid:
            recovery = _display_command(_with_resume(command))
            stage_rows.append(
                {
                    "stage": stage.command,
                    "status": "failed_validation",
                    "reason": after.reason,
                    "command": _display_command(command),
                    "recovery_command": recovery,
                }
            )
            return _pipeline_summary(
                run_id,
                stage_rows,
                success=False,
                failed_stage=stage.command,
                recovery_command=recovery,
            )
        stage_rows.append(
            {
                "stage": stage.command,
                "status": "rebuilt_stale" if before.reason.startswith("source") else "executed",
                "reason": before.reason,
                "command": _display_command(command),
            }
        )
    return _pipeline_summary(run_id, stage_rows, success=True)


def write_pipeline_artifact(
    summary: dict[str, Any],
    *,
    path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    write_json_artifact(
        path=path,
        dataset_name="pipeline_run_summary",
        payload=summary,
        records=list(summary["stages"]),
        primary_key=["stage"],
        run_id=run_id,
        config_hashes={"project": config_file_hash(project_config_path)},
        source_versions={"pipeline_orchestrator": "manifest-aware-pipeline-2026-08-06-v2"},
        source_manifests=[
            str(output.manifest) for stage in DEFAULT_STAGES for output in stage.outputs
        ],
        command=command,
    )


def _stage_arguments(
    stage: PipelineStage,
    *,
    run_id: str,
    config_path: Path,
    start_year: int,
    end_year: int,
    corpus: str,
    hierarchy: str,
    resume: bool,
    force: bool,
) -> list[str]:
    arguments = [stage.command, *stage.arguments]
    if not stage.accepts_common_options:
        return arguments
    arguments.extend(
        [
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--start-year",
            str(start_year),
            "--end-year",
            str(end_year),
            "--corpus",
            corpus,
            "--hierarchy",
            hierarchy,
        ]
    )
    if resume or stage.preserves_raw_data:
        arguments.append("--resume")
    if force and not stage.preserves_raw_data:
        arguments.append("--force")
    return arguments


def _pipeline_summary(
    run_id: str,
    stages: list[dict[str, Any]],
    *,
    success: bool,
    failed_stage: str | None = None,
    recovery_command: str | None = None,
) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for stage in stages:
        status = str(stage["status"])
        counts[status] = counts.get(status, 0) + 1
    return {
        "schema_version": 1,
        "run_id": run_id,
        "success": success,
        "stage_count": len(stages),
        "status_counts": counts,
        "failed_stage": failed_stage,
        "recovery_command": recovery_command,
        "raw_data_deleted": False,
        "stages": stages,
    }


def _validate_bundle(metadata_path: Path) -> StageValidation:
    try:
        metadata = _load_json(metadata_path)
        tables = metadata["tables"]
        if not isinstance(tables, dict) or not tables:
            raise ValueError("bundle table index is empty")
        for name, raw in tables.items():
            if not isinstance(raw, dict):
                raise ValueError(f"invalid bundle table entry: {name}")
            path = Path(str(raw["path"]))
            if not path.is_file():
                raise ValueError(f"missing dashboard table: {path}")
            if file_sha256(path) != raw["sha256"]:
                raise ValueError(f"dashboard table checksum changed: {path}")
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return StageValidation(False, f"dashboard bundle is invalid: {exc}")
    return StageValidation(True, "dashboard bundle hashes are valid")


def _semantic_file_hash(path: Path) -> str:
    if path.suffix.casefold() == ".json":
        return semantic_hash(json.loads(path.read_text(encoding="utf-8")))
    if path.suffix.casefold() == ".csv":
        return file_sha256(path)
    return config_file_hash(path)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("expected a JSON object")
    return value


def _parse_timestamp(value: object) -> datetime:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _display_command(arguments: Sequence[str]) -> str:
    return "uv run python -m gisnet.cli " + " ".join(arguments)


def _with_resume(arguments: Sequence[str]) -> list[str]:
    result = [value for value in arguments if value != "--force"]
    if "--resume" not in result:
        result.append("--resume")
    return result
