"""Strict loader for the versioned school-decision analytical contract."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gisnet.artifacts import current_git_commit, utc_timestamp
from gisnet.config import config_file_hash, load_yaml
from gisnet.dataset import file_sha256
from gisnet.manifest import DatasetManifest


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MetricClass(StrEnum):
    DESCRIPTIVE = "descriptive"
    NORMALIZED = "normalized"
    BIBLIOMETRIC = "bibliometric"
    NETWORK_DERIVED = "network_derived"
    USER_DEFINED = "user_defined"


class MetricAvailability(StrEnum):
    CURRENT_ANNUAL = "current_annual"
    CURRENT_ANNUAL_CORE_LIMITED = "current_annual_core_limited"
    PLANNED = "planned"
    UI_ONLY = "ui_only"


class SourceLayer(StrEnum):
    PUBLICATION = "publication"
    COAUTHORSHIP = "coauthorship"
    CITATION = "citation_flow"
    TOPIC_PROFILE = "topic_profile"
    TOPIC_SIMILARITY = "topic_similarity"
    USER_INPUT = "user_input"


class ModeAvailability(StrEnum):
    CURRENT = "current"
    PLANNED = "planned"


class MetricDefinition(StrictModel):
    label: str
    metric_class: MetricClass
    definition: str
    unit: str
    time_basis: str
    source_layer: SourceLayer
    availability: MetricAvailability
    interpretation: str
    forbidden_interpretations: list[str] = Field(min_length=1)
    comparability: str


class DimensionDefinition(StrictModel):
    label: str
    definition: str
    metric_ids: list[str] = Field(min_length=1)


class LayerDefinition(StrictModel):
    label: str
    directionality: str
    meaning: str
    must_not_mean: str
    merge_policy: str


class AnalyticalMode(StrictModel):
    label: str
    definition: str
    time_basis: str
    default_window: str
    partial_current_year: bool
    comparison_policy: str
    availability: ModeAvailability


class SchoolProfilePolicy(StrictModel):
    primary_entity: str
    interface_term: str
    interface_term_scope: str
    eligible_entity_definition: str
    eligibility_flag: Literal["is_primary_research_scope"]
    primary_scope_categories: list[str] = Field(min_length=1)
    secondary_entity_policy: str
    geographic_scope: list[str] = Field(min_length=1)
    stable_join_keys: list[str] = Field(min_length=1)
    forbidden_membership_thresholds: list[str] = Field(min_length=1)
    coordinate_policy: str
    canonical_identity_policy: str
    unresolved_identity_policy: str


class FitComponentTransform(StrictModel):
    method: Literal["identity_0_1", "average_percentile_rank_0_1"]
    reference_set: str
    missing_policy: str
    degenerate_reference_value: float = Field(ge=0.0, le=1.0)


class FitScorePolicy(StrictModel):
    global_quality_score_allowed: bool
    allowed_combined_score_name: str
    definition: str
    allowed_component_metric_ids: list[str] = Field(min_length=1)
    transform_definitions: dict[str, str]
    component_transforms: dict[str, FitComponentTransform]
    weight_storage: str
    persisted_to_scientific_datasets: bool
    missing_component_policy: str
    zero_weight_policy: str
    required_disclosure: str


class TopicQualityPolicy(StrictModel):
    registry_status: str
    human_review_complete: bool
    required_warning: str
    ranking_authority_claim_allowed: bool


class SchoolDecisionContract(StrictModel):
    schema_version: int
    contract_version: str
    title: str
    product_claim: str
    non_claims: list[str] = Field(min_length=1)
    metric_classes: dict[MetricClass, str]
    analytical_modes: dict[str, AnalyticalMode]
    primary_time_keys: list[str]
    school_profile: SchoolProfilePolicy
    metrics: dict[str, MetricDefinition]
    dimensions: dict[str, DimensionDefinition]
    layers: dict[str, LayerDefinition]
    fit_score_policy: FitScorePolicy
    topic_quality_policy: TopicQualityPolicy

    @model_validator(mode="after")
    def validate_contract(self) -> SchoolDecisionContract:
        required_classes = set(MetricClass)
        if set(self.metric_classes) != required_classes:
            raise ValueError("metric_classes must define every supported metric class exactly once")
        required_modes = {"historical_scientific", "current_school_decision"}
        if set(self.analytical_modes) != required_modes:
            raise ValueError("both historical and current analytical modes are required")
        if self.analytical_modes["historical_scientific"].availability != ModeAvailability.CURRENT:
            raise ValueError("historical scientific mode must remain current")
        if (
            self.analytical_modes["current_school_decision"].availability
            != ModeAvailability.PLANNED
        ):
            raise ValueError("current school-decision mode remains planned until GISNET-124")
        required_time_keys = {
            "publication_month",
            "publication_quarter",
            "publication_year",
            "window_start",
            "window_end",
            "window_months",
        }
        if not required_time_keys.issubset(self.primary_time_keys):
            raise ValueError("the required subannual and rolling time keys are incomplete")
        required_primary_categories = {
            "education",
            "government_research",
            "nonprofit_research",
            "research_facility",
        }
        if set(self.school_profile.primary_scope_categories) != required_primary_categories:
            raise ValueError("school eligibility must use normalized primary research categories")
        required_dimensions = {
            "research_activity",
            "research_specialization",
            "collaboration_reach",
            "collaboration_persistence",
            "network_position",
            "citation_influence",
            "research_proximity",
            "recent_momentum",
            "user_defined_research_fit",
        }
        missing_dimensions = required_dimensions.difference(self.dimensions)
        if missing_dimensions:
            raise ValueError(f"required analytical dimensions are missing: {missing_dimensions}")
        for dimension_id, dimension in self.dimensions.items():
            unknown = set(dimension.metric_ids).difference(self.metrics)
            if unknown:
                raise ValueError(f"dimension {dimension_id} references unknown metrics: {unknown}")
        required_layers = {"coauthorship", "citation_flow", "topic_similarity"}
        if set(self.layers) != required_layers:
            raise ValueError(
                "coauthorship, citation flow, and Topic similarity must remain separate"
            )
        if any(
            layer.merge_policy != "never_merge_into_scientific_edge_weight"
            for layer in self.layers.values()
        ):
            raise ValueError("scientific network layers may not be merged into one edge weight")
        expected_directions = {
            "coauthorship": "undirected",
            "citation_flow": "directed_citing_institution_to_cited_institution",
            "topic_similarity": "undirected_similarity",
        }
        for layer_id, directionality in expected_directions.items():
            if self.layers[layer_id].directionality != directionality:
                raise ValueError(f"layer {layer_id} has an invalid directionality contract")
        score = self.fit_score_policy
        if score.global_quality_score_allowed:
            raise ValueError("a global university-quality score is prohibited")
        if score.allowed_combined_score_name != "user_defined_fit_score":
            raise ValueError("the only allowed combined score name is user_defined_fit_score")
        if score.persisted_to_scientific_datasets:
            raise ValueError("user-defined fit weights and scores must remain outside source data")
        if score.weight_storage != "streamlit_session_state_only":
            raise ValueError("user-defined fit weights must remain in UI session state only")
        unknown_components = set(score.allowed_component_metric_ids).difference(self.metrics)
        if unknown_components:
            raise ValueError(
                f"fit policy references unknown component metrics: {unknown_components}"
            )
        if "user_defined_fit_score" in score.allowed_component_metric_ids:
            raise ValueError("user_defined_fit_score may not recursively include itself")
        if set(score.component_transforms) != set(score.allowed_component_metric_ids):
            raise ValueError("every fit component requires exactly one transformation contract")
        if set(score.transform_definitions) != {
            "identity_0_1",
            "average_percentile_rank_0_1",
        }:
            raise ValueError("fit transformation definitions are incomplete")
        fit_metric = self.metrics.get("user_defined_fit_score")
        if fit_metric is None:
            raise ValueError("user_defined_fit_score metric definition is required")
        expected_fit_semantics = (
            MetricClass.USER_DEFINED,
            MetricAvailability.UI_ONLY,
            SourceLayer.USER_INPUT,
        )
        if (
            fit_metric.metric_class,
            fit_metric.availability,
            fit_metric.source_layer,
        ) != expected_fit_semantics:
            raise ValueError("user_defined_fit_score must remain user-defined, UI-only input")
        if self.topic_quality_policy.human_review_complete:
            raise ValueError("the current provisional Topic registry is not human-review complete")
        if self.topic_quality_policy.ranking_authority_claim_allowed:
            raise ValueError("provisional Topic results may not claim ranking authority")
        forbidden_metric_ids = {
            metric_id
            for metric_id in self.metrics
            if ("quality" in metric_id and "score" in metric_id)
            or "university_rank" in metric_id
            or metric_id.startswith("best_university")
        }
        if forbidden_metric_ids:
            raise ValueError("global quality-score metrics are prohibited")
        return self


def load_school_decision_contract(
    path: str | Path = "config/school_decision.yml",
) -> SchoolDecisionContract:
    """Load and strictly validate the school-decision analytical contract."""
    return SchoolDecisionContract.model_validate(load_yaml(path))


DEFAULT_SOURCE_MANIFESTS = [
    ".agent/manifests/institution_type_profile.json",
    ".agent/manifests/institution_hierarchy_summary.json",
    ".agent/manifests/collaboration_edges_summary.json",
    ".agent/manifests/institution_outputs_summary.json",
    ".agent/manifests/network_metrics_summary.json",
    ".agent/manifests/citation_flow_summary.json",
    ".agent/manifests/topic_similarity_summary.json",
]


def write_school_decision_contract_manifest(
    *,
    contract_path: str | Path,
    institution_types_path: str | Path,
    topic_registry_path: str | Path,
    project_path: str | Path,
    manifest_path: str | Path,
    run_id: str,
    command: str,
    source_manifests: list[str] | None = None,
) -> DatasetManifest:
    """Validate the contract and write a reproducible provenance manifest."""
    contract = load_school_decision_contract(contract_path)
    institution_types = _mapping(load_yaml(institution_types_path), "institution types")
    topic_registry = _mapping(load_yaml(topic_registry_path), "Topic registry")
    project = _mapping(load_yaml(project_path), "project configuration")
    sources = DEFAULT_SOURCE_MANIFESTS if source_manifests is None else source_manifests
    missing = [path for path in sources if not Path(path).is_file()]
    if missing:
        raise ValueError(f"school-decision contract source manifests are missing: {missing}")
    manifest = DatasetManifest(
        dataset_name="school_decision_contract",
        created_at_utc=utc_timestamp(),
        run_id=run_id,
        git_commit=current_git_commit(),
        config_hashes={
            "school_decision_contract": config_file_hash(contract_path),
            "institution_types": config_file_hash(institution_types_path),
            "topic_registry": config_file_hash(topic_registry_path),
            "project": config_file_hash(project_path),
        },
        source_manifests=sources,
        source_versions={
            "school_decision_contract": contract.contract_version,
            "institution_types": str(institution_types["policy_version"]),
            "topic_registry": str(topic_registry["registry_version"]),
            "project": str(project["project_version"]),
        },
        row_count=len(contract.metrics),
        column_count=len(MetricDefinition.model_fields) + 1,
        primary_key=["metric_id"],
        null_counts={field: 0 for field in ("metric_id", *MetricDefinition.model_fields)},
        checksum_sha256=file_sha256(contract_path),
        command=command,
    )
    manifest.write(str(manifest_path))
    return manifest


def _mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a mapping")
    return value
