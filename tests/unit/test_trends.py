from pathlib import Path
from xml.etree import ElementTree

from gisnet.visualization.trends import _write_svg


def test_svg_labels_units_views_and_complete_years(tmp_path: Path) -> None:
    path = tmp_path / "trend.svg"
    _write_svg(
        path,
        title="Annual collaboration",
        subtitle="Broad corpus · organization hierarchy · complete years",
        rows=[(2020, "Europe — Asia", 1.0), (2021, "Europe — Asia", 2.0)],
        y_label="Fractional collaboration weight",
    )
    value = path.read_text()
    assert "Broad corpus" in value
    assert "Fractional collaboration weight" in value
    assert "complete calendar years only" in value
    assert "Europe — Asia" in value


def test_published_trend_figure_contains_all_required_region_series() -> None:
    value = Path("figures/annual_region_trends.svg").read_text(encoding="utf-8")
    expected = {
        "Americas — Americas",
        "Americas — Asia",
        "Americas — Europe",
        "Asia — Asia",
        "Asia — Europe",
        "Europe — Europe",
    }
    assert expected == {series for series in expected if series in value}


def test_trend_svg_has_accessible_metadata_and_non_color_cues(tmp_path: Path) -> None:
    path = tmp_path / "trend.svg"
    _write_svg(
        path,
        title="Annual collaboration",
        subtitle="Broad corpus · organization hierarchy · complete years",
        rows=[
            (2020, "Europe — Asia", 1.0),
            (2021, "Europe — Asia", 2.0),
            (2020, "Asia — Asia", 3.0),
            (2021, "Asia — Asia", 4.0),
        ],
        y_label="Fractional collaboration weight",
    )
    value = path.read_text(encoding="utf-8")
    root = ElementTree.fromstring(value)
    assert root.attrib["viewBox"] == "0 0 1200 720"
    assert root.attrib["role"] == "img"
    assert "<title" in value
    assert "<desc" in value
    assert "stroke-dasharray" in value


def test_identical_geographic_series_do_not_claim_hierarchy_equivalence(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trend.svg"
    _write_svg(
        path,
        title="Annual collaboration",
        subtitle="Broad corpus · organization hierarchy · complete years",
        rows=[
            (2020, "Europe — Asia", 1.0),
            (2021, "Europe — Asia", 2.0),
            (2020, "Americas — Asia", 1.0),
            (2021, "Americas — Asia", 2.0),
        ],
        y_label="Fractional collaboration weight",
    )
    value = path.read_text(encoding="utf-8")
    assert "no active collapse" not in value


def test_hierarchy_comparison_can_disclose_identical_series(tmp_path: Path) -> None:
    path = tmp_path / "comparison.svg"
    _write_svg(
        path,
        title="Hierarchy comparison",
        subtitle="Complete years",
        rows=[
            (2020, "broad / organization", 1.0),
            (2021, "broad / organization", 2.0),
            (2020, "broad / umbrella", 1.0),
            (2021, "broad / umbrella", 2.0),
        ],
        y_label="Fractional collaboration weight",
        overlap_note="No active collapse changes these aggregates.",
    )
    assert "No active collapse" in path.read_text(encoding="utf-8")
