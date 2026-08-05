from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.network.edges import build_collaboration_edges


def test_three_institutions_make_three_full_and_thirds_fractional(tmp_path: Path) -> None:
    source = tmp_path / "work-institutions.parquet"
    rows = []
    for institution in ("I1", "I2", "I3"):
        rows.append(
            {
                "publication_year": 2020,
                "work_id": "W1",
                "hierarchy_view": "organization",
                "institution_id": institution,
                "display_name": institution,
                "macro_region": "Europe",
                "subregion": "Western Europe",
                "country_code": "DE",
                "normalized_category": "higher_education",
                "method_families": ["gis"],
                "strict_primary": True,
                "broad_primary": True,
                "is_primary_network_scope": True,
            }
        )
    rows.append({**rows[0]})  # defensive duplicate must not alter pair arithmetic
    pq.write_table(pa.Table.from_pylist(rows), source)
    work_edges = tmp_path / "work-edges.parquet"
    edges = tmp_path / "edges.parquet"
    diagnostics = tmp_path / "diag.parquet"
    summary = build_collaboration_edges(
        source,
        work_edges_path=work_edges,
        edges_year_path=edges,
        diagnostics_path=diagnostics,
        corpus_views=["strict"],
        hierarchy_views=["organization"],
        warning_institution_count=3,
        exclusion_institution_count=4,
    )
    assert summary["work_edge_count"] == 3
    assert summary["annual_edge_count"] == 3
    connection = duckdb.connect()
    try:
        pairs = connection.execute(
            """
            SELECT source_id, target_id, full_weight, fractional_weight
            FROM read_parquet(?) ORDER BY 1, 2
            """,
            [str(work_edges)],
        ).fetchall()
        diag = connection.execute(
            """
            SELECT institution_count, generated_pair_count, fractional_weight_sum,
                   is_large_consortium
            FROM read_parquet(?)
            """,
            [str(diagnostics)],
        ).fetchone()
    finally:
        connection.close()
    assert pairs == [("I1", "I2", 1, 1 / 3), ("I1", "I3", 1, 1 / 3), ("I2", "I3", 1, 1 / 3)]
    assert diag[0:2] == (3, 3)
    assert abs(diag[2] - 1.0) < 1e-12
    assert diag[3] is True
