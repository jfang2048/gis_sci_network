"""Runtime smoke tests for every public dashboard page."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest


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

    for page in pages[1:]:
        page_widget = next(widget for widget in app.sidebar.selectbox if widget.label == "Page")
        page_widget.set_value(page)
        app = app.run(timeout=30)
        assert not app.exception

    labels = {widget.label for widget in app.sidebar.selectbox}
    labels.update(widget.label for widget in app.sidebar.select_slider)
    labels.update(widget.label for widget in app.sidebar.radio)
    assert {
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
    } <= labels
