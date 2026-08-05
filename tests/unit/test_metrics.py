from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.network.metrics import build_network_metrics


def test_metrics_on_path_with_isolate(tmp_path: Path) -> None:
    nodes = tmp_path / "nodes.parquet"
    node_rows = [
        {
            "year": year,
            "corpus_view": "strict",
            "hierarchy_view": "organization",
            "institution_id": institution,
            "display_name": institution,
            "country_code": country,
            "macro_region": region,
            "work_count": 1,
            "fractional_work_count": 1.0,
            "international_collaboration_share": 0.0,
            "cross_region_collaboration_share": 0.0,
        }
        for year, institutions in (
            (2019, (("I1", "FR", "Europe"), ("I2", "DE", "Europe"), ("I3", "JP", "Asia"))),
            (
                2020,
                (
                    ("I1", "FR", "Europe"),
                    ("I2", "DE", "Europe"),
                    ("I3", "JP", "Asia"),
                    ("I4", "US", "Americas"),
                ),
            ),
        )
        for institution, country, region in institutions
    ]
    pq.write_table(pa.Table.from_pylist(node_rows), nodes)
    edges = tmp_path / "edges.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2019,
                    "corpus_view": "strict",
                    "hierarchy_view": "organization",
                    "source_id": "I1",
                    "target_id": "I2",
                    "full_count": 1,
                    "fractional_count": 0.5,
                    "source_region": "Europe",
                    "target_region": "Europe",
                    "source_country": "France",
                    "target_country": "Germany",
                }
            ]
            + [
                {
                    "year": 2020,
                    "corpus_view": "strict",
                    "hierarchy_view": "organization",
                    "source_id": source,
                    "target_id": target,
                    "full_count": 1,
                    "fractional_count": 0.5,
                    "source_region": source_region,
                    "target_region": target_region,
                    "source_country": source_country,
                    "target_country": target_country,
                }
                for (
                    source,
                    target,
                    source_region,
                    target_region,
                    source_country,
                    target_country,
                ) in (
                    ("I1", "I2", "Europe", "Europe", "France", "Germany"),
                    ("I2", "I3", "Europe", "Asia", "Germany", "Japan"),
                )
            ]
        ),
        edges,
    )
    node_output = tmp_path / "nodes_metrics.parquet"
    graph_output = tmp_path / "graph_metrics.parquet"
    result = build_network_metrics(
        edges,
        nodes,
        nodes_metrics_path=node_output,
        graph_metrics_path=graph_output,
        approximate_betweenness_threshold=3,
        random_seed=7,
    )
    assert result["node_metric_row_count"] == 7
    c = duckdb.connect()
    try:
        degree = c.execute(
            "select institution_id,degree from read_parquet(?) where year=2020 "
            "order by institution_id",
            [str(node_output)],
        ).fetchall()
        graph = c.execute(
            "select node_count,edge_count,connected_component_count,density,"
            "new_edge_count,continuing_edge_count from read_parquet(?) where year=2020",
            [str(graph_output)],
        ).fetchone()
        pagerank_sum = c.execute(
            "select sum(pagerank) from read_parquet(?) where year=2020", [str(node_output)]
        ).fetchone()
    finally:
        c.close()
    assert degree == [("I1", 1), ("I2", 2), ("I3", 1), ("I4", 0)]
    assert graph is not None
    assert graph[:3] == (4, 2, 2)
    assert abs(graph[3] - 1 / 3) < 1e-12
    assert graph[4:] == (1, 1)
    assert pagerank_sum is not None and abs(pagerank_sum[0] - 1.0) < 1e-12
