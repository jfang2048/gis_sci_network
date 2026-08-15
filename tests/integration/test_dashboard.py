"""Runtime smoke tests for every public dashboard page."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


def _sidebar_controls(app: AppTest) -> dict[str, object]:
    controls = [
        *app.sidebar.selectbox,
        *app.sidebar.select_slider,
        *app.sidebar.radio,
    ]
    return {control.label: control for control in controls if control.label != "Page"}


@pytest.mark.integration
def test_public_dashboard_pages_and_global_filters() -> None:
    app_path = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"
    pages = (
        "Overview",
        "Region trends",
        "Geographic map",
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
        "Geographic map": {
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
        assert any("corpus boundary remain provisional" in warning.value for warning in app.warning)

    page_widget = next(widget for widget in app.sidebar.selectbox if widget.label == "Page")
    page_widget.set_value("Geographic map")
    app = app.run(timeout=30)
    assert not app.exception
    assert any(metric.label == "Coordinate coverage" for metric in app.metric)
    assert any(
        subheader.value == "Domestic collaboration share by country" for subheader in app.subheader
    )
    assert any("internal links count twice" in caption.value for caption in app.caption)
    country_widget = next(widget for widget in app.sidebar.selectbox if widget.label == "Country")
    country_widget.set_value("China")
    app = app.run(timeout=30)
    assert not app.exception
    assert any(subheader.value == "Partner composition for China" for subheader in app.subheader)

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
