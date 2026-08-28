from __future__ import annotations

import math

import pandas as pd
import pytest

from gisnet.visualization.geographic_flows import (
    FLOW_LINE_WIDTH_DEFINITIONS,
    FlowDisplayPolicy,
    GeographicFlowSelection,
    build_flow_map_figure,
    build_flow_matrix_figure,
    build_flow_view,
    calibrated_line_width,
    filter_readable_flows,
    flow_source_options,
    great_circle_arc_coordinates,
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
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "geographic_level": "macro_region",
                "source_geography": "Americas",
                "target_geography": "Asia",
                "full_count": 1,
                "fractional_count": 0.5,
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
    for year, asia, europe, americas in ((2024, 40, 25, 30), (2025, 60, 75, 50)):
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
                {
                    "year": year,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "geographic_level": "macro_region",
                    "geography": "Americas",
                    "full_work_count": americas,
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
                "macro_region": "Asia",
                "latitude": 35.0,
                "longitude": 105.0,
            },
            {
                **common,
                "geography": "Europe",
                "display_name": "Europe",
                "macro_region": "Europe",
                "latitude": 50.0,
                "longitude": 10.0,
            },
            {
                **common,
                "geography": "Americas",
                "display_name": "Americas",
                "macro_region": "Americas",
                "latitude": 20.0,
                "longitude": -90.0,
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
    assert view.loc["Asia", "partner_share"] == pytest.approx(10 / 13.5)
    assert view.loc["Europe", "partner_share"] == pytest.approx(3 / 13.5)
    assert view.loc["Americas", "partner_share"] == pytest.approx(0.5 / 13.5)
    assert view.loc["Asia", "normalized_intensity"] == pytest.approx(5 / 100)
    assert view.loc["Europe", "normalized_intensity"] == pytest.approx(3 / math.sqrt(100 * 100))
    assert view.loc["Americas", "normalized_intensity"] == pytest.approx(0.5 / math.sqrt(100 * 80))
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
    complete_view = build_flow_view(_flows(), _outputs(), _anchors(), selection)
    view = filter_readable_flows(complete_view, FlowDisplayPolicy(top_n=2))

    map_figure = build_flow_map_figure(view, selection)
    matrix_figure = build_flow_matrix_figure(view, selection)
    partner_traces = [trace for trace in map_figure.data if trace.meta == "flow-partner-markers"]
    map_values = {
        str(row[0]): float(row[1]) for trace in partner_traces for row in trace.customdata
    }
    matrix_values = {str(row[0][0]): float(row[0][1]) for row in matrix_figure.data[0].customdata}

    expected = dict(view[["target_geography", "selected_value"]].itertuples(index=False, name=None))
    assert map_values == expected
    assert matrix_values == expected
    arc_traces = [trace for trace in map_figure.data if trace.meta == "flow-arc"]
    assert len(arc_traces) == 2
    assert len({trace.line.color for trace in arc_traces}) == 2
    expected_widths = dict(
        view.loc[~view["is_internal"], ["target_geography", "calibrated_width_px"]].itertuples(
            index=False, name=None
        )
    )
    assert {
        str(trace.customdata[0][0]): float(trace.line.width) for trace in arc_traces
    } == expected_widths
    assert all(len(trace.lon) == 32 for trace in arc_traces)
    assert all(trace.mode == "markers+text" for trace in partner_traces)


def test_display_filters_are_deterministic_and_do_not_rescale_widths() -> None:
    selection = GeographicFlowSelection(
        geographic_level="macro_region",
        source_geography="Asia",
        start_year=2025,
        end_year=2025,
        corpus_view="broad",
        hierarchy_view="organization",
        counting_method="fractional",
        metric="volume",
    )
    complete = build_flow_view(_flows(), _outputs(), _anchors(), selection)
    top_one = filter_readable_flows(complete, FlowDisplayPolicy(top_n=1))
    top_two = filter_readable_flows(complete, FlowDisplayPolicy(top_n=2))

    assert list(top_one["target_geography"]) == ["Europe", "Asia"]
    assert list(top_two["target_geography"]) == ["Europe", "Americas", "Asia"]
    assert (
        top_one.set_index("target_geography").loc["Europe", "calibrated_width_px"]
        == top_two.set_index("target_geography").loc["Europe", "calibrated_width_px"]
    )

    thresholded = filter_readable_flows(
        complete,
        FlowDisplayPolicy(top_n=5, minimum_weight=1.0, minimum_partner_share=0.05),
    )
    assert list(thresholded["target_geography"]) == ["Europe", "Asia"]
    assert list(thresholded["display_rank"]) == [1, 0]


def test_width_calibration_and_great_circle_geometry_have_fixed_semantics() -> None:
    assert calibrated_line_width(0.0, "volume") == 0.8
    assert calibrated_line_width(9.0, "volume") == pytest.approx(3.05)
    assert calibrated_line_width(0.25, "partner_share") == pytest.approx(4.4)
    assert calibrated_line_width(0.25, "normalized_intensity") == pytest.approx(4.4)
    assert calibrated_line_width(10_000.0, "volume") == 8.0
    assert "log10" in FLOW_LINE_WIDTH_DEFINITIONS["volume"]

    longitudes, latitudes = great_circle_arc_coordinates(35.0, 105.0, 50.0, 10.0)
    assert len(longitudes) == len(latitudes) == 32
    assert (longitudes[0], latitudes[0]) == pytest.approx((105.0, 35.0))
    assert (longitudes[-1], latitudes[-1]) == pytest.approx((10.0, 50.0))
    assert max(latitudes) > 50.0

    with pytest.raises(ValueError, match="top_n"):
        FlowDisplayPolicy(top_n=0)
    with pytest.raises(ValueError, match="between 0 and 1"):
        FlowDisplayPolicy(minimum_partner_share=1.1)


def test_source_options_use_sourced_display_labels_and_observed_scope() -> None:
    assert flow_source_options(
        _flows(),
        _anchors(),
        geographic_level="macro_region",
        start_year=2025,
        end_year=2025,
        corpus_view="broad",
        hierarchy_view="organization",
    ) == [("Americas", "Americas"), ("Asia", "Asia"), ("Europe", "Europe")]


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
