from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.visualization.school_profile import (
    activity_horizon_view,
    profile_quality_messages,
    query_school_profile,
    query_school_topics,
    research_neighbor_view,
)


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_profile_queries_use_stable_id_corpus_and_exact_rolling_window(tmp_path: Path) -> None:
    profiles = tmp_path / "profiles.parquet"
    topics = tmp_path / "topics.parquet"
    _write(
        profiles,
        [
            {
                "school_id": "I1",
                "corpus_view": corpus,
                "hierarchy_view": "school",
                "window_start": "2024-01",
                "window_end": "2025-12",
                "window_months": months,
                "profile_support_status": "supported",
                "full_work_count": count,
                "recent_12m_work_count": 3,
                "recent_24m_work_count": 7,
                "recent_36m_work_count": 10,
                "date_coverage_ratio": 1.0,
                "coverage_ratio": 1.0,
                "is_complete_window": True,
                "quality_flags": [],
            }
            for corpus, months, count in (
                ("broad", 12, 3),
                ("broad", 24, 7),
                ("strict", 24, 2),
            )
        ],
    )
    _write(
        topics,
        [
            {
                "school_id": "I1",
                "corpus_view": "broad",
                "hierarchy_view": "school",
                "window_start": "2024-01",
                "window_end": "2025-12",
                "window_months": 24,
                "topic_family": family,
                "topic_family_share": share,
                "topic_rank": rank,
            }
            for rank, (family, share) in enumerate(
                (("core_gis", 0.7), ("remote_sensing", 0.3)), start=1
            )
        ],
    )

    profile = query_school_profile(
        profiles,
        school_id="I1",
        corpus_view="broad",
        window_months=24,
    )
    topic_view = query_school_topics(
        topics,
        school_id="I1",
        corpus_view="broad",
        window_months=24,
    )

    assert len(profile) == 1
    assert profile.iloc[0]["full_work_count"] == 7
    assert topic_view["topic_family"].tolist() == ["core_gis", "remote_sensing"]
    assert topic_view["topic_family_share"].sum() == 1.0


def test_profile_views_retain_exact_horizons_neighbors_and_quality_messages() -> None:
    profile = {
        "recent_12m_work_count": 3,
        "recent_24m_work_count": 7,
        "recent_36m_work_count": 10,
        "topic_similarity_top_neighbor_ids": ["I2", "I-missing"],
        "profile_support_status": "no_recent_activity",
        "date_coverage_ratio": 0.6,
        "coverage_ratio": 0.75,
        "is_complete_window": False,
        "quality_flags": ["identity_fragmentation_review"],
    }
    school_index = pa.Table.from_pylist(
        [
            {
                "school_id": "I2",
                "display_name": "Neighbour University",
                "country_name": "France",
                "macro_region": "Europe",
            }
        ]
    ).to_pandas()

    activity = activity_horizon_view(profile)
    neighbors = research_neighbor_view(profile, school_index)
    messages = profile_quality_messages(profile, low_date_coverage_threshold=0.8)

    assert activity.to_dict("records") == [
        {"window_months": 12, "work_count": 3, "window_label": "Rolling 12 months"},
        {"window_months": 24, "work_count": 7, "window_label": "Rolling 24 months"},
        {"window_months": 36, "work_count": 10, "window_label": "Rolling 36 months"},
    ]
    assert neighbors.to_dict("records") == [
        {
            "proximity_rank": 1,
            "school_id": "I2",
            "display_name": "Neighbour University",
            "country_name": "France",
            "macro_region": "Europe",
            "index_match_status": "matched_complete_school_index",
        },
        {
            "proximity_rank": 2,
            "school_id": "I-missing",
            "display_name": None,
            "country_name": None,
            "macro_region": None,
            "index_match_status": "stable_id_not_in_complete_school_index",
        },
    ]
    assert any("no recent activity" in message.lower() for message in messages)
    assert any("60.0%" in message and "low" in message.lower() for message in messages)
    assert any("75.0%" in message and "incomplete" in message.lower() for message in messages)
    assert any("identity_fragmentation_review" in message for message in messages)
