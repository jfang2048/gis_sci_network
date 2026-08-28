from __future__ import annotations

import math

import pandas as pd
import pytest

from gisnet.visualization.geographic_flows import (
    GeographicFlowSelection,
    build_flow_map_figure,
    build_flow_matrix_figure,
    build_flow_view,
    flow_source_options,
)


def _flows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "year": 2024,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "geographic_level": "macro_region",
                "source_geography": "Asia",
                "target_geography": "Asia",
                "full_count": 4,
                "fractional_count": 2.0,
            },
            {
                "year": 2024,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "geographic_level": "macro_region",
                "source_geography": "Asia",
                "target_geography": "Europe",
                "full_count": 2,
                "fractional_count": 1.0,
            },
            {
                "year": 2025,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "geographic_level": "macro_region",
                "source_geography": "Asia",
                "target_geography": "Asia",
                "full_count": 6,
                "fractional_count": 3.0,
            },
            {
                "year": 2025,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "geographic_level": "macro_region",
                "source_geography": "Asia",
                "target_geography": "Europe",
                "full_count": 4,
                "fractional_count": 2.0,
            },
            {
                "year": 2025,
                "corpus_view": "strict",
                "hierarchy_view": "organization",
                "geographic_level": "macro_region",
                "source_geography": "Asia",
                "target_geography": "Europe",
                "full_count": 99,
                "fractional_count": 99.0,
            },
        ]
    )


def _outputs() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for year, asia, europe in ((2024, 40, 25), (2025, 60, 75)):
        rows.extend(
            [
                {
                    "year": year,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "geographic_level": "macro_region",
                    "geography": "Asia",
                    "full_work_count": asia,
                },
                {
                    "year": year,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "geographic_level": "macro_region",
                    "geography": "Europe",
                    "full_work_count": europe,
                },
            ]
        )
    return pd.DataFrame(rows)


def _anchors() -> pd.DataFrame:
    common = {
        "geographic_level": "macro_region",
        "anchor_method": "spherical mean of sourced institution coordinates",
        "coordinate_source": "openalex",
        "coordinate_license": "CC0 1.0 Universal",
        "coordinate_license_url": "https://creativecommons.org/publicdomain/zero/1.0/",
        "source_dataset_sha256": "abc123",
    }
    return pd.DataFrame(
        [
            {
                **common,
                "geography": "Asia",
                "display_name": "Asia",
                "latitude": 35.0,
                "longitude": 105.0,
            },
            {
                **common,
                "geography": "Europe",
                "display_name": "Europe",
                "latitude": 50.0,
                "longitude": 10.0,
            },
        ]
    )


def test_flow_view_reconciles_window_direction_and_metric_definitions() -> None:
    selection = GeographicFlowSelection(
        geographic_level="macro_region",
        source_geography="Asia",
        start_year=2024,
        end_year=2025,
        corpus_view="broad",
        hierarchy_view="organization",
        counting_method="fractional",
        metric="partner_share",
    )

    view = build_flow_view(_flows(), _outputs(), _anchors(), selection).set_index(
        "target_geography"
    )

    assert view.loc["Asia", "fractional_count"] == 5.0
    assert view.loc["Europe", "fractional_count"] == 3.0
    assert view.loc["Asia", "partner_share"] == pytest.approx(10 / 13)
    assert view.loc["Europe", "partner_share"] == pytest.approx(3 / 13)
    assert view.loc["Asia", "normalized_intensity"] == pytest.approx(5 / 100)
    assert view.loc["Europe", "normalized_intensity"] == pytest.approx(3 / math.sqrt(100 * 100))
    assert view["partner_share"].sum() == pytest.approx(1.0)
    assert set(view["window_label"]) == {"2024-2025"}


def test_map_and_matrix_modes_share_the_same_exact_selected_values() -> None:
    selection = GeographicFlowSelection(
        geographic_level="macro_region",
        source_geography="Asia",
        start_year=2025,
        end_year=2025,
        corpus_view="broad",
        hierarchy_view="organization",
        counting_method="fractional",
        metric="normalized_intensity",
    )
    view = build_flow_view(_flows(), _outputs(), _anchors(), selection)

    map_figure = build_flow_map_figure(view, selection)
    matrix_figure = build_flow_matrix_figure(view, selection)
    map_values = {str(row[0]): float(row[1]) for row in map_figure.data[-2].customdata}
    matrix_values = {str(row[0][0]): float(row[0][1]) for row in matrix_figure.data[0].customdata}

    expected = dict(view[["target_geography", "selected_value"]].itertuples(index=False, name=None))
    assert map_values == expected
    assert matrix_values == expected
    assert all(trace.line.width == 1.25 for trace in map_figure.data[:-2])


def test_source_options_use_sourced_display_labels_and_observed_scope() -> None:
    assert flow_source_options(
        _flows(),
        _anchors(),
        geographic_level="macro_region",
        start_year=2025,
        end_year=2025,
        corpus_view="broad",
        hierarchy_view="organization",
    ) == [("Asia", "Asia"), ("Europe", "Europe")]


def test_flow_view_refuses_missing_anchor_or_denominator() -> None:
    selection = GeographicFlowSelection(
        geographic_level="macro_region",
        source_geography="Asia",
        start_year=2025,
        end_year=2025,
        corpus_view="broad",
        hierarchy_view="organization",
        counting_method="full",
        metric="volume",
    )
    with pytest.raises(ValueError, match="denominators are missing"):
        build_flow_view(_flows(), _outputs().iloc[:1], _anchors(), selection)
    with pytest.raises(ValueError, match="anchors are missing"):
        build_flow_view(_flows(), _outputs(), _anchors().iloc[:1], selection)
