from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gisnet.dataset import file_sha256
from gisnet.network.multiplex import build_multiplex_comparison


def test_multiplex_comparison_keeps_layers_separate_and_unweighted(tmp_path: Path) -> None:
    collaboration = tmp_path / "collaboration.parquet"
    citation = tmp_path / "citation.parquet"
    proximity = tmp_path / "proximity.parquet"
    layers = tmp_path / "layers.parquet"
    overlaps = tmp_path / "overlaps.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2020,
                    "corpus_view": "strict",
                    "hierarchy_view": "organization",
                    "source_id": "A",
                    "target_id": "B",
                    "fractional_count": 1.0,
                },
                {
                    "year": 2020,
                    "corpus_view": "strict",
                    "hierarchy_view": "organization",
                    "source_id": "B",
                    "target_id": "C",
                    "fractional_count": 2.0,
                },
            ]
        ),
        collaboration,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2020,
                    "corpus_view": "strict",
                    "hierarchy_view": "organization",
                    "source_id": "A",
                    "target_id": "B",
                    "fractional_count": 0.5,
                },
                {
                    "year": 2020,
                    "corpus_view": "strict",
                    "hierarchy_view": "organization",
                    "source_id": "C",
                    "target_id": "A",
                    "fractional_count": 0.5,
                },
                {
                    "year": 2020,
                    "corpus_view": "strict",
                    "hierarchy_view": "organization",
                    "source_id": "A",
                    "target_id": "A",
                    "fractional_count": 1.0,
                },
            ]
        ),
        citation,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2020,
                    "corpus_view": "strict",
                    "hierarchy_view": "organization",
                    "source_id": "A",
                    "target_id": "B",
                    "cosine_similarity": 0.9,
                },
                {
                    "year": 2020,
                    "corpus_view": "strict",
                    "hierarchy_view": "organization",
                    "source_id": "A",
                    "target_id": "C",
                    "cosine_similarity": 0.8,
                },
            ]
        ),
        proximity,
    )

    summary = build_multiplex_comparison(
        collaboration,
        citation,
        proximity,
        layer_summary_path=layers,
        overlap_path=overlaps,
    )

    connection = duckdb.connect()
    try:
        layer_rows = connection.execute(
            """
            SELECT layer, directionality, node_count, edge_count, self_edge_count,
                   undirected_dyad_count, total_layer_weight, weight_semantics
            FROM read_parquet(?) ORDER BY layer
            """,
            [str(layers)],
        ).fetchall()
        overlap_rows = connection.execute(
            """
            SELECT layer_a, layer_b, layer_a_dyad_count, layer_b_dyad_count,
                   shared_dyad_count, union_dyad_count, dyad_jaccard,
                   shared_node_count, node_jaccard, comparison_weighting,
                   citation_overlap_projection
            FROM read_parquet(?) ORDER BY layer_a, layer_b
            """,
            [str(overlaps)],
        ).fetchall()
    finally:
        connection.close()

    assert [(row[0], *row[2:6]) for row in layer_rows] == [
        ("citation_flow", 3, 3, 1, 2),
        ("coauthorship", 3, 2, 0, 2),
        ("topic_proximity", 3, 2, 0, 2),
    ]
    assert [row[1] for row in layer_rows] == ["directed", "undirected", "undirected"]
    assert [row[6] for row in layer_rows] == pytest.approx([2.0, 3.0, 1.7])
    assert [row[7] for row in layer_rows] == [
        "fractional citation-flow weight",
        "fractional co-authorship weight",
        "cosine Topic-profile proximity",
    ]
    assert [(row[0], row[1], *row[2:6]) for row in overlap_rows] == [
        ("citation_flow", "coauthorship", 2, 2, 1, 3),
        ("citation_flow", "topic_proximity", 2, 2, 2, 2),
        ("coauthorship", "topic_proximity", 2, 2, 1, 3),
    ]
    assert [row[6] for row in overlap_rows] == pytest.approx([1 / 3, 1.0, 1 / 3])
    assert all(row[7] == 3 and row[8] == 1.0 for row in overlap_rows)
    assert all(
        row[9] == "unweighted presence only; no layer weights applied" for row in overlap_rows
    )
    assert [row[10] for row in overlap_rows] == [
        "direction ignored for dyad presence only",
        "direction ignored for dyad presence only",
        "not applicable; both layers are undirected",
    ]
    assert summary["layers_merged"] is False
    assert summary["composite_weight_defined"] is False
    assert summary["citation_overlap_projection"] == "direction ignored for dyad presence only"

    first_hashes = (file_sha256(layers), file_sha256(overlaps))
    build_multiplex_comparison(
        collaboration,
        citation,
        proximity,
        layer_summary_path=layers,
        overlap_path=overlaps,
    )
    assert first_hashes == (file_sha256(layers), file_sha256(overlaps))
