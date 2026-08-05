from pathlib import Path

from gisnet.visualization.matrix import _write_matrix_svg


def test_matrix_svg_distinguishes_missing_and_exact_values(tmp_path: Path) -> None:
    output = tmp_path / "matrix.svg"
    _write_matrix_svg(output, 2025, [("Asia", "Europe", 12.5), ("Asia", "Asia", 4.0)])
    value = output.read_text()
    assert "12" in value
    assert "missing" in value
    assert "exact values" in value
    assert "2025" in value
