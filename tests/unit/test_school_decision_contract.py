from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import cast

import pytest
import yaml
from pydantic import ValidationError

from gisnet.cli import main
from gisnet.config import config_file_hash
from gisnet.dataset import file_sha256
from gisnet.schools.contract import (
    MetricClass,
    SchoolDecisionContract,
    load_school_decision_contract,
)

ROOT = Path(__file__).resolve().parents[2]


def _raw_contract() -> dict[str, object]:
    value = yaml.safe_load((ROOT / "config" / "school_decision.yml").read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return cast(dict[str, object], value)


def test_school_decision_contract_defines_separate_non_quality_dimensions() -> None:
    contract = load_school_decision_contract(ROOT / "config" / "school_decision.yml")

    assert set(contract.metric_classes) == set(MetricClass)
    assert {
        "research_activity",
        "research_specialization",
        "collaboration_reach",
        "collaboration_persistence",
        "network_position",
        "citation_influence",
        "research_proximity",
        "recent_momentum",
        "user_defined_research_fit",
    }.issubset(contract.dimensions)
    assert set(contract.layers) == {"coauthorship", "citation_flow", "topic_similarity"}
    assert all(
        layer.merge_policy == "never_merge_into_scientific_edge_weight"
        for layer in contract.layers.values()
    )
    assert contract.fit_score_policy.global_quality_score_allowed is False
    assert contract.fit_score_policy.allowed_combined_score_name == "user_defined_fit_score"
    assert contract.fit_score_policy.persisted_to_scientific_datasets is False
    assert contract.analytical_modes["historical_scientific"].availability.value == "current"
    assert contract.analytical_modes["current_school_decision"].availability.value == "planned"
    assert contract.school_profile.eligibility_flag == "is_primary_research_scope"
    assert set(contract.school_profile.primary_scope_categories) == {
        "education",
        "government_research",
        "nonprofit_research",
        "research_facility",
    }
    assert contract.metrics["citation_flow_fractional_in_strength"].availability.value == "planned"
    assert set(contract.fit_score_policy.component_transforms) == set(
        contract.fit_score_policy.allowed_component_metric_ids
    )
    assert contract.topic_quality_policy.human_review_complete is False
    assert contract.topic_quality_policy.ranking_authority_claim_allowed is False
    assert contract.school_profile.forbidden_membership_thresholds == [
        "global_top_node_rank",
        "global_top_edge_rank",
        "map_top_n",
        "visualization_score",
        "coordinate_presence",
    ]


def test_school_decision_contract_rejects_global_quality_score() -> None:
    raw = _raw_contract()
    fit_policy = raw["fit_score_policy"]
    assert isinstance(fit_policy, dict)
    fit_policy["global_quality_score_allowed"] = True

    with pytest.raises(ValidationError, match="global university-quality score is prohibited"):
        SchoolDecisionContract.model_validate(raw)


def test_school_decision_contract_rejects_unknown_metric_reference() -> None:
    raw = _raw_contract()
    dimensions = raw["dimensions"]
    assert isinstance(dimensions, dict)
    activity = dimensions["research_activity"]
    assert isinstance(activity, dict)
    metric_ids = activity["metric_ids"]
    assert isinstance(metric_ids, list)
    metric_ids.append("invented_metric")

    with pytest.raises(ValidationError, match="references unknown metrics"):
        SchoolDecisionContract.model_validate(raw)


def test_school_decision_contract_rejects_missing_dimension_and_layer_merge() -> None:
    missing_dimension = _raw_contract()
    dimensions = missing_dimension["dimensions"]
    assert isinstance(dimensions, dict)
    dimensions.pop("recent_momentum")
    with pytest.raises(ValidationError, match="required analytical dimensions are missing"):
        SchoolDecisionContract.model_validate(missing_dimension)

    merged_layer = _raw_contract()
    layers = merged_layer["layers"]
    assert isinstance(layers, dict)
    coauthorship = layers["coauthorship"]
    assert isinstance(coauthorship, dict)
    coauthorship["merge_policy"] = "weighted_merge"
    with pytest.raises(ValidationError, match="may not be merged"):
        SchoolDecisionContract.model_validate(merged_layer)

    wrong_direction = _raw_contract()
    layers = wrong_direction["layers"]
    assert isinstance(layers, dict)
    citation = layers["citation_flow"]
    assert isinstance(citation, dict)
    citation["directionality"] = "undirected"
    with pytest.raises(ValidationError, match="invalid directionality"):
        SchoolDecisionContract.model_validate(wrong_direction)


def test_school_decision_contract_rejects_persisted_user_fit() -> None:
    raw = _raw_contract()
    fit_policy = raw["fit_score_policy"]
    assert isinstance(fit_policy, dict)
    fit_policy["persisted_to_scientific_datasets"] = True

    with pytest.raises(ValidationError, match="must remain outside source data"):
        SchoolDecisionContract.model_validate(raw)

    wrong_storage = _raw_contract()
    fit_policy = wrong_storage["fit_score_policy"]
    assert isinstance(fit_policy, dict)
    fit_policy["weight_storage"] = "scientific_dataset"
    with pytest.raises(ValidationError, match="UI session state only"):
        SchoolDecisionContract.model_validate(wrong_storage)

    recursive = _raw_contract()
    fit_policy = recursive["fit_score_policy"]
    assert isinstance(fit_policy, dict)
    components = fit_policy["allowed_component_metric_ids"]
    transforms = fit_policy["component_transforms"]
    assert isinstance(components, list)
    assert isinstance(transforms, dict)
    components.append("user_defined_fit_score")
    transforms["user_defined_fit_score"] = deepcopy(transforms["topic_fit_similarity"])
    with pytest.raises(ValidationError, match="may not recursively include itself"):
        SchoolDecisionContract.model_validate(recursive)


def test_school_decision_contract_rejects_disguised_quality_and_persisted_fit_metric() -> None:
    quality = _raw_contract()
    metrics = quality["metrics"]
    assert isinstance(metrics, dict)
    metrics["best_university_score"] = deepcopy(metrics["user_defined_fit_score"])
    with pytest.raises(ValidationError, match="global quality-score metrics are prohibited"):
        SchoolDecisionContract.model_validate(quality)

    persisted_fit = _raw_contract()
    metrics = persisted_fit["metrics"]
    assert isinstance(metrics, dict)
    fit_metric = metrics["user_defined_fit_score"]
    assert isinstance(fit_metric, dict)
    fit_metric["availability"] = "current_annual"
    fit_metric["source_layer"] = "publication"
    with pytest.raises(ValidationError, match="must remain user-defined, UI-only input"):
        SchoolDecisionContract.model_validate(persisted_fit)


def test_school_decision_backlog_is_persisted_in_both_contract_surfaces() -> None:
    backlog = json.loads((ROOT / ".agent" / "backlog.json").read_text(encoding="utf-8"))
    task_ids = {task["id"] for task in backlog["tasks"]}

    assert {f"GISNET-{number}" for number in range(120, 140)}.issubset(task_ids)
    canonical = (ROOT / "AI_EXECUTION_BACKLOG_GIS_COLLABORATION.md").read_text(encoding="utf-8")
    assert "### P12. Research-based school decision and institutional comparison" in canonical
    assert "#### GISNET-120: Define school-decision analytical contract" in canonical
    assert "#### GISNET-139: Update release and documentation" in canonical
    tasks = {task["id"]: task for task in backlog["tasks"]}
    assert "GISNET-139" in tasks["GISNET-113"]["dependencies"]
    assert "GISNET-139" in tasks["GISNET-114"]["dependencies"]


def test_school_decision_contract_manifest_matches_source() -> None:
    manifest = json.loads(
        (ROOT / ".agent" / "manifests" / "school_decision_contract.json").read_text(
            encoding="utf-8"
        )
    )
    source = ROOT / "config" / "school_decision.yml"

    assert manifest["dataset_name"] == "school_decision_contract"
    assert manifest["status"] == "valid"
    assert manifest["checksum_sha256"] == file_sha256(source)
    assert manifest["config_hashes"]["school_decision_contract"] == config_file_hash(source)
    assert set(manifest["config_hashes"]) == {
        "institution_types",
        "project",
        "school_decision_contract",
        "topic_registry",
    }
    assert manifest["source_manifests"]
    assert manifest["command"] == "python -m gisnet.cli validate-school-contract --resume"


def test_validate_school_contract_cli_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["validate-school-contract", "--dry-run"]) == 0
    assert "Validated 30 school-decision metrics" in capsys.readouterr().out
