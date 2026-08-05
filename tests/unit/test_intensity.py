from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.network.intensity import build_edge_intensity


def test_intensity_and_fixed_persistence_windows(tmp_path: Path) -> None:
    edges = tmp_path / "edges.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": year,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "source_id": "I1",
                    "target_id": "I2",
                    "fractional_count": 1.0,
                    "full_count": 1,
                }
                for year in (2010, 2012, 2013)
            ]
        ),
        edges,
    )
    nodes = tmp_path / "nodes.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": year,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "institution_id": institution,
                    "work_count": count,
                }
                for year in (2010, 2012, 2013)
                for institution, count in (("I1", 4), ("I2", 9))
            ]
        ),
        nodes,
    )
    output = tmp_path / "metrics.parquet"
    summary = build_edge_intensity(edges, nodes, output_path=output, analysis_start_year=2010)
    assert summary["edge_year_count"] == 3
    c = duckdb.connect()
    try:
        rows = c.execute(
            "select year,normalized_intensity,persistence_3y,persistence_5y,"
            "persistence_3y_incomplete_window,persistence_5y_incomplete_window,"
            "visualization_score_is_primary from read_parquet(?) order by year",
            [str(output)],
        ).fetchall()
    finally:
        c.close()
    assert rows[0] == (2010, 1 / 6, 1 / 3, 1 / 5, True, True, False)
    assert rows[1] == (2012, 1 / 6, 2 / 3, 2 / 5, False, True, False)
    assert rows[2] == (2013, 1 / 6, 2 / 3, 3 / 5, False, True, False)
