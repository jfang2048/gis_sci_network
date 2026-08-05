"""Tests for stable-ID institution-pair explorer behavior."""

from __future__ import annotations

import pandas as pd
import pytest

from gisnet.visualization.pair_explorer import (
    build_pair_timeline,
    identity_rows,
    institution_labels,
)


def _edges() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "year": 2021,
                "source_id": "I1",
                "target_id": "I2",
                "source_name": "GIS Institute",
                "target_name": "GIS Institute",
                "full_count": 2,
                "fractional_count": 0.75,
                "normalized_intensity": 0.4,
                "persistence_3y": 1 / 3,
                "persistence_5y": 0.2,
                "topic_families": ["core_gis"],
                "work_ids_sample": ["W1"],
            }
        ]
    )


def test_labels_preserve_similar_names_with_distinct_stable_ids() -> None:
    labels = institution_labels(_edges())
    assert labels == {"I1": "GIS Institute [I1]", "I2": "GIS Institute [I2]"}


def test_pair_timeline_fills_counts_but_not_undefined_metrics() -> None:
    timeline = build_pair_timeline(_edges(), "I2", "I1", years=[2020, 2021, 2022])
    missing = timeline.loc[timeline["year"] == 2020].iloc[0]
    observed = timeline.loc[timeline["year"] == 2021].iloc[0]
    assert missing["full_count"] == 0
    assert missing["fractional_count"] == 0.0
    assert pd.isna(missing["normalized_intensity"])
    assert pd.isna(missing["persistence_3y"])
    assert missing["topic_families"] == []
    assert observed["work_ids_sample"] == ["W1"]


def test_pair_timeline_rejects_same_stable_id() -> None:
    with pytest.raises(ValueError, match="different institution IDs"):
        build_pair_timeline(_edges(), "I1", "I1", years=[2021])


def test_identity_rows_show_organization_and_umbrella_views() -> None:
    identities = pd.DataFrame(
        [
            {
                "organization_id": "I1",
                "organization_name": "Lab One",
                "umbrella_id": "U1",
                "umbrella_name": "University One",
                "is_collapsed": True,
            },
            {
                "organization_id": "I2",
                "organization_name": "Lab Two",
                "umbrella_id": "U1",
                "umbrella_name": "University One",
                "is_collapsed": True,
            },
        ]
    )
    assert identity_rows(identities, "I1", hierarchy_view="organization")[
        "umbrella_id"
    ].tolist() == ["U1"]
    assert identity_rows(identities, "U1", hierarchy_view="umbrella")[
        "organization_id"
    ].tolist() == ["I1", "I2"]
