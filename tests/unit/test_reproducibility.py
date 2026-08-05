import json
from pathlib import Path

import pytest

from gisnet.dataset import file_sha256
from gisnet.validation.reproducibility import verify_reproducibility


def test_manifest_checksum_matches_and_temp_output_blocks_success(tmp_path: Path) -> None:
    processed = tmp_path / "processed"
    manifests = tmp_path / "manifests"
    processed.mkdir()
    manifests.mkdir()
    dataset = processed / "sample.parquet"
    dataset.write_bytes(b"deterministic")
    (manifests / "sample.json").write_text(
        json.dumps(
            {
                "dataset_name": "sample",
                "checksum_sha256": file_sha256(dataset),
                "run_id": "repeat-2",
                "status": "valid",
                "row_count": 1,
            }
        ),
        encoding="utf-8",
    )
    payload = verify_reproducibility(
        {"sample": str(dataset)},
        manifest_directory=manifests,
        processed_directory=processed,
    )
    assert payload["status"] == "passed"
    assert payload["dataset_checks"][0]["checksum_matches"] is True
    (processed / "incomplete.parquet.tmp").write_bytes(b"partial")
    with pytest.raises(ValueError, match="temporary outputs remain"):
        verify_reproducibility(
            {"sample": str(dataset)},
            manifest_directory=manifests,
            processed_directory=processed,
        )


def test_checksum_mismatch_is_surfaced(tmp_path: Path) -> None:
    processed = tmp_path / "processed"
    manifests = tmp_path / "manifests"
    processed.mkdir()
    manifests.mkdir()
    dataset = processed / "sample.parquet"
    dataset.write_bytes(b"changed")
    (manifests / "sample.json").write_text(
        json.dumps({"checksum_sha256": "0" * 64}), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="checksum mismatches"):
        verify_reproducibility(
            {"sample": str(dataset)},
            manifest_directory=manifests,
            processed_directory=processed,
        )
