"""Real-snapshot acceptance evidence for GISNET-138 when processed sources are present."""

from pathlib import Path

import pytest

from gisnet.validation.school_decision import validate_school_decision_system


@pytest.mark.integration
def test_complete_school_decision_acceptance_matrix_is_stable() -> None:
    root = Path(__file__).resolve().parents[2]
    required_local_source = root / "data/processed/school_partner_index.parquet"
    if not required_local_source.is_file():
        pytest.skip("local processed school-decision sources are not available")
    exclusions = (
        "data/reference/school_decision_validation.json",
        ".agent/manifests/school_decision_validation.json",
    )

    first = validate_school_decision_system(root=root, excluded_public_paths=exclusions)
    second = validate_school_decision_system(root=root, excluded_public_paths=exclusions)

    assert first["status"] == "passed"
    assert first["acceptance_check_count"] == 13
    assert first["passed_check_count"] == 13
    assert {check["check_id"] for check in first["checks"]} == {
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
    }
    assert first["logical_input_hash"] == second["logical_input_hash"]
    privacy = next(
        check for check in first["checks"] if check["check_id"] == "public_output_secret_scan"
    )["evidence"]
    assert privacy == {
        "current_public_file_scan_completed": True,
        "privacy_scan_finding_count": 0,
        "release_manifest_verified": True,
        "release_privacy_scan_finding_count": 0,
    }
