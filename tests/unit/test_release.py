"""Tests for checksum-complete public release manifests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gisnet.release import build_release_manifest, verify_release_manifest


def _release_root(tmp_path: Path) -> Path:
    (tmp_path / "public").mkdir()
    (tmp_path / "dashboard/data").mkdir(parents=True)
    (tmp_path / "public/result.txt").write_text("public result\n", encoding="utf-8")
    (tmp_path / "dashboard/data/metadata.json").write_text(
        json.dumps({"tables": {"result": {"row_count": 1}}}), encoding="utf-8"
    )
    return tmp_path


def test_build_and_verify_release_manifest(tmp_path: Path) -> None:
    root = _release_root(tmp_path)
    payload = build_release_manifest(root, public_roots=("public", "dashboard/data"))
    assert payload["public_file_count"] == 2
    assert verify_release_manifest(root)["verified_file_count"] == 2


def test_verify_detects_changed_public_file(tmp_path: Path) -> None:
    root = _release_root(tmp_path)
    build_release_manifest(root, public_roots=("public", "dashboard/data"))
    (root / "public/result.txt").write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match=r"size changed|checksum changed"):
        verify_release_manifest(root)


def test_build_rejects_private_material(tmp_path: Path) -> None:
    root = _release_root(tmp_path)
    (root / "public/secret.txt").write_text("/home/alice/private/data", encoding="utf-8")
    with pytest.raises(ValueError, match="privacy scan failed"):
        build_release_manifest(root, public_roots=("public", "dashboard/data"))
