from pathlib import Path

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
