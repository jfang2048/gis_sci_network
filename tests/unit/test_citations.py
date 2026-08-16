from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.dataset import file_sha256
from gisnet.network.citations import build_citation_flows


def _institution(
    work_id: str,
    institution_id: str,
    *,
    in_scope: bool = True,
) -> dict[str, object]:
    return {
        "work_id": work_id,
        "hierarchy_view": "organization",
        "institution_id": institution_id,
        "display_name": f"Institution {institution_id}",
        "macro_region": "Europe" if institution_id.startswith("S") else "Asia",
        "subregion": "Western Europe" if institution_id.startswith("S") else "Eastern Asia",
        "country_code": "FR" if institution_id.startswith("S") else "JP",
        "normalized_category": "university",
        "is_primary_network_scope": in_scope,
    }


def test_directed_citation_flows_preserve_direction_weighting_and_coverage(
    tmp_path: Path,
) -> None:
    works = tmp_path / "works.parquet"
    corpus = tmp_path / "corpus.parquet"
    institutions = tmp_path / "work-institutions.parquet"
    edges = tmp_path / "citation-edges.parquet"
    coverage = tmp_path / "citation-coverage.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "work_id": "C1",
                    "publication_year": 2021,
                    "referenced_work_ids": ["D1", "D2", "D3", "X1"],
                },
                {"work_id": "C2", "publication_year": 2021, "referenced_work_ids": ["D1"]},
                {"work_id": "D1", "publication_year": 2019, "referenced_work_ids": []},
                {"work_id": "D2", "publication_year": 2022, "referenced_work_ids": []},
                {"work_id": "D3", "publication_year": 2018, "referenced_work_ids": []},
            ]
        ),
        works,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "work_id": work_id,
                    "publication_year": year,
                    "strict_primary": work_id != "C2",
                    "broad_primary": True,
                }
                for work_id, year in [
                    ("C1", 2021),
                    ("C2", 2021),
                    ("D1", 2019),
                    ("D2", 2022),
                    ("D3", 2018),
                ]
            ]
        ),
        corpus,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                _institution("C1", "S1"),
                _institution("C1", "S2"),
                _institution("C2", "S3"),
                _institution("D1", "S1"),
                _institution("D1", "T2"),
                _institution("D2", "T3"),
                _institution("D3", "T4", in_scope=False),
            ]
        ),
        institutions,
    )

    summary = build_citation_flows(
        works,
        corpus,
        institutions,
        edges_year_path=edges,
        coverage_year_path=coverage,
        corpus_views=["strict", "broad"],
        hierarchy_views=["organization"],
        memory_limit="256MB",
    )

    connection = duckdb.connect()
    try:
        totals = connection.execute(
            """
            SELECT corpus_view, sum(full_count), sum(fractional_count),
                   count(*) FILTER (WHERE source_id = target_id),
                   sum(negative_lag_full_count)
            FROM read_parquet(?)
            GROUP BY corpus_view ORDER BY corpus_view
            """,
            [str(edges)],
        ).fetchall()
        coverage_rows = connection.execute(
            """
            SELECT corpus_view, reference_count, internal_corpus_reference_count,
                   institution_resolved_reference_count,
                   external_or_out_of_corpus_reference_count,
                   internal_without_scoped_institution_count,
                   negative_lag_reference_count,
                   institution_resolved_share
            FROM read_parquet(?) WHERE year = 2021 ORDER BY corpus_view
            """,
            [str(coverage)],
        ).fetchall()
        directed_edge = connection.execute(
            """
            SELECT source_id, target_id
            FROM read_parquet(?)
            WHERE corpus_view = 'strict' AND source_id = 'S2' AND target_id = 'S1'
            """,
            [str(edges)],
        ).fetchone()
    finally:
        connection.close()

    assert totals == [("broad", 8, 3.0, 1, 2), ("strict", 6, 2.0, 1, 2)]
    assert coverage_rows == [
        ("broad", 5, 4, 3, 1, 1, 1, 0.6),
        ("strict", 4, 3, 2, 1, 1, 1, 0.5),
    ]
    assert directed_edge == ("S2", "S1")
    assert summary["layer_semantics"] == "directed corpus-internal citation flow, not collaboration"
    assert summary["self_flows_preserved"] is True
    assert summary["maximum_fractional_reconciliation_error"] < 1e-12

    first_hashes = (file_sha256(edges), file_sha256(coverage))
    build_citation_flows(
        works,
        corpus,
        institutions,
        edges_year_path=edges,
        coverage_year_path=coverage,
        corpus_views=["strict", "broad"],
        hierarchy_views=["organization"],
        memory_limit="256MB",
    )
    assert first_hashes == (file_sha256(edges), file_sha256(coverage))
