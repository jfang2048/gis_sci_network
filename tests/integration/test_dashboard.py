"""Runtime and semantic smoke tests for every public dashboard page."""

import json
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest


def _sidebar_controls(app: AppTest) -> dict[str, object]:
    controls = [
        *app.sidebar.selectbox,
        *app.sidebar.select_slider,
        *app.sidebar.radio,
    ]
    return {control.label: control for control in controls if control.label != "Page"}


def _plot_specs(app: AppTest) -> list[dict[str, object]]:
    return [json.loads(element.proto.spec) for element in app.get("plotly_chart")]


@pytest.mark.integration
def test_public_dashboard_pages_and_global_filters() -> None:
    app_path = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"
    pages = (
        "Overview",
        "Region trends",
        "Geographic flows",
        "Institutional network",
        "Institution explorer",
        "Topic-family comparison",
        "Methods and limitations",
        "Data quality",
    )
    app = AppTest.from_file(app_path, default_timeout=30).run()
    assert not app.exception

    enabled_by_page = {
        "Overview": {
            "Year",
            "Corpus view",
            "Hierarchy view",
            "Counting method",
            "Macro-region pair",
        },
        "Region trends": {
            "Year",
            "Corpus view",
            "Hierarchy view",
            "Counting method",
            "Macro-region pair",
        },
        "Geographic flows": {
            "Corpus view",
            "Hierarchy view",
            "Counting method",
            "Macro-region pair",
            "Country",
            "Subregion",
            "Institution type",
            "Topic family",
            "Consortium policy",
        },
        "Institutional network": {
            "Year",
            "Corpus view",
            "Hierarchy view",
            "Counting method",
            "Macro-region pair",
            "Country",
            "Subregion",
            "Institution type",
            "Topic family",
            "Consortium policy",
        },
        "Institution explorer": {"Corpus view", "Hierarchy view"},
        "Topic-family comparison": {
            "Year",
            "Corpus view",
            "Hierarchy view",
            "Counting method",
            "Topic family",
        },
        "Methods and limitations": set(),
        "Data quality": {"Year", "Corpus view", "Hierarchy view"},
    }
    for page in pages:
        page_widget = next(widget for widget in app.sidebar.selectbox if widget.label == "Page")
        page_widget.set_value(page)
        app = app.run(timeout=30)
        assert not app.exception
        controls = _sidebar_controls(app)
        assert set(controls) == {
            "Year",
            "Corpus view",
            "Hierarchy view",
            "Counting method",
            "Macro-region pair",
            "Country",
            "Subregion",
            "Institution type",
            "Topic family",
            "Consortium policy",
        }
        assert {
            label for label, control in controls.items() if not control.disabled
        } == enabled_by_page[page]
        assert any(
            "Provisional corpus boundary" in warning.value and "human review" in warning.value
            for warning in app.warning
        )

    page_widget = next(widget for widget in app.sidebar.selectbox if widget.label == "Page")
    page_widget.set_value("Geographic flows")
    app = app.run(timeout=30)
    assert not app.exception
    assert any(metric.label == "Coordinate coverage" for metric in app.metric)
    assert any(subheader.value == "Exact displayed flows" for subheader in app.subheader)
    assert any("same exact selected values" in caption.value for caption in app.caption)
    assert any(widget.label == "Geographic level" for widget in app.selectbox)
    assert any(widget.label == "Flow metric" for widget in app.selectbox)
    assert any(widget.label == "Source geography" for widget in app.selectbox)
    assert any(widget.label == "Complete-year window" for widget in app.select_slider)
    assert any(widget.label == "Top cross-geography flows" for widget in app.number_input)
    assert any(widget.label == "Minimum collaboration weight" for widget in app.number_input)
    assert any(widget.label == "Minimum partner share" for widget in app.slider)
    assert any("never rescaled" in caption.value for caption in app.caption)
    assert len(_plot_specs(app)) >= 2
    top_n_widget = next(
        widget for widget in app.number_input if widget.label == "Top cross-geography flows"
    )
    top_n_widget.set_value(1)
    minimum_share_widget = next(
        widget for widget in app.slider if widget.label == "Minimum partner share"
    )
    minimum_share_widget.set_value(5)
    app = app.run(timeout=30)
    assert not app.exception
    assert any("Displaying 1 of" in caption.value for caption in app.caption)
    country_widget = next(widget for widget in app.sidebar.selectbox if widget.label == "Country")
    country_widget.set_value("China")
    app = app.run(timeout=30)
    assert not app.exception
    assert any(metric.label == "Coordinate coverage" for metric in app.metric)

    page_widget = next(widget for widget in app.sidebar.selectbox if widget.label == "Page")
    page_widget.set_value("Region trends")
    app = app.run(timeout=30)
    assert not app.exception
    assert any(
        "default compares within-region proportions" in caption.value for caption in app.caption
    )

    page_widget = next(widget for widget in app.sidebar.selectbox if widget.label == "Page")
    page_widget.set_value("Institutional network")
    app = app.run(timeout=30)
    assert not app.exception
    assert any("edge width is constant" in caption.value for caption in app.caption)
    hierarchy_widget = next(
        widget for widget in app.sidebar.selectbox if widget.label == "Hierarchy view"
    )
    hierarchy_widget.set_value("umbrella")
    app = app.run(timeout=30)
    assert not app.exception
    assert any("zero active collapse rules" in warning.value for warning in app.warning)


