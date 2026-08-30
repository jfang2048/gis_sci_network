"""Tests for the auditable GISNET-138 acceptance artifact."""

import json
from pathlib import Path

import pytest

from gisnet.validation.school_decision import (
    _order_checks,
    write_school_decision_validation_artifact,
)

CHECK_IDS = [
    "outside_prior_core_school_searchable",
    "outside_global_edge_core_school_has_ego_partners",
    "subannual_reconciliation",
    "rolling_12m_calendar_boundaries",
    "no_imputed_publication_months",
    "region_flow_reconciliation",
    "country_map_matrix_reconciliation",
    "calibrated_edge_width_semantics",
    "profile_compare_source_equality",
    "strict_subset_broad",
    "public_output_secret_scan",
    "deterministic_rebuild_evidence",
    "annual_pipeline_regression_evidence",
]


def _checks() -> list[dict[str, object]]:
    return [
        {
            "check_id": check_id,
            "requirement": f"Requirement for {check_id}",
            "passed": True,
            "evidence": {"mismatch_count": 0},
        }
        for check_id in reversed(CHECK_IDS)
    ]


def test_acceptance_matrix_requires_all_thirteen_checks_in_contract_order() -> None:
    ordered = _order_checks(_checks())

    assert [check["check_id"] for check in ordered] == CHECK_IDS
    with pytest.raises(ValueError, match="acceptance matrix mismatch"):
        _order_checks(_checks()[:-1])


def test_validation_artifact_is_atomic_and_manifested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config"
    config.mkdir()
    project_config = config / "project.yml"
    decision_config = config / "school_decision.yml"
    project_config.write_text("schema_version: 1\n", encoding="utf-8")
    decision_config.write_text("schema_version: 1\n", encoding="utf-8")
    payload = {
        "schema_version": 1,
        "validation_version": "test-v1",
        "status": "passed",
        "acceptance_check_count": 13,
        "passed_check_count": 13,
        "logical_input_hash": "abc123",
        "checks": _order_checks(_checks()),
        "required_regression_commands": ["pytest"],
        "generated_at_utc": "2026-08-31T00:00:00Z",
    }
    output = tmp_path / "data/reference/school_decision_validation.json"

    write_school_decision_validation_artifact(
        payload,
        path=output,
        run_id="test-run",
        project_config_path=project_config,
        school_decision_path=decision_config,
        command="python -m gisnet.cli validate-school-decision --resume",
    )

    assert json.loads(output.read_text(encoding="utf-8")) == payload
    manifest = json.loads(
        (tmp_path / ".agent/manifests/school_decision_validation.json").read_text(encoding="utf-8")
    )
    assert manifest["dataset_name"] == "school_decision_validation"
    assert manifest["row_count"] == 13
    assert manifest["primary_key"] == ["check_id"]
    assert manifest["run_id"] == "test-run"
