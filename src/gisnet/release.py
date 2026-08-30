"""Build and verify the checksum-complete public release manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, TypedDict

from gisnet.atomic import atomic_write_json, atomic_write_text
from gisnet.state import RunLock, make_run_id, utc_timestamp

PUBLIC_ROOTS = (
    ".agent/manifests",
    "config",
    "dashboard/data",
    "data/reference",
    "figures",
    "outputs/reports",
)
FORBIDDEN_PATTERNS = {
    "GitHub token": re.compile(rb"(?:github_pat_|gh[pousr]_)[A-Za-z0-9_]{20,}"),
    "API-style secret": re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    "private key": re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "private home path": re.compile(rb"/home/[A-Za-z0-9._-]+/"),
}


class ReleaseFile(TypedDict):
    """One checksummed public release file."""

    path: str
    size_bytes: int
    sha256: str


def build_release_manifest(
    root: str | Path,
    *,
    output_path: str | Path = "release/manifest.json",
    checksum_path: str | Path = "release/manifest.json.sha256",
    public_roots: Sequence[str] = PUBLIC_ROOTS,
) -> dict[str, Any]:
    """Hash all public release outputs and write the manifest atomically."""
    project = Path(root).resolve()
    files = _public_files(project, public_roots)
    if not files:
        raise ValueError("release contains no public files")
    findings = _privacy_findings(project, files)
    if findings:
        raise ValueError(f"release privacy scan failed: {findings}")
    entries: list[ReleaseFile] = [
        {
            "path": path.as_posix(),
            "size_bytes": (project / path).stat().st_size,
            "sha256": _sha256(project / path),
        }
        for path in files
    ]
    dashboard_metadata = json.loads(
        (project / "dashboard/data/metadata.json").read_text(encoding="utf-8")
    )
    categories: dict[str, dict[str, int]] = {}
    for configured_root in public_roots:
        category_files = [entry for entry in entries if _is_below(entry["path"], configured_root)]
        categories[configured_root] = {
            "file_count": len(category_files),
            "size_bytes": sum(int(entry["size_bytes"]) for entry in category_files),
        }
    payload: dict[str, Any] = {
        "schema_version": 1,
        "release_version": "0.1.0",
        "generated_at_utc": utc_timestamp(),
        "hash_algorithm": "sha256",
        "public_file_count": len(entries),
        "public_size_bytes": sum(int(entry["size_bytes"]) for entry in entries),
        "dashboard_table_count": len(dashboard_metadata["tables"]),
        "raw_api_responses_included": False,
        "privacy_scan_finding_count": 0,
        "categories": categories,
        "external_large_data": [
            {
                "name": "OpenAlex data",
                "url": "https://docs.openalex.org/download-all-data",
            },
            {
                "name": "Research Organization Registry data dump",
                "url": "https://ror.readme.io/docs/data-dump",
            },
            {
                "name": "United Nations M49 geography standard",
                "url": "https://unstats.un.org/unsd/methodology/m49/",
            },
        ],
        "files": entries,
    }
    destination = project / output_path
    checksum_destination = project / checksum_path
    atomic_write_json(destination, payload)
    atomic_write_text(
        checksum_destination,
        f"{_sha256(destination)}  {destination.name}\n",
    )
    return payload


def verify_release_manifest(
    root: str | Path,
    *,
    manifest_path: str | Path = "release/manifest.json",
    checksum_path: str | Path = "release/manifest.json.sha256",
) -> dict[str, int]:
    """Verify the manifest checksum and every listed public file."""
    project = Path(root).resolve()
    manifest_file = project / manifest_path
    checksum_file = project / checksum_path
    payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    expected_manifest_hash = checksum_file.read_text(encoding="utf-8").split()[0]
    if _sha256(manifest_file) != expected_manifest_hash:
        raise ValueError("release manifest checksum does not match")
    entries = payload.get("files")
    if not isinstance(entries, list) or not entries:
        raise ValueError("release manifest has no files")
    seen: set[str] = set()
    for entry in entries:
        relative = str(entry["path"])
        if relative in seen or relative.startswith("/") or ".." in Path(relative).parts:
            raise ValueError(f"unsafe or duplicate release path: {relative}")
        seen.add(relative)
        path = project / relative
        if not path.is_file():
            raise ValueError(f"release file is missing: {relative}")
        if path.stat().st_size != int(entry["size_bytes"]):
            raise ValueError(f"release file size changed: {relative}")
        if _sha256(path) != entry["sha256"]:
            raise ValueError(f"release file checksum changed: {relative}")
    findings = _privacy_findings(project, [Path(value) for value in seen])
    if findings:
        raise ValueError(f"release privacy scan failed: {findings}")
    return {
        "verified_file_count": len(entries),
        "verified_size_bytes": sum(int(entry["size_bytes"]) for entry in entries),
        "privacy_scan_finding_count": 0,
    }


def scan_public_release_files(
    root: str | Path,
    *,
    public_roots: Sequence[str] = PUBLIC_ROOTS,
    excluded_paths: Sequence[str] = (),
) -> dict[str, int]:
    """Scan every current public file, including files not yet listed in a release manifest."""
    project = Path(root).resolve()
    excluded = {Path(value).as_posix() for value in excluded_paths}
    files = [
        path for path in _public_files(project, public_roots) if path.as_posix() not in excluded
    ]
    findings = _privacy_findings(project, files)
    if findings:
        raise ValueError(f"release privacy scan failed: {findings}")
    return {
        "scanned_file_count": len(files),
        "scanned_size_bytes": sum((project / path).stat().st_size for path in files),
        "privacy_scan_finding_count": 0,
    }


def _public_files(project: Path, public_roots: Sequence[str]) -> list[Path]:
    files: set[Path] = set()
    for relative_root in public_roots:
        directory = project / relative_root
        if not directory.is_dir():
            raise ValueError(f"public release directory is missing: {relative_root}")
        for path in directory.rglob("*"):
            if path.is_file() and path.name != ".gitkeep" and ".tmp" not in path.suffixes:
                files.add(path.relative_to(project))
    return sorted(files, key=lambda path: path.as_posix())


def _privacy_findings(project: Path, files: Sequence[Path]) -> list[str]:
    findings: list[str] = []
    for relative in files:
        data = (project / relative).read_bytes()
        for label, pattern in FORBIDDEN_PATTERNS.items():
            if pattern.search(data):
                findings.append(f"{label}: {relative.as_posix()}")
    return findings


def _is_below(path: str, root: str) -> bool:
    return path == root or path.startswith(f"{root.rstrip('/')}/")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("build", "verify"))
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--run-id")
    args = parser.parse_args(argv)
    if args.action == "build":
        run_id = args.run_id or make_run_id()
        with RunLock(args.root / ".agent/locks/run.lock", run_id=run_id, task_id="GISNET-104"):
            result = build_release_manifest(args.root)
        print(
            f"Built release manifest for {result['public_file_count']} files "
            f"({result['public_size_bytes']} bytes)."
        )
    else:
        result = verify_release_manifest(args.root)
        print(
            f"Verified {result['verified_file_count']} release files "
            f"({result['verified_size_bytes']} bytes); privacy findings: 0."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
