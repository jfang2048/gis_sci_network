from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.network.edges import build_collaboration_edges


def _node(work_id: str, institution_id: str, hierarchy: str = "organization") -> dict[str, object]:
    return {
        "publication_year": 2020,
        "work_id": work_id,
        "hierarchy_view": hierarchy,
        "institution_id": institution_id,
        "display_name": institution_id,
        "macro_region": "Europe",
        "subregion": "Western Europe",
        "country_code": "DE",
        "normalized_category": "higher_education",
        "method_families": ["gis"],
        "strict_primary": True,
        "broad_primary": True,
        "is_primary_network_scope": True,
    }


def test_three_institutions_make_three_full_and_thirds_fractional(tmp_path: Path) -> None:
    source = tmp_path / "work-institutions.parquet"
    rows = [_node("W1", institution) for institution in ("I1", "I2", "I3")]
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


def test_two_and_many_institution_arithmetic_and_consortium_threshold(tmp_path: Path) -> None:
    source = tmp_path / "work-institutions.parquet"
    rows = [_node("W2", institution) for institution in ("I1", "I2")]
    rows.extend(_node("W5", f"I{index}") for index in range(1, 6))
    pq.write_table(pa.Table.from_pylist(rows), source)
    work_edges = tmp_path / "work-edges.parquet"
    diagnostics = tmp_path / "diag.parquet"
    build_collaboration_edges(
        source,
        work_edges_path=work_edges,
        edges_year_path=tmp_path / "edges.parquet",
        diagnostics_path=diagnostics,
        corpus_views=["strict"],
        hierarchy_views=["organization"],
        warning_institution_count=5,
        exclusion_institution_count=5,
    )
    connection = duckdb.connect()
    try:
        values = connection.execute(
            """
            SELECT work_id, count(*), sum(fractional_weight)
            FROM read_parquet(?) GROUP BY work_id ORDER BY work_id
            """,
            [str(work_edges)],
        ).fetchall()
        consortium = connection.execute(
            """
            SELECT is_large_consortium, exceeds_consortium_exclusion_threshold
            FROM read_parquet(?) WHERE work_id = 'W5'
            """,
            [str(diagnostics)],
        ).fetchone()
    finally:
        connection.close()
    assert values[0] == ("W2", 1, 1.0)
    assert values[1][0:2] == ("W5", 10)
    assert abs(values[1][2] - 1.0) < 1e-12
    assert consortium == (True, True)


def test_umbrella_self_pair_is_removed_only_from_edges(tmp_path: Path) -> None:
    source = tmp_path / "work-institutions.parquet"
    rows = [_node("W1", "I1"), _node("W1", "I2")]
    rows.append(_node("W1", "I2", "umbrella"))
    pq.write_table(pa.Table.from_pylist(rows), source)
    work_edges = tmp_path / "work-edges.parquet"
    build_collaboration_edges(
        source,
        work_edges_path=work_edges,
        edges_year_path=tmp_path / "edges.parquet",
        diagnostics_path=tmp_path / "diag.parquet",
        corpus_views=["strict"],
        hierarchy_views=["organization", "umbrella"],
    )
    connection = duckdb.connect()
    try:
        views = connection.execute(
            "SELECT hierarchy_view, count(*) FROM read_parquet(?) GROUP BY 1",
            [str(work_edges)],
        ).fetchall()
    finally:
        connection.close()
    assert views == [("organization", 1)]
