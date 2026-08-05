from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.network.graphs import build_annual_graph_catalogue


def test_graph_catalogue_retains_isolates_and_counts_edges(tmp_path: Path) -> None:
    edges = tmp_path / "edges.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2020,
                    "corpus_view": "strict",
                    "hierarchy_view": "organization",
                    "source_id": "I1",
                    "target_id": "I2",
                    "full_count": 2,
                    "fractional_count": 0.5,
                }
            ]
        ),
        edges,
    )
    nodes = tmp_path / "nodes.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2020,
                    "corpus_view": "strict",
                    "hierarchy_view": "organization",
                    "institution_id": institution,
                    "work_count": 1,
                    "analytical_scope": scope,
                }
                for institution, scope in (("I1", "primary"), ("I2", "primary"), ("I3", "expanded"))
            ]
        ),
        nodes,
    )
    output = tmp_path / "graphs.parquet"
    result = build_annual_graph_catalogue(
        edges,
        nodes,
        summary_path=output,
        minimum_fractional_weight=1.0,
    )
    assert result["graph_count"] == 1
    c = duckdb.connect()
    try:
        row = c.execute(
            "select node_count,active_node_count,isolated_output_node_count,edge_count,"
            "configured_filtered_edge_count,primary_node_count from read_parquet(?)",
            [str(output)],
        ).fetchone()
    finally:
        c.close()
    assert row == (3, 2, 1, 1, 0, 2)