@pytest.mark.integration
def test_visual_pages_keep_scientific_encodings_and_consistent_interaction() -> None:
    app_path = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"
    app = AppTest.from_file(app_path, default_timeout=30).run()
    overview = _plot_specs(app)
    assert len(overview) == 1
    assert overview[0]["layout"]["yaxis"]["range"] == [0, 1]
    assert overview[0]["layout"]["hovermode"] == "x unified"

    page_widget = next(widget for widget in app.sidebar.selectbox if widget.label == "Page")
    page_widget.set_value("Region trends")
    app = app.run(timeout=30)
    region_specs = _plot_specs(app)
    assert len(region_specs) == 2
    assert region_specs[0]["layout"]["hovermode"] == "x unified"
    assert any(trace["type"] == "heatmap" for trace in region_specs[1]["data"])

    page_widget = next(widget for widget in app.sidebar.selectbox if widget.label == "Page")
    page_widget.set_value("Institutional network")
    app = app.run(timeout=30)
    network_spec = _plot_specs(app)[0]
    assert network_spec["layout"]["xaxis"]["visible"] is False
    assert network_spec["layout"]["yaxis"]["scaleanchor"] == "x"
    assert any("visible core institutions" in info.value for info in app.info)


@pytest.mark.integration
def test_institution_explorer_renders_an_observed_pair() -> None:
    root = Path(__file__).resolve().parents[2]
    edges = pd.read_parquet(root / "dashboard/data/network_edges.parquet")
    row = edges.loc[
        (edges["corpus_view"] == "broad") & (edges["hierarchy_view"] == "organization")
    ].iloc[0]

    app = AppTest.from_file(root / "dashboard/app.py", default_timeout=30).run()
    page_widget = next(widget for widget in app.sidebar.selectbox if widget.label == "Page")
    page_widget.set_value("Institution explorer")
    app = app.run(timeout=30)

    institution_a = next(widget for widget in app.selectbox if widget.label == "Institution A")
    institution_b = next(widget for widget in app.selectbox if widget.label == "Institution B")
    institution_a.set_value(str(row["source_id"]))
    institution_b.set_value(str(row["target_id"]))
    app = app.run(timeout=30)

    assert not app.exception
    specs = _plot_specs(app)
    assert len(specs) == 1
    assert {trace["name"] for trace in specs[0]["data"]} == {
        "Full count",
        "Fractional count",
    }
    assert len(app.dataframe) >= 3
