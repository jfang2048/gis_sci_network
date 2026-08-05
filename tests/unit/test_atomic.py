from pathlib import Path

import pytest

from gisnet.atomic import atomic_write_bytes


def test_failed_validation_preserves_previous_file(tmp_path: Path) -> None:
    destination = tmp_path / "dataset.bin"
    destination.write_bytes(b"known-good")

    def reject(_: Path) -> None:
        raise ValueError("simulated interruption before rename")

    with pytest.raises(ValueError, match="simulated interruption"):
        atomic_write_bytes(destination, b"partial", validate=reject)

    assert destination.read_bytes() == b"known-good"
    assert list(tmp_path.glob("*.tmp")) == []
