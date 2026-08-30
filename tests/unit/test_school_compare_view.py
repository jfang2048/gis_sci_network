import pandas as pd

from gisnet.visualization.school_compare import (
    align_school_profiles,
    comparison_activity_horizons,
    comparison_topic_view,
)


def _school_index() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "school_id": school_id,
                "display_name": name,
                "country_name": country,
                "macro_region": region,
                "subregion": subregion,
                "institution_category": "education",
            }
            for school_id, name, country, region, subregion in (
                ("I1", "Alpha", "France", "Europe", "Western Europe"),
                ("I2", "Beta", "Japan", "Asia", "Eastern Asia"),
                ("I3", "Gamma", "Brazil", "Americas", "South America"),
            )
        ]
    )


def test_profile_alignment_preserves_selection_order_exact_values_and_missingness() -> None:
    profiles = pd.DataFrame(
        [
            {
                "school_id": "I2",
                "full_work_count": 5,
                "recent_12m_work_count": None,
                "recent_24m_work_count": 5,
                "recent_36m_work_count": 9,
            },
            {
                "school_id": "I1",
                "full_work_count": 12,
                "recent_12m_work_count": 4,
                "recent_24m_work_count": 12,
                "recent_36m_work_count": 18,
            },
        ]
    )

    comparison = align_school_profiles(profiles, _school_index(), school_ids=["I1", "I2", "I3"])
    horizons = comparison_activity_horizons(comparison)

    assert comparison["school_id"].tolist() == ["I1", "I2", "I3"]
    assert comparison["full_work_count"].tolist()[:2] == [12.0, 5.0]
    assert comparison["profile_row_status"].tolist() == [
        "available",
        "available",
        "missing_source_profile",
    ]
    missing = horizons.loc[
        (horizons["school_id"] == "I2") & (horizons["window_months"] == 12),
        "work_count",
    ].iloc[0]
    absent = horizons.loc[horizons["school_id"] == "I3", "work_count"]
    assert pd.isna(missing)
    assert absent.isna().all()


def test_topic_view_keeps_observed_rows_only_and_uses_common_topic_order() -> None:
    comparison = align_school_profiles(
        pd.DataFrame([{"school_id": "I1"}, {"school_id": "I2"}]),
        _school_index(),
        school_ids=["I1", "I2"],
    )
    topics = pd.DataFrame(
        [
            {"school_id": "I1", "topic_family": "core_gis", "topic_family_share": 0.8},
            {"school_id": "I1", "topic_family": "cartography", "topic_family_share": 0.2},
            {"school_id": "I2", "topic_family": "remote_sensing", "topic_family_share": 1.0},
        ]
    )

    view = comparison_topic_view(topics, comparison, top_n=2)

    assert set(view["topic_family"]) == {"core_gis", "remote_sensing"}
    assert len(view) == 2
    assert not ((view["school_id"] == "I2") & (view["topic_family"] == "core_gis")).any()
    assert view["topic_family_share"].tolist() == [1.0, 0.8]
