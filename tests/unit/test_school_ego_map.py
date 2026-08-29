from __future__ import annotations

from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gisnet.visualization.school_ego_map import (
    SchoolEgoSelection,
    build_school_ego_map_figure,
    build_school_ego_view,
    query_school_ego_partners,
)


def _partners() -> pd.DataFrame:
    common: dict[str, object] = {
        "time_basis": "rolling",
        "period_key": "rolling_24m",
        "period_label": "Rolling 24 months · 2024-01 to 2025-12",
        "corpus_view": "broad",
        "school_id": "I1",
        "school_name": "Source School",
        "school_country": "FR",
        "school_country_name": "France",
        "school_macro_region": "Europe",
        "school_subregion": "Western Europe",
        "school_latitude": 48.0,
        "school_longitude": 2.0,
        "school_coordinate_source": "openalex",
        "source_work_count": 20,
        "persistence_definition": "active months divided by 24",
    }
    return pd.DataFrame(
        [
            {
                **common,
                "partner_id": "I2",
                "partner_name": "Alpha Partner",
                "partner_country": "JP",
                "partner_country_name": "Japan",
                "partner_macro_region": "Asia",
                "partner_subregion": "Eastern Asia",
                "partner_latitude": 35.0,
                "partner_longitude": 139.0,
                "partner_coordinate_source": "openalex",
                "full_count": 5,
                "fractional_count": 3.0,
                "distinct_work_count": 5,
                "target_work_count": 30,
                "normalized_intensity": 0.15,
                "persistence": 0.5,
                "partner_rank": 1,
            },
            {
                **common,
                "partner_id": "I3",
                "partner_name": "Beta Partner",
                "partner_country": "JP",
                "partner_country_name": "Japan",
                "partner_macro_region": "Asia",
                "partner_subregion": "Eastern Asia",
                "partner_latitude": 36.0,
                "partner_longitude": 140.0,
                "partner_coordinate_source": "openalex",
                "full_count": 4,
                "fractional_count": 2.0,
                "distinct_work_count": 4,
                "target_work_count": 10,
                "normalized_intensity": 0.1,
                "persistence": 0.25,
                "partner_rank": 2,
            },
            {
                **common,
                "partner_id": "I4",
                "partner_name": "No-coordinate Partner",
                "partner_country": "US",
                "partner_country_name": "United States of America",
                "partner_macro_region": "Americas",
                "partner_subregion": "Northern America",
                "partner_latitude": None,
                "partner_longitude": None,
                "partner_coordinate_source": None,
                "full_count": 2,
                "fractional_count": 1.0,
                "distinct_work_count": 2,
                "target_work_count": 8,
                "normalized_intensity": 0.05,
                "persistence": 0.1,
                "partner_rank": 3,
            },
        ]
    )


def _anchors() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "geographic_level": "country",
                "geography": "JP",
                "latitude": 36.0,
                "longitude": 138.0,
                "coordinate_source": "openalex",
            },
            {
                "geographic_level": "country",
                "geography": "US",
                "latitude": 38.0,
                "longitude": -97.0,
                "coordinate_source": "openalex",
            },
            {
                "geographic_level": "macro_region",
                "geography": "Asia",
                "latitude": 35.0,
                "longitude": 105.0,
                "coordinate_source": "openalex",
            },
            {
                "geographic_level": "macro_region",
                "geography": "Americas",
                "latitude": 20.0,
                "longitude": -90.0,
                "coordinate_source": "openalex",
            },
        ]
    )


def test_institution_map_and_exact_mapped_table_share_values_and_stable_ids() -> None:
    selection = SchoolEgoSelection(
        school_id="I1",
        corpus_view="broad",
        period_key="rolling_24m",
        level="institution",
        metric="fractional_volume",
        top_n=3,
    )
    view = build_school_ego_view(_partners(), _anchors(), selection)
    mapped = view.loc[view["is_mappable"]].copy()
    figure = build_school_ego_map_figure(mapped, selection)

    assert list(view["target_id"]) == ["I2", "I3", "I4"]
    assert int(view["is_mappable"].sum()) == 2
    marker_values = {
        str(row[0]): float(row[1])
        for trace in figure.data
        if trace.meta == "school-ego-partner-markers"
        for row in trace.customdata
    }
    expected = dict(mapped[["target_id", "selected_value"]].itertuples(index=False, name=None))
    assert marker_values == expected
    assert all(
        "Stable IDs: I1" in text
        for trace in figure.data
        if trace.meta == "school-ego-partner-markers"
        for text in trace.hovertext
    )
    assert all(len(trace.lon) == 32 for trace in figure.data if trace.meta == "school-ego-arc")


def test_country_and_region_views_aggregate_only_retained_institution_partners() -> None:
    country_selection = SchoolEgoSelection(
        school_id="I1",
        corpus_view="broad",
        period_key="rolling_24m",
        level="country",
        metric="normalized_intensity",
        top_n=5,
    )
    country = build_school_ego_view(_partners(), _anchors(), country_selection).set_index(
        "target_id"
    )
    assert country.loc["JP", "fractional_count"] == 5.0
    assert country.loc["JP", "normalized_intensity"] == pytest.approx((3 * 0.15 + 2 * 0.1) / 5)
    assert country.loc["JP", "persistence"] == pytest.approx((3 * 0.5 + 2 * 0.25) / 5)
    assert country.loc["JP", "institution_partner_count"] == 2

    region_selection = SchoolEgoSelection(
        school_id="I1",
        corpus_view="broad",
        period_key="rolling_24m",
        level="macro_region",
        metric="persistence",
        top_n=1,
    )
    region = build_school_ego_view(_partners(), _anchors(), region_selection)
    assert list(region["target_id"]) == ["Asia"]
    assert region.iloc[0]["persistence"] == pytest.approx(0.4)


def test_width_for_the_same_partner_is_independent_of_top_n() -> None:
    base = dict(
        school_id="I1",
        corpus_view="broad",
        period_key="rolling_24m",
        level="institution",
        metric="normalized_intensity",
    )
    top_one = build_school_ego_view(
        _partners(), _anchors(), SchoolEgoSelection(**base, top_n=1)
    ).set_index("target_id")
    top_three = build_school_ego_view(
        _partners(), _anchors(), SchoolEgoSelection(**base, top_n=3)
    ).set_index("target_id")
    assert top_one.loc["I2", "calibrated_width_px"] == top_three.loc["I2", "calibrated_width_px"]


def test_partner_query_pushes_stable_id_corpus_and_period_predicates(tmp_path: Path) -> None:
    rows = _partners()
    other = rows.iloc[[0]].copy()
    other["school_id"] = "I9"
    other["school_name"] = "Other School"
    path = tmp_path / "school_ego_partners.parquet"
    pq.write_table(pa.Table.from_pandas(pd.concat([rows, other], ignore_index=True)), path)

    selected = query_school_ego_partners(
        path,
        school_id="I1",
        corpus_view="broad",
        period_key="rolling_24m",
    )

    assert len(selected) == 3
    assert set(selected["school_id"]) == {"I1"}
    assert list(selected["partner_rank"]) == [1, 2, 3]
