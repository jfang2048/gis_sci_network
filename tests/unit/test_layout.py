from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.visualization.layout import build_fixed_layout


def test_fixed_layout_is_deterministic_and_assigns_fallback(tmp_path: Path) -> None:
    nodes = tmp_path / "nodes.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2020,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "institution_id": identifier,
                    "display_name": identifier,
                    "macro_region": "Europe",
                    "country_code": "FR",
                    "fractional_strength": strength,
                }
                for identifier, strength in (("A", 4.0), ("B", 3.0), ("C", 2.0), ("D", 0.0))
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
                    "source_id": source,
                    "target_id": target,
                    "fractional_count": 1.0,
                }
                for source, target in (("A", "B"), ("B", "C"), ("A", "C"))
            ]
        ),
        edges,
    )
    output = tmp_path / "layout.parquet"
    build_fixed_layout(edges, nodes, output_path=output, random_seed=11, core_size=3)
    first = output.read_bytes()
    build_fixed_layout(edges, nodes, output_path=output, random_seed=11, core_size=3)
    assert output.read_bytes() == first
    c = duckdb.connect()
    try:
        counts = c.execute(
            "select count(*) filter(where is_core),count(*) filter(where not is_core),"
            "count(*) filter(where not isfinite(x) or not isfinite(y)) from read_parquet(?)",
            [str(output)],
        ).fetchone()
    finally:
        c.close()
    assert counts == (3, 1, 0)
