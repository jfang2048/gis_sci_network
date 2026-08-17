from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import cast

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import gisnet.corpus.publication_dates as publication_dates_module
from gisnet.corpus.publication_dates import build_publication_date_qa
from gisnet.dataset import file_sha256


def _write_inputs(tmp_path: Path) -> dict[str, Path]:
    raw_dates = [
        ("W01", 2024, "2024-01-31"),
        ("W02", 2024, "2024-02-29"),
        ("W03", 2024, "2024-12-31"),
        ("W04", 2025, "2025-01-01"),
        ("W05", 2024, None),
        ("W06", 2024, ""),
        ("W07", 2024, "2024"),
        ("W08", 2024, "2024-02"),
        ("W09", 2024, "2024-02-30"),
        ("W10", 2023, "2024-01-31"),
        ("W11", 2009, "2009-12-31"),
        ("W12", 2025, "2025-12-31"),
        ("WDUP", 2024, "2024-01-31"),
    ]
    works = tmp_path / "works.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "work_id": work_id,
                    "publication_year": publication_year,
                    "publication_date": publication_date,
                }
                for work_id, publication_year, publication_date in raw_dates
            ]
        ),
        works,
    )

    corpus = tmp_path / "work_corpus.parquet"
    corpus_rows = []
    for work_id, publication_year, publication_date in raw_dates:
        is_primary = work_id != "WDUP"
        corpus_rows.append(
            {
                "work_id": work_id,
                "publication_year": publication_year,
                "publication_date": publication_date,
                "strict_primary": is_primary,
                "broad_primary": is_primary,
                "strict_all_versions_sensitivity": True,
                "broad_all_versions_sensitivity": True,
                "version_family_id": "doi:shared" if work_id in {"W01", "WDUP"} else work_id,
                "is_recommended_primary_representative": is_primary,
            }
        )
    pq.write_table(pa.Table.from_pylist(corpus_rows), corpus)

    work_institutions = tmp_path / "work_institutions.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "work_id": row["work_id"],
                    "publication_year": row["publication_year"],
                    "hierarchy_view": "organization",
                    "institution_id": "I1",
                    "display_name": "Example University",
                    "country_code": "IT",
                    "macro_region": "Europe",
                    "is_primary_research_scope": True,
                    "strict_primary": row["strict_primary"],
                    "broad_primary": row["broad_primary"],
                }
                for row in corpus_rows
            ]
        ),
        work_institutions,
    )

    work_topics = tmp_path / "work_topics.parquet"
    topic_rows = [
        {
            "work_id": work_id,
            "topic_id": f"T-{work_id}",
            "corpus_membership": "strict",
            "method_family": "GIScience",
        }
        for work_id, _, _ in raw_dates
    ]
    topic_rows.append(
        {
            "work_id": "W01",
            "topic_id": "T-W01-secondary",
            "corpus_membership": "strict",
            "method_family": "GIScience",
        }
    )
    pq.write_table(pa.Table.from_pylist(topic_rows), work_topics)

    versions = tmp_path / "work_version_diagnostics.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "work_id": work_id,
                    "version_family_id": (
                        "doi:shared" if work_id in {"W01", "WDUP"} else f"work:{work_id}"
                    ),
                    "exact_doi_member_count": 2 if work_id in {"W01", "WDUP"} else 1,
                    "ambiguous_possible_family": False,
                    "is_recommended_primary_representative": work_id != "WDUP",
                }
                for work_id, _, _ in raw_dates
            ]
        ),
        versions,
    )
    return {
        "works": works,
        "corpus": corpus,
        "work_institutions": work_institutions,
        "work_topics": work_topics,
        "versions": versions,
    }


def _output_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "work_dates_path": tmp_path / "work_publication_dates.parquet",
        "corpus_coverage_path": tmp_path / "publication_date_coverage_corpus.parquet",
        "year_coverage_path": tmp_path / "publication_date_coverage_year.parquet",
        "institution_coverage_path": tmp_path / "publication_date_coverage_institution.parquet",
        "topic_family_coverage_path": tmp_path / "publication_date_coverage_topic_family.parquet",
    }


def _build(tmp_path: Path) -> tuple[dict[str, object], dict[str, Path]]:
    inputs = _write_inputs(tmp_path)
    outputs = _output_paths(tmp_path)
    summary = build_publication_date_qa(
        inputs["works"],
        inputs["corpus"],
        inputs["work_institutions"],
        inputs["work_topics"],
        inputs["versions"],
        start_year=2010,
        end_year=2025,
        **outputs,
    )
    return summary, outputs


def test_publication_date_quality_never_fabricates_subannual_dates(tmp_path: Path) -> None:
    _, outputs = _build(tmp_path)
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """
            SELECT work_id, publication_date_raw, publication_date, publication_month,
                   publication_quarter, has_exact_publication_date,
                   subannual_date_eligible, date_quality_status
            FROM read_parquet(?)
            ORDER BY work_id
            """,
            [str(outputs["work_dates_path"])],
        ).fetchall()
    finally:
        connection.close()

    assert rows[:4] == [
        ("W01", "2024-01-31", date(2024, 1, 31), "2024-01", "2024-Q1", True, True, "exact_valid"),
        ("W02", "2024-02-29", date(2024, 2, 29), "2024-02", "2024-Q1", True, True, "exact_valid"),
        ("W03", "2024-12-31", date(2024, 12, 31), "2024-12", "2024-Q4", True, True, "exact_valid"),
        ("W04", "2025-01-01", date(2025, 1, 1), "2025-01", "2025-Q1", True, True, "exact_valid"),
    ]
    by_id = {row[0]: row for row in rows}
    assert by_id["W05"] == ("W05", None, None, None, None, False, False, "missing")
    for work_id in ("W06", "W07", "W08", "W09"):
        assert by_id[work_id][2:7] == (None, None, None, False, False)
        assert by_id[work_id][7] == "malformed"
    assert by_id["W10"] == (
        "W10",
        "2024-01-31",
        None,
        None,
        None,
        True,
        False,
        "year_conflict",
    )
    assert by_id["W11"] == (
        "W11",
        "2009-12-31",
        None,
        None,
        None,
        True,
        False,
        "outside_supported_range",
    )


