from __future__ import annotations

from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from gisnet.dataset import file_sha256
from gisnet.schools.comparison import (
    FIT_COMPONENT_IDS,
    SchoolComparisonFilters,
    SortMetric,
    compare_schools,
    search_schools,
)

ROOT = Path(__file__).resolve().parents[2]


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    pq.write_table(pa.Table.from_pylist(rows), path)


def _comparison_sources(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    index = tmp_path / "school_index.parquet"
    names = tmp_path / "school_name_index.parquet"
    profiles = tmp_path / "school_profiles.parquet"
    topics = tmp_path / "school_topic_profiles.parquet"
    schools = [
        ("I1", "Alpha University", "NL", "Europe", "Western Europe"),
        ("I2", "Beta University", "DE", "Europe", "Western Europe"),
        ("I3", "Gamma Institute", "US", "Americas", "Northern America"),
        ("I4", "Delta School", "JP", "Asia", "Eastern Asia"),
    ]
    _write(
        index,
        [
            {
                "canonical_school_id": school_id,
                "display_name": name,
                "country_code": country,
                "country_name": country,
                "macro_region": region,
                "subregion": subregion,
                "institution_category": "education",
                "identity_status": "retained_source_organization",
                "identity_resolution_confidence": "source_identity",
                "identity_quality_flags": [],
                "eligibility_status": "eligible_primary_research_broad",
                "support_status": "supported",
            }
            for school_id, name, country, region, subregion in schools
        ],
    )
    _write(
        names,
        [
            {
                "normalized_name": normalized,
                "canonical_school_id": school_id,
                "display_name": name,
                "country_code": country,
                "matched_names": matched_names,
                "match_types": ["display" if normalized != "shared institute" else "alternative"],
                "ambiguity_count": ambiguity_count,
                "is_ambiguous": ambiguity_count > 1,
            }
            for normalized, school_id, name, country, matched_names, ambiguity_count in [
                ("alpha university", "I1", "Alpha University", "NL", ["Alpha University"], 1),
                ("shared institute", "I1", "Alpha University", "NL", ["Shared Institute"], 2),
                ("beta university", "I2", "Beta University", "DE", ["Beta University"], 1),
                ("shared institute", "I2", "Beta University", "DE", ["Shared Institute"], 2),
                ("gamma institute", "I3", "Gamma Institute", "US", ["Gamma Institute"], 1),
                ("delta school", "I4", "Delta School", "JP", ["Delta School"], 1),
            ]
        ],
    )
    metrics = {
        "I1": (10, 0.2, 2.0, 0.30, 0.10, 5.0, 0.5, 1.0),
        "I2": (20, 0.8, 3.0, 0.20, 0.70, 4.0, -0.5, 0.8),
        "I3": (5, 0.5, 1.0, 0.10, None, 2.0, 0.0, 0.9),
        "I4": (0, None, 0.0, None, None, 0.0, None, None),
    }
    _write(
        profiles,
        [
            {
                "canonical_school_id": school_id,
                "corpus_view": "broad",
                "window_start": "2024-01",
                "window_end": "2025-12",
                "window_months": 24,
                "profile_support_status": "supported" if values[0] else "no_recent_activity",
                "full_work_count": values[0],
                "recent_24m_work_count": values[0],
                "international_collaboration_share": values[1],
                "effective_partner_count": values[2],
                "pagerank": values[3],
                "bridge_score": values[4],
                "citation_flow_fractional_in_strength": values[5],
                "rolling_12m_activity_change": values[6],
                "annual_graph_year": 2025 if values[3] is not None else None,
                "annual_graph_boundary": "broad organization coauthorship",
                "annual_network_support_status": (
                    "supported" if values[3] is not None else "not_observed"
                ),
                "citation_flow_year": 2025,
                "citation_flow_boundary": "broad closed corpus",
                "citation_flow_support_status": "supported",
                "momentum_support_status": (
                    "supported" if values[6] is not None else "insufficient_prior_activity"
                ),
                "date_coverage_ratio": values[7],
                "date_coverage_status": "exact",
                "quality_flags": [],
            }
            for school_id, *_ in schools
            for values in [metrics[school_id]]
        ],
    )
    _write(
        topics,
        [
            {
                "canonical_school_id": school_id,
                "corpus_view": "broad",
                "window_start": "2024-01",
                "window_end": "2025-12",
                "window_months": 24,
                "topic_family": family,
                "topic_family_share": share,
                "specialization_lift_global": lift,
                "specialization_lift_macro_region": lift,
                "specialization_lift_country": lift,
                "provisional_topic_registry": True,
                "topic_profile_support_status": "supported",
            }
            for school_id, family, share, lift in [
                ("I1", "core_gis", 0.8, 2.0),
                ("I1", "remote_sensing_earth_observation", 0.2, 0.5),
                ("I2", "core_gis", 0.2, 0.5),
                ("I2", "remote_sensing_earth_observation", 0.8, 2.0),
                ("I3", "remote_sensing_earth_observation", 1.0, 2.5),
            ]
        ],
    )
    return index, names, profiles, topics


def test_school_search_uses_complete_alias_index_and_returns_stable_ids(tmp_path: Path) -> None:
    index, names, _, _ = _comparison_sources(tmp_path)

    direct = search_schools(index, names, query="I4")
    ambiguous = search_schools(index, names, query="Shared Institute")

    assert direct[0]["canonical_school_id"] == "I4"
    assert direct[0]["match_basis"] == "stable_id"
    assert {row["canonical_school_id"] for row in ambiguous} == {"I1", "I2"}
    assert all(row["is_ambiguous"] for row in ambiguous)
    assert all(row["ambiguity_count"] == 2 for row in ambiguous)


def test_school_comparison_filters_complete_index_and_sorts_independent_metrics(
    tmp_path: Path,
) -> None:
    index, _, profiles, topics = _comparison_sources(tmp_path)
    complete = compare_schools(
        index,
        profiles,
        topics,
        filters=SchoolComparisonFilters(),
        contract_path=ROOT / "config" / "school_decision.yml",
    )
    assert complete.candidate_count == 4
    assert complete.rows[-1]["canonical_school_id"] == "I4"
    filters = SchoolComparisonFilters(
        corpus_view="broad",
        window_months=24,
        macro_region="Europe",
        topic_family="core_gis",
        minimum_recent_activity=5,
        minimum_date_coverage=0.75,
    )

    specialization = compare_schools(
        index,
        profiles,
        topics,
        filters=filters,
        sort_metric="specialization_lift_global",
        contract_path=ROOT / "config" / "school_decision.yml",
    )
    international = compare_schools(
        index,
        profiles,
        topics,
        filters=filters,
        sort_metric="international_collaboration_share",
        contract_path=ROOT / "config" / "school_decision.yml",
    )

    assert specialization.candidate_count == 2
    assert [row["canonical_school_id"] for row in specialization.rows] == ["I1", "I2"]
    assert specialization.rows[0]["specialization_lift_global"] == 2.0
    assert [row["canonical_school_id"] for row in international.rows] == ["I2", "I1"]
    assert international.disclosure["sort_metric"] == "international_collaboration_share"
    assert international.disclosure["filters"]["window_months"] == 24
    expected_first: dict[SortMetric, str] = {
        "full_work_count": "I2",
        "international_collaboration_share": "I2",
        "effective_partner_count": "I2",
        "pagerank": "I1",
        "citation_flow_fractional_in_strength": "I1",
        "rolling_12m_activity_change": "I1",
    }
    for metric, expected_id in expected_first.items():
        result = compare_schools(
            index,
            profiles,
            topics,
            filters=filters,
            sort_metric=metric,
            contract_path=ROOT / "config" / "school_decision.yml",
        )
        assert result.rows[0]["canonical_school_id"] == expected_id

    country = compare_schools(
        index,
        profiles,
        topics,
        filters=SchoolComparisonFilters(
            country_code="DE",
            subregion="Western Europe",
            minimum_recent_activity=15,
            minimum_date_coverage=0.8,
        ),
        contract_path=ROOT / "config" / "school_decision.yml",
    )
    assert [row["canonical_school_id"] for row in country.rows] == ["I2"]


def test_user_fit_is_session_only_transparent_and_uses_filtered_reference_set(
    tmp_path: Path,
) -> None:
    index, _, profiles, topics = _comparison_sources(tmp_path)
    before = {path: file_sha256(path) for path in (index, profiles, topics)}
    weights = {
        "topic_fit_similarity": 2.0,
        "recent_24m_work_count": 1.0,
        "international_collaboration_share": 1.0,
        "bridge_score": 0.0,
        "rolling_12m_activity_change": 0.0,
    }

    result = compare_schools(
        index,
        profiles,
        topics,
        filters=SchoolComparisonFilters(macro_region="Europe", window_months=24),
        sort_metric="user_defined_fit_score",
        fit_weights=weights,
        topic_preferences={"core_gis": 1.0},
        contract_path=ROOT / "config" / "school_decision.yml",
    )

    assert result.candidate_count == 2
    assert result.disclosure["fit_session"]["weights"] == weights
    assert result.disclosure["fit_session"]["candidate_count"] == 2
    assert result.disclosure["fit_session"]["component_reference_counts"] == {
        component: 2 for component in FIT_COMPONENT_IDS
    }
    assert set(result.disclosure["fit_session"]["component_transforms"]) == set(FIT_COMPONENT_IDS)
    rows = {row["canonical_school_id"]: row for row in result.rows}
    assert rows["I1"]["fit_components_raw"]["recent_24m_work_count"] == 10
    assert rows["I1"]["fit_components_transformed"]["recent_24m_work_count"] == 0.0
    assert rows["I2"]["fit_components_transformed"]["recent_24m_work_count"] == 1.0
    assert rows["I1"]["fit_components_raw"]["topic_fit_similarity"] == pytest.approx(
        0.8 / (0.8**2 + 0.2**2) ** 0.5
    )
    assert rows["I1"]["user_defined_fit_score"] == pytest.approx(
        (2 * rows["I1"]["fit_components_raw"]["topic_fit_similarity"] + 0.0 + 0.2) / 4
    )
    assert all("quality_score" not in row for row in result.rows)
    assert before == {path: file_sha256(path) for path in before}


def test_user_fit_nulls_positive_weight_missing_components_and_validates_inputs(
    tmp_path: Path,
) -> None:
    index, _, profiles, topics = _comparison_sources(tmp_path)
    result = compare_schools(
        index,
        profiles,
        topics,
        filters=SchoolComparisonFilters(stable_school_ids=("I3",)),
        fit_weights={"bridge_score": 1.0},
        contract_path=ROOT / "config" / "school_decision.yml",
    )
    assert result.rows[0]["user_defined_fit_score"] is None

    degenerate = compare_schools(
        index,
        profiles,
        topics,
        filters=SchoolComparisonFilters(stable_school_ids=("I1",)),
        fit_weights={"recent_24m_work_count": 1.0},
        contract_path=ROOT / "config" / "school_decision.yml",
    )
    assert degenerate.rows[0]["fit_components_transformed"]["recent_24m_work_count"] == 0.5
    assert degenerate.rows[0]["user_defined_fit_score"] == 0.5

    with pytest.raises(ValueError, match="unknown stable school IDs"):
        compare_schools(
            index,
            profiles,
            topics,
            filters=SchoolComparisonFilters(stable_school_ids=("NOT-A-SCHOOL",)),
            contract_path=ROOT / "config" / "school_decision.yml",
        )
    with pytest.raises(ValueError, match="non-negative"):
        compare_schools(
            index,
            profiles,
            topics,
            filters=SchoolComparisonFilters(),
            fit_weights={"recent_24m_work_count": -1.0},
            contract_path=ROOT / "config" / "school_decision.yml",
        )
