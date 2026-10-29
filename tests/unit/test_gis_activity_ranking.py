"""The published activity ordering must retain the complete stable-ID universe."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from gisnet.reporting.gis_activity_ranking import (
    COLUMNS,
    CSV,
    REPORT,
    SOURCE,
    build_ranking,
    render_markdown,
)

ROOT = Path(__file__).resolve().parents[2]


def _row(school_id: str, name: str, strict: int, broad: int) -> dict[str, object]:
    return {
        "school_id": school_id,
        "display_name": name,
        "institution_category": "education",
        "country_code": "IT",
        "country_name": "Italy",
        "macro_region": "Europe",
        "subregion": "Southern Europe",
        "strict_work_count": strict,
        "broad_work_count": broad,
        "openalex_id": school_id,
        "ror_id": "",
        "identity_status": "unchanged",
    }


def test_strict_count_is_the_only_strength_sort_key() -> None:
    index = pd.DataFrame(
        [
            _row("I3", "Zulu", 0, 20),
            _row("I2", "Beta", 2, 3),
            _row("I1", "Alpha | Institute", 2, 2),
        ]
    )
    ranked = build_ranking(index)

    assert ranked["school_id"].tolist() == ["I1", "I2", "I3"]
    assert ranked["position"].tolist() == [1, 2, 3]
    assert ranked["strict_work_count"].tolist() == [2, 2, 0]
    assert ranked["broad_work_count"].tolist() == [2, 3, 20]
    report = render_markdown(ranked, source_sha256="a" * 64)
    assert "Alpha \\| Institute" in report
    assert "Strict 为 0" in report


def test_invalid_source_cannot_produce_a_ranking() -> None:
    index = pd.DataFrame([_row("I1", "Alpha", 2, 2), _row("I2", "Beta", 1, 3)])

    with pytest.raises(ValueError, match="unique"):
        build_ranking(pd.concat([index, index.iloc[[0]]], ignore_index=True))
    with pytest.raises(ValueError, match="cannot exceed"):
        build_ranking(index.assign(strict_work_count=[4, 1]))
    with pytest.raises(ValueError, match="lacks ranking columns"):
        build_ranking(index.drop(columns=["ror_id"]))


def test_published_document_and_csv_match_complete_checked_in_index() -> None:
    index = pd.read_parquet(ROOT / SOURCE)
    ranking = build_ranking(index)
    csv_bytes = (ROOT / CSV).read_bytes()
    report = (ROOT / REPORT).read_text(encoding="utf-8")
    source_sha = hashlib.sha256((ROOT / SOURCE).read_bytes()).hexdigest()

    assert len(ranking) == len(index) == ranking["school_id"].nunique()
    assert set(ranking["school_id"]) == set(index["school_id"])
    assert list(ranking.columns) == COLUMNS
    assert csv_bytes == ranking.to_csv(index=False, lineterminator="\n").encode("utf-8")
    assert report == render_markdown(ranking, source_sha256=source_sha)
    lines = [line for line in report.splitlines() if line.startswith("| ")][2:]
    assert len(lines) == len(ranking)
    assert all(
        f"| {row.school_id} |" in line
        for row, line in zip(ranking.itertuples(), lines, strict=True)
    )
    for file, name in (
        (CSV, "gis_strict_activity_ranking_csv"),
        (REPORT, "gis_strict_activity_ranking_markdown"),
    ):
        manifest = json.loads((ROOT / ".agent/manifests" / f"{name}.json").read_text())
        assert manifest["row_count"] == len(index)
        assert manifest["checksum_sha256"] == hashlib.sha256((ROOT / file).read_bytes()).hexdigest()
        assert manifest["source_versions"]["school_index_sha256"] == source_sha
