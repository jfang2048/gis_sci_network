from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.visualization.map_data import build_map_data


def test_map_data_reports_missing_coordinates_without_inventing_them(tmp_path: Path) -> None:
    nodes = tmp_path / "nodes.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2020,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "institution_id": "I1",
                    "display_name": "One",
                    "country_code": "FR",
                    "country_name": "France",
                    "macro_region": "Europe",
                    "subregion": "Western Europe",
                    "institution_category": "education",
                    "work_count": 2,
                    "latitude": 1.0,
                    "longitude": 2.0,
                },
                {
                    "year": 2020,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "institution_id": "I2",
                    "display_name": "Two",
                    "country_code": "JP",
                    "country_name": "Japan",
                    "macro_region": "Asia",
                    "subregion": "Eastern Asia",
                    "institution_category": "education",
                    "work_count": 1,
                    "latitude": None,
                    "longitude": None,
                },
            ]
        ),
        nodes,
    )
    edges = tmp_path / "edges.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2020,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "source_id": "I1",
                    "target_id": "I2",
                    "source_region": "Europe",
                    "target_region": "Asia",
                    "source_country": "France",
                    "target_country": "Japan",
                    "fractional_count": 0.5,
                    "visualization_score": 1.0,
                    "topic_families": ["GIS"],
                }
            ]
        ),
        edges,
    )
    map_nodes, map_edges, coverage = (
        tmp_path / "map_nodes.parquet",
        tmp_path / "map_edges.parquet",
        tmp_path / "coverage.parquet",
    )
    summary = build_map_data(
        nodes,
        edges,
        map_nodes_path=map_nodes,
        map_edges_path=map_edges,
        coverage_path=coverage,
        edge_limit_per_view=10,
        node_limit_per_view=10,
    )
    assert summary["coordinates_invented"] is False
    c = duckdb.connect()
    try:
        counts = c.execute(
            "select coordinate_node_count,missing_coordinate_node_count,"
            "selected_edge_count from read_parquet(?)",
            [str(coverage)],
        ).fetchone()
    finally:
        c.close()
    assert counts == (1, 1, 0)
