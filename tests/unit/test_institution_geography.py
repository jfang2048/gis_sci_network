from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.geography import load_region_registry
from gisnet.institutions.geography import apply_institution_geography
from gisnet.institutions.overrides import InstitutionOverrideRegistry


def test_applies_manual_country_and_preserves_unknown_qa(tmp_path: Path) -> None:
    institutions = tmp_path / "institutions.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"institution_id": "I1", "country_code": "US", "display_name": "One"},
                {"institution_id": "I2", "country_code": None, "display_name": "Two"},
            ]
        ),
        institutions,
    )
    overrides_path = tmp_path / "overrides.csv"
    overrides_path.write_text(
        "rule_id,action,source_institution_id,target_institution_id,country_code,reason,provenance\n"
        "country-i1,manual_country,I1,,DE,Test correction,Test fixture\n",
        encoding="utf-8",
    )
    output = tmp_path / "geographic.parquet"
    qa = tmp_path / "qa.parquet"
    summary = apply_institution_geography(
        institutions,
        load_region_registry(),
        InstitutionOverrideRegistry.load(overrides_path),
        output_path=output,
        qa_path=qa,
    )
    assert summary["institution_count"] == 2
    assert summary["manual_override_count"] == 1
    assert summary["geography_qa_count"] == 2
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """
            SELECT institution_id, openalex_country_code, country_code, macro_region, subregion,
                   manual_country_override
            FROM read_parquet(?) ORDER BY institution_id
            """,
            [str(output)],
        ).fetchall()
        qa_rows = connection.execute(
            "SELECT institution_id, issue FROM read_parquet(?) ORDER BY institution_id", [str(qa)]
        ).fetchall()
    finally:
        connection.close()
    assert rows == [
        ("I1", "US", "DE", "Europe", "Western Europe", True),
        ("I2", None, "ZZ", "Unknown", "Unknown", False),
    ]
    assert qa_rows == [
        ("I1", "manual_country_differs_from_source"),
        ("I2", "missing_source_country"),
    ]
