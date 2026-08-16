from pathlib import Path
from xml.etree import ElementTree

from gisnet.visualization.matrix import _write_matrix_svg


def test_matrix_svg_distinguishes_missing_and_exact_values(tmp_path: Path) -> None:
    output = tmp_path / "matrix.svg"
    _write_matrix_svg(output, 2025, [("Asia", "Europe", 12.5), ("Asia", "Asia", 4.0)])
    value = output.read_text()
    assert ">12.5</text>" in value
    assert "missing" in value
    assert "exact values" in value
    assert "2025" in value


def test_matrix_svg_is_unclipped_accessible_and_has_scale_legend(tmp_path: Path) -> None:
    output = tmp_path / "matrix.svg"
    _write_matrix_svg(output, 2025, [("Asia", "Europe", 12.5), ("Asia", "Asia", 4.0)])
    value = output.read_text(encoding="utf-8")
    root = ElementTree.fromstring(value)
    width = int(root.attrib["width"])
    height = int(root.attrib["height"])
    assert width >= 760
    assert root.attrib["viewBox"] == f"0 0 {width} {height}"
    assert root.attrib["role"] == "img"
    assert "<title" in value
    assert "<desc" in value
    assert "Color scale" in value
    assert 'fill="#ffffff"' in value
