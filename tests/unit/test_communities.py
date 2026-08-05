from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.network.communities import build_annual_communities


def test_leiden_assigns_nonisolates_and_is_reproducible(tmp_path: Path) -> None:
    nodes = tmp_path / "nodes.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2020,
                    "corpus_view": "strict",
                    "hierarchy_view": "organization",
                    "institution_id": identifier,
                    "display_name": identifier,
                    "degree": degree,
                }
                for identifier, degree in (
                    ("A", 2),
                    ("B", 2),
                    ("C", 2),
                    ("D", 2),
                    ("E", 2),
                    ("F", 2),
                    ("Z", 0),
                )
            ]
        ),
        nodes,
    )
    edges = tmp_path / "edges.parquet"
    pairs = [("A", "B"), ("A", "C"), ("B", "C"), ("D", "E"), ("D", "F"), ("E", "F")]
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2020,
                    "corpus_view": "strict",
                    "hierarchy_view": "organization",
                    "source_id": source,
                    "target_id": target,
                    "fractional_count": 1.0,
                }
                for source, target in pairs
            ]
        ),
        edges,
    )
    communities = tmp_path / "communities.parquet"
    sensitivity = tmp_path / "sensitivity.parquet"
    build_annual_communities(
        edges,
        nodes,
        communities_path=communities,
        sensitivity_path=sensitivity,
        random_seed=17,
    )
    first = communities.read_bytes()
    build_annual_communities(
        edges,
        nodes,
        communities_path=communities,
        sensitivity_path=sensitivity,
        random_seed=17,
    )
    assert communities.read_bytes() == first
    c = duckdb.connect()
    try:
        missing = c.execute(
            "select count(*) from read_parquet(?) where degree>0 and community_id is null",
            [str(communities)],
        ).fetchone()
        isolate = c.execute(
            "select community_id,status from read_parquet(?) where institution_id='Z'",
            [str(communities)],
        ).fetchone()
        resolutions = c.execute(
            "select count(distinct resolution) from read_parquet(?)", [str(sensitivity)]
        ).fetchone()
    finally:
        c.close()
    assert missing == (0,)
    assert isolate == (None, "isolated")
    assert resolutions == (3,)
