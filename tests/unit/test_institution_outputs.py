from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.network.outputs import build_institution_outputs


def test_fractional_outputs_reconcile_and_singleton_is_retained(tmp_path: Path) -> None:
    source = tmp_path / "work-institutions.parquet"
    base = {
        "hierarchy_view": "organization",
        "normalized_category": "higher_education",
        "analytical_scope": "primary",
        "latitude": 1.0,
        "longitude": 2.0,
        "strict_primary": True,
        "broad_primary": True,
        "is_primary_network_scope": True,
        "ror_id": None,
    }
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    **base,
                    "publication_year": 2020,
                    "work_id": "W1",
                    "institution_id": "I1",
                    "display_name": "One",
                    "country_code": "DE",
                    "country_name": "Germany",
                    "macro_region": "Europe",
                    "subregion": "Western Europe",
                },
                {
                    **base,
                    "publication_year": 2020,
                    "work_id": "W1",
                    "institution_id": "I2",
                    "display_name": "Two",
                    "country_code": "US",
                    "country_name": "United States",
                    "macro_region": "Americas",
                    "subregion": "Northern America",
                },
                {
                    **base,
                    "publication_year": 2020,
                    "work_id": "W2",
                    "institution_id": "I1",
                    "display_name": "One",
                    "country_code": "DE",
                    "country_name": "Germany",
                    "macro_region": "Europe",
                    "subregion": "Western Europe",
                },
                {
                    **base,
                    "publication_year": 2020,
                    "work_id": "W3",
                    "institution_id": "I3",
                    "display_name": "Three",
                    "country_code": "FR",
                    "country_name": "France",
                    "macro_region": "Europe",
                    "subregion": "Western Europe",
                },
            ]
        ),
        source,
    )
    outputs = tmp_path / "outputs.parquet"
    reconciliation = tmp_path / "reconcile.parquet"
    summary = build_institution_outputs(
        source,
        outputs_year_path=outputs,
        reconciliation_path=reconciliation,
        corpus_views=["strict"],
        hierarchy_views=["organization"],
    )
    assert summary["node_year_count"] == 3
    assert summary["zero_edge_output_node_year_count"] == 1
    c = duckdb.connect()
    try:
        rows = c.execute(
            "select institution_id,work_count,fractional_work_count,collaborative_work_count,"
            "single_institution_work_count,international_collaboration_share "
            "from read_parquet(?) order by institution_id",
            [str(outputs)],
        ).fetchall()
        diff = c.execute(
            "select fractional_work_difference,work_institution_row_difference "
            "from read_parquet(?)",
            [str(reconciliation)],
        ).fetchone()
    finally:
        c.close()
    assert rows == [
        ("I1", 2, 1.5, 1, 1, 0.5),
        ("I2", 1, 0.5, 1, 0, 1.0),
        ("I3", 1, 1.0, 0, 1, 0.0),
    ]
    assert abs(diff[0]) < 1e-12 and diff[1] == 0
