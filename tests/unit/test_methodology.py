from pathlib import Path

from gisnet.reporting.methodology import REQUIRED_HEADINGS, build_methodology_report


def test_methodology_contains_required_evidence_and_disclosures(tmp_path: Path) -> None:
    output = tmp_path / "methodology.md"
    summary = build_methodology_report(
        project_path="config/project.yml",
        topic_registry_path="config/topic_registry.yml",
        regions_path="config/regions.yml",
        output_path=output,
    )
    report = output.read_text(encoding="utf-8")

    assert summary["present_section_count"] == len(REQUIRED_HEADINGS) == 12
    assert summary["figure_count"] == 3
    assert summary["all_figures_from_processed_data"] is True
    assert summary["provisional_topic_decisions_disclosed"] is True
    assert summary["partial_year_policy_disclosed"] is True
    assert summary["composite_score_non_primary_disclosed"] is True
    assert summary["historical_mode_disclosed"] is True
    assert summary["school_decision_mode_disclosed"] is True
    assert summary["school_validation_passed"] is True
    assert summary["admissions_and_quality_limits_disclosed"] is True
    assert "No human review has occurred" in report
    assert "No partial 2026 data" in report
    assert "not a primary scientific metric" in report
    assert "fractional weight `1 / choose(k, 2) = 2 / (k * (k - 1))`" in report
    assert "divided by **all included institutional Works**" in report
    assert "passed all 13 of 13 acceptance checks" in report
    assert "validate-school-contract --resume" in report
    assert "/home/" not in report
    assert "OPENALEX_API_KEY=" not in report
