from pathlib import Path

import pytest
from pydantic import ValidationError

from gisnet.config import ProjectConfig, config_file_hash
from gisnet.manifest import DatasetManifest


def test_equivalent_yaml_has_same_semantic_hash(tmp_path: Path) -> None:
    first = tmp_path / "first.yml"
    second = tmp_path / "second.yml"
    first.write_text("a: 1\nb:\n  c: true\n", encoding="utf-8")
    second.write_text("# formatting differs\nb: {c: true}\na: 1\n", encoding="utf-8")
    assert config_file_hash(first) == config_file_hash(second)


@pytest.mark.parametrize(
    "change",
    [
        {"analysis": {"start_year": 2025, "end_year": 2024}},
        {"analysis": {"start_year": 2010, "end_year": 2026}},
        {"corpus_views": ["strict", "strict"]},
        {"corpus_views": ["invalid"]},
    ],
)
def test_invalid_configuration_fails_early(change: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ProjectConfig.model_validate(change)


def test_dataset_manifest_requires_configuration_hash() -> None:
    values = {
        "dataset_name": "test",
        "created_at_utc": "2026-08-05T00:00:00Z",
        "run_id": "run",
        "git_commit": "nogit",
        "config_hashes": {},
        "row_count": 0,
        "column_count": 0,
        "primary_key": [],
        "null_counts": {},
        "checksum_sha256": "0" * 64,
        "command": "test",
    }
    with pytest.raises(ValidationError, match="configuration hash"):
        DatasetManifest.model_validate(values)
