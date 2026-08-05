from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.corpus.versions import build_version_diagnostics


def test_exact_doi_selects_published_representative_and_title_match_stays_ambiguous(
    tmp_path: Path,
) -> None:
    works = tmp_path / "works.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "work_id": "W1",
                    "doi": "https://doi.org/10.1/ABC",
                    "title": "A sufficiently long shared research title",
                    "publication_year": 2020,
                    "publication_date": "2020-01-01",
                    "work_type": "preprint",
                    "is_retracted": False,
                    "is_paratext": False,
                    "cited_by_count": 50,
                    "updated_date": "2025-01-01",
                },
                {
                    "work_id": "W2",
                    "doi": "10.1/abc",
                    "title": "A sufficiently long shared research title",
                    "publication_year": 2021,
                    "publication_date": "2021-01-01",
                    "work_type": "article",
                    "is_retracted": False,
                    "is_paratext": False,
                    "cited_by_count": 10,
                    "updated_date": "2024-01-01",
                },
                {
                    "work_id": "W3",
                    "doi": "10.2/pre",
                    "title": "Another sufficiently long candidate title",
                    "publication_year": 2020,
                    "publication_date": "2020-01-01",
                    "work_type": "preprint",
                    "is_retracted": False,
                    "is_paratext": False,
                    "cited_by_count": 1,
                    "updated_date": "2024-01-01",
                },
                {
                    "work_id": "W4",
                    "doi": "10.2/published",
                    "title": "Another sufficiently long candidate title",
                    "publication_year": 2021,
                    "publication_date": "2021-01-01",
                    "work_type": "article",
                    "is_retracted": False,
                    "is_paratext": False,
                    "cited_by_count": 2,
                    "updated_date": "2024-01-01",
                },
            ]
        ),
        works,
    )
    diagnostics = tmp_path / "diagnostics.parquet"
    duplicates = tmp_path / "duplicates.parquet"
    ambiguous = tmp_path / "ambiguous.parquet"
    summary = build_version_diagnostics(
        works,
        diagnostics_path=diagnostics,
        duplicate_doi_path=duplicates,
        ambiguous_path=ambiguous,
    )
    assert summary["work_count"] == 4
    assert summary["exact_doi_family_count"] == 1
    assert summary["ambiguous_possible_family_count"] == 1
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """
            SELECT work_id, is_recommended_primary_representative,
                   primary_representative_work_id, ambiguous_possible_family
            FROM read_parquet(?) ORDER BY work_id
            """,
            [str(diagnostics)],
        ).fetchall()
    finally:
        connection.close()
    assert rows == [
        ("W1", False, "W2", False),
        ("W2", True, "W2", False),
        ("W3", True, "W3", True),
        ("W4", True, "W4", True),
    ]
