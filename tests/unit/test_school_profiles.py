from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.schools.profiles import build_school_profiles


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_school_profiles_keep_evidence_layers_separate_and_cover_inactive_schools(
    tmp_path: Path,
) -> None:
    schools = tmp_path / "schools.parquet"
    identities = tmp_path / "identities.parquet"
    rolling = tmp_path / "rolling.parquet"
    partners = tmp_path / "partners.parquet"
    nodes = tmp_path / "nodes.parquet"
    citations = tmp_path / "citations.parquet"
    vectors = tmp_path / "vectors.parquet"
    similarities = tmp_path / "similarities.parquet"
    memberships = tmp_path / "memberships.parquet"
    dates = tmp_path / "dates.parquet"
    work_topics = tmp_path / "work_topics.parquet"
    profile_output = tmp_path / "profiles.parquet"
    topic_output = tmp_path / "profile_topics.parquet"
    school_rows = [
        {
            "canonical_school_id": school_id,
            "display_name": name,
            "country_code": country,
            "country_name": country,
            "macro_region": region,
            "subregion": subregion,
            "institution_category": "education",
            "recent_window_end": "2025-02",
            "date_coverage_ratio": 1.0,
            "identity_status": "retained_source_organization",
            "identity_resolution_confidence": "source_identity",
            "identity_quality_flags": [],
        }
        for school_id, name, country, region, subregion in [
            ("I1", "Alpha", "NL", "Europe", "Western Europe"),
            ("I2", "Beta", "US", "Americas", "Northern America"),
        ]
    ]
    _write(schools, school_rows)
    _write(
        identities,
        [
            {
                "institution_id": row["canonical_school_id"],
                "canonical_school_id": row["canonical_school_id"],
                "is_collapsed": False,
            }
            for row in school_rows
        ],
    )
    _write(
        rolling,
        [
            {
                "window_start": "2024-03",
                "window_end": "2025-02",
                "window_months": 12,
                "observed_month_count": 12,
                "eligible_month_count": 12,
                "coverage_ratio": 1.0,
                "is_complete_window": True,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "institution_id": "I1",
                "work_count": 1,
                "fractional_work_count": 0.5,
                "international_collaboration_share": 1.0,
                "cross_region_collaboration_share": 1.0,
                "partner_institution_count": 1,
                "partner_country_count": 1,
                "fractional_collaboration_strength": 0.5,
                "repeat_partner_count": 0,
                "repeat_partner_ratio": 0.0,
                "effective_partner_count": 1.0,
                "date_coverage_ratio": 1.0,
                "date_coverage_status": "exact",
            }
        ],
    )
    _write(
        partners,
        [
            {
                "window_end": "2025-02",
                "window_months": 12,
                "corpus_view": "broad",
                "school_id": "I1",
                "partner_id": "I2",
                "partner_name": "Beta",
                "fractional_count": 0.5,
                "partner_rank": 1,
            }
        ],
    )
    _write(
        nodes,
        [
            {
                "year": 2025,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "institution_id": "I1",
                "degree": 1,
                "pagerank": 0.2,
                "betweenness": 0.0,
                "betweenness_method": "exact",
                "bridge_score": 1.0,
                "community_id": "C1",
            }
        ],
    )
    _write(
        citations,
        [
            {
                "year": 2025,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "source_id": "I1",
                "target_id": "I2",
                "full_count": 2,
                "fractional_count": 0.5,
            }
        ],
    )
    _write(
        vectors,
        [
            {
                "year": 2025,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "institution_id": "I1",
                "topic_id": "T1",
                "is_similarity_core": True,
            }
        ],
    )
    _write(
        similarities,
        [
            {
                "year": 2025,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "source_id": "I1",
                "target_id": "I2",
                "cosine_similarity": 0.8,
            }
        ],
    )
    _write(
        memberships,
        [
            {
                "work_id": "W1",
                "publication_year": 2025,
                "hierarchy_view": "organization",
                "institution_id": "I1",
                "is_primary_research_scope": True,
                "strict_primary": False,
                "broad_primary": True,
            }
        ],
    )
    _write(
        dates,
        [
            {
                "work_id": "W1",
                "publication_year": 2025,
                "publication_month": "2025-01",
                "subannual_date_eligible": True,
            }
        ],
    )
    _write(
        work_topics,
        [
            {
                "work_id": "W1",
                "topic_id": "T1",
                "topic_score": 0.8,
                "corpus_membership": "broad_only",
                "method_family": "geospatial_computer_vision",
            }
        ],
    )

    summary = build_school_profiles(
        schools,
        identities,
        rolling,
        partners,
        nodes,
        citations,
        vectors,
        similarities,
        memberships,
        dates,
        work_topics,
        profiles_path=profile_output,
        topic_profiles_path=topic_output,
        corpus_views=("broad",),
        window_months=(12,),
    )

    profiles = {
        row["canonical_school_id"]: row for row in pq.read_table(profile_output).to_pylist()
    }
    assert profiles["I1"]["work_count"] == 1
    assert profiles["I1"]["full_work_count"] == 1
    assert profiles["I1"]["top_partner_ids"] == ["I2"]
    assert profiles["I1"]["citation_flow_out_fractional"] == 0.5
    assert profiles["I2"]["citation_flow_fractional_in_strength"] == 0.5
    assert profiles["I1"]["topic_similarity_maximum"] == 0.8
    assert profiles["I2"]["profile_support_status"] == "no_recent_activity"
    assert profiles["I2"]["annual_network_support_status"].startswith("not_observed")
    assert "quality_score" not in profiles["I1"]
    assert "user_defined_fit_score" not in profiles["I1"]
    assert profiles["I1"]["bridge_score"] == 1.0
    topic = pq.read_table(topic_output).to_pylist()[0]
    assert topic["topic_family"] == "geospatial_computer_vision"
    assert topic["topic_family_share"] == 1.0
    assert topic["topic_weight"] == 0.8
    assert summary["profile_row_count"] == 2
    assert summary["topic_profile_row_count"] == 1