def test_publication_date_coverage_reconciles_and_preserves_version_policy(
    tmp_path: Path,
) -> None:
    summary, outputs = _build(tmp_path)
    connection = duckdb.connect()
    try:
        corpus_rows = connection.execute(
            """
            SELECT corpus_view, annual_work_count, has_exact_publication_date_work_count,
                   subannual_date_eligible_work_count, annual_only_work_count,
                   exact_valid_work_count, missing_work_count, malformed_work_count,
                   year_conflict_work_count, outside_supported_range_work_count,
                   date_coverage_ratio, coverage_reconciliation_difference,
                   status_reconciliation_difference, reconciliation_passed
            FROM read_parquet(?)
            ORDER BY corpus_view
            """,
            [str(outputs["corpus_coverage_path"])],
        ).fetchall()
        institution_row = connection.execute(
            """
            SELECT annual_work_count, subannual_date_eligible_work_count,
                   annual_only_work_count, reconciliation_passed
            FROM read_parquet(?)
            WHERE corpus_view = 'strict' AND hierarchy_view = 'organization'
              AND institution_id = 'I1'
            """,
            [str(outputs["institution_coverage_path"])],
        ).fetchone()
        topic_row = connection.execute(
            """
            SELECT annual_work_count, subannual_date_eligible_work_count,
                   annual_only_work_count, reconciliation_passed
            FROM read_parquet(?)
            WHERE corpus_view = 'strict' AND method_family = 'GIScience'
            """,
            [str(outputs["topic_family_coverage_path"])],
        ).fetchone()
    finally:
        connection.close()

    assert corpus_rows == [
        ("broad", 12, 7, 5, 7, 5, 1, 4, 1, 1, 5 / 12, 0, 0, True),
        ("normalized_all", 13, 8, 6, 7, 6, 1, 4, 1, 1, 6 / 13, 0, 0, True),
        ("strict", 12, 7, 5, 7, 5, 1, 4, 1, 1, 5 / 12, 0, 0, True),
    ]
    assert institution_row == (12, 5, 7, True)
    assert topic_row == (12, 5, 7, True)
    version_policy = cast(dict[str, object], summary["version_family_policy"])
    assert version_policy["exact_doi_multi_member_family_count"] == 1
    assert version_policy["nonrepresentative_exact_doi_work_count"] == 1
    assert version_policy["strict_all_versions_minus_primary_work_count"] == 1
    assert version_policy["broad_all_versions_minus_primary_work_count"] == 1
    assert version_policy["strict_all_versions_minus_primary_subannual_eligible_work_count"] == 1
    assert version_policy["broad_all_versions_minus_primary_subannual_eligible_work_count"] == 1
    assert version_policy["strict_affected_publication_month_count"] == 1
    assert version_policy["broad_affected_publication_month_count"] == 1


def test_publication_date_outputs_are_deterministic(tmp_path: Path) -> None:
    _, outputs = _build(tmp_path)
    first_hashes = {name: file_sha256(path) for name, path in outputs.items()}
    inputs = {
        "works": tmp_path / "works.parquet",
        "corpus": tmp_path / "work_corpus.parquet",
        "work_institutions": tmp_path / "work_institutions.parquet",
        "work_topics": tmp_path / "work_topics.parquet",
        "versions": tmp_path / "work_version_diagnostics.parquet",
    }
    build_publication_date_qa(
        inputs["works"],
        inputs["corpus"],
        inputs["work_institutions"],
        inputs["work_topics"],
        inputs["versions"],
        start_year=2010,
        end_year=2025,
        **outputs,
    )
    assert {name: file_sha256(path) for name, path in outputs.items()} == first_hashes


def test_failed_group_promotion_restores_every_prior_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, outputs = _build(tmp_path)
    first_hashes = {name: file_sha256(path) for name, path in outputs.items()}
    works = tmp_path / "works.parquet"
    rows = pq.read_table(works).to_pylist()
    next(row for row in rows if row["work_id"] == "W05")["publication_date"] = "2024-03-01"
    pq.write_table(pa.Table.from_pylist(rows), works)
    failing_source = outputs["corpus_coverage_path"].with_suffix(".parquet.tmp")
    real_replace = publication_dates_module.os.replace

    def fail_second_promotion(source: str | Path, destination: str | Path) -> None:
        if Path(source) == failing_source:
            raise OSError("simulated publication-date generation promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(publication_dates_module.os, "replace", fail_second_promotion)
    with pytest.raises(OSError, match="simulated publication-date"):
        build_publication_date_qa(
            works,
            tmp_path / "work_corpus.parquet",
            tmp_path / "work_institutions.parquet",
            tmp_path / "work_topics.parquet",
            tmp_path / "work_version_diagnostics.parquet",
            start_year=2010,
            end_year=2025,
            **outputs,
        )
    assert {name: file_sha256(path) for name, path in outputs.items()} == first_hashes
    assert not list(tmp_path.glob("*.tmp"))
