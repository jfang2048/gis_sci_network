from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gisnet.dataset import file_sha256
from gisnet.network.topic_similarity import build_topic_similarity


def _institution(work_id: str, institution_id: str, *, strict: bool = True) -> dict[str, object]:
    return {
        "work_id": work_id,
        "publication_year": 2020,
        "hierarchy_view": "organization",
        "institution_id": institution_id,
        "display_name": f"Institution {institution_id}",
        "macro_region": "Europe" if institution_id in {"A", "B"} else "Asia",
        "subregion": "Western Europe" if institution_id in {"A", "B"} else "Eastern Asia",
        "country_code": "FR" if institution_id in {"A", "B"} else "JP",
        "normalized_category": "university",
        "strict_primary": strict,
        "broad_primary": True,
        "is_primary_network_scope": True,
    }


def _topic(
    work_id: str,
    topic_id: str,
    score: float,
    membership: str,
) -> dict[str, object]:
    return {
        "work_id": work_id,
        "topic_id": topic_id,
        "topic_name": f"Topic {topic_id}",
        "topic_score": score,
        "subfield_name": "Subfield",
        "field_name": "Field",
        "domain_name": "Domain",
        "corpus_membership": membership,
    }


def test_topic_similarity_builds_normalized_vectors_and_union_top_k_edges(
    tmp_path: Path,
) -> None:
    institutions = tmp_path / "work-institutions.parquet"
    topics = tmp_path / "work-topics.parquet"
    vectors = tmp_path / "topic-vectors.parquet"
    edges = tmp_path / "topic-similarity.parquet"
    coverage = tmp_path / "topic-coverage.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                _institution("W1", "A"),
                _institution("W2", "B"),
                _institution("W3", "C"),
                _institution("W4", "D", strict=False),
                _institution("W5", "A"),
                _institution("W5", "B"),
            ]
        ),
        institutions,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                _topic("W1", "T1", 1.0, "strict"),
                _topic("W1", "T9", 1.0, "uncertain"),
                _topic("W2", "T1", 0.8, "strict"),
                _topic("W3", "T2", 1.0, "strict"),
                _topic("W4", "T1", 0.6, "broad_only"),
                _topic("W4", "T2", 0.8, "broad_only"),
                _topic("W5", "T2", 1.0, "strict"),
            ]
        ),
        topics,
    )

    summary = build_topic_similarity(
        topics,
        institutions,
        vectors_path=vectors,
        edges_path=edges,
        coverage_path=coverage,
        corpus_views=["strict", "broad"],
        hierarchy_views=["organization"],
        maximum_institutions_per_view=4,
        top_k=1,
        memory_limit="256MB",
    )

    connection = duckdb.connect()
    try:
        vector_totals = connection.execute(
            """
            SELECT corpus_view, sum(topic_weight), count(DISTINCT topic_id)
            FROM read_parquet(?) GROUP BY corpus_view ORDER BY corpus_view
            """,
            [str(vectors)],
        ).fetchall()
        norm_failures = connection.execute(
            """
            SELECT count(*) FROM (
                SELECT year, corpus_view, hierarchy_view, institution_id,
                       sum(normalized_topic_weight * normalized_topic_weight) AS squared_norm
                FROM read_parquet(?) GROUP BY 1, 2, 3, 4
            ) WHERE abs(squared_norm - 1.0) > 1e-12
            """,
            [str(vectors)],
        ).fetchone()
        strict_edges = connection.execute(
            """
            SELECT source_id, target_id
            FROM read_parquet(?) WHERE corpus_view = 'strict' ORDER BY 1, 2
            """,
            [str(edges)],
        ).fetchall()
        invalid_edges = connection.execute(
            """
            SELECT count(*) FROM read_parquet(?)
            WHERE source_id >= target_id OR cosine_similarity <= 0 OR cosine_similarity > 1
               OR least(source_neighbor_rank, target_neighbor_rank) > 1
            """,
            [str(edges)],
        ).fetchone()
        coverage_rows = connection.execute(
            """
            SELECT corpus_view, in_scope_institution_count,
                   vector_eligible_institution_count, zero_vector_institution_count,
                   selected_core_institution_count,
                   vector_component_count, topic_dimension_count,
                   source_topic_score_sum, vector_topic_weight_sum,
                   selected_similarity_edge_count
            FROM read_parquet(?) ORDER BY corpus_view
            """,
            [str(coverage)],
        ).fetchall()
        excluded_topics = connection.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE topic_id = 'T9'",
            [str(vectors)],
        ).fetchone()
    finally:
        connection.close()

    assert [(row[0], row[2]) for row in vector_totals] == [("broad", 2), ("strict", 2)]
    assert [row[1] for row in vector_totals] == pytest.approx([5.2, 3.8])
    assert norm_failures == (0,)
    assert strict_edges == [("A", "B"), ("B", "C")]
    assert invalid_edges == (0,)
    assert [row[:7] + row[9:] for row in coverage_rows] == [
        ("broad", 4, 4, 0, 4, 7, 2, 3),
        ("strict", 3, 3, 0, 3, 5, 2, 2),
    ]
    assert [row[7] for row in coverage_rows] == pytest.approx([5.2, 3.8])
    assert [row[8] for row in coverage_rows] == pytest.approx([5.2, 3.8])
    assert excluded_topics == (0,)
    assert summary["layer_semantics"] == "Topic-profile research proximity, not collaboration"
    assert summary["edge_selection_policy"] == "union of top-k neighbors per institution"
    assert summary["maximum_weight_reconciliation_error"] < 1e-12

    first_hashes = (file_sha256(vectors), file_sha256(edges), file_sha256(coverage))
    build_topic_similarity(
        topics,
        institutions,
        vectors_path=vectors,
        edges_path=edges,
        coverage_path=coverage,
        corpus_views=["strict", "broad"],
        hierarchy_views=["organization"],
        maximum_institutions_per_view=4,
        top_k=1,
        memory_limit="256MB",
    )
    assert first_hashes == (
        file_sha256(vectors),
        file_sha256(edges),
        file_sha256(coverage),
    )
