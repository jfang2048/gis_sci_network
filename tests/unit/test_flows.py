from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.network.flows import build_geographic_flows


def test_geography_pairs_are_canonical_and_reconcile(tmp_path: Path) -> None:
    source = tmp_path / "work-edges.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2020,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "work_id": "W1",
                    "source_id": "I1",
                    "target_id": "I2",
                    "source_region": "Europe",
                    "target_region": "Asia",
                    "source_subregion": "Western Europe",
                    "target_subregion": "Eastern Asia",
                    "source_country": "DE",
                    "target_country": "CN",
                    "full_weight": 1,
                    "fractional_weight": 1.0,
                },
                {
                    "year": 2020,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "work_id": "W3",
                    "source_id": "I5",
                    "target_id": "I6",
                    "source_region": "Asia",
                    "target_region": "Americas",
                    "source_subregion": "Eastern Asia",
                    "target_subregion": "South America",
                    "source_country": "JP",
                    "target_country": "BR",
                    "full_weight": 1,
                    "fractional_weight": 1.0,
                },
                {
                    "year": 2020,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "work_id": "W2",
                    "source_id": "I3",
                    "target_id": "I4",
                    "source_region": "Asia",
                    "target_region": "Americas",
                    "source_subregion": "Southern Asia",
                    "target_subregion": "Northern America",
                    "source_country": "IN",
                    "target_country": "US",
                    "full_weight": 1,
                    "fractional_weight": 1.0,
                },
            ]
        ),
        source,
    )
    flows = tmp_path / "flows.parquet"
    reconciliation = tmp_path / "reconcile.parquet"
    summary = build_geographic_flows(source, flows_path=flows, reconciliation_path=reconciliation)
    assert summary["flow_row_count"] == 8
    assert summary["asia_country_count"] == 3
    assert summary["americas_country_count"] == 2
    c = duckdb.connect()
    try:
        rows = c.execute(
            "select source_geography,target_geography,full_count,normalized_share "
            "from read_parquet(?) where geographic_level='macro_region' order by 1,2",
            [str(flows)],
        ).fetchall()
    finally:
        c.close()
    assert rows == [
        ("Americas", "Asia", 2, 2 / 3),
        ("Asia", "Europe", 1, 1 / 3),
    ]
