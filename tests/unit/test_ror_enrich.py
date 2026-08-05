import json
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.ror.enrich import enrich_institutions_with_ror, normalize_ror_record


def _record(identifier: str = "03ysstz10") -> dict[str, object]:
    return {
        "id": f"https://ror.org/{identifier}",
        "admin": {"last_modified": {"date": "2025-09-22", "schema_version": "2.1"}},
        "names": [
            {"value": "Example Institute", "lang": "en", "types": ["ror_display", "label"]},
            {"value": "EI", "lang": "en", "types": ["acronym"]},
        ],
        "locations": [
            {
                "geonames_details": {
                    "country_code": "DE",
                    "country_name": "Germany",
                    "name": "Berlin",
                    "lat": 52.5,
                    "lng": 13.4,
                }
            }
        ],
        "relationships": [{"type": "parent", "id": "https://ror.org/058rymf81", "label": "Parent"}],
        "status": "active",
        "types": ["education", "funder"],
    }


def test_normalizer_extracts_v21_fields() -> None:
    value = normalize_ror_record(_record())
    assert value["ror_record_id"] == "03ysstz10"
    assert value["ror_display_name"] == "Example Institute"
    assert value["ror_acronyms"] == ["EI"]
    assert value["ror_country_code"] == "DE"
    assert value["ror_parent_ids"] == ["058rymf81"]
    assert value["ror_schema_version"] == "2.1"


def test_dump_and_cache_modes_share_normalized_schema(tmp_path: Path) -> None:
    institutions = tmp_path / "institutions.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "institution_id": "I1",
                    "ror_id": "https://ror.org/03ysstz10",
                    "display_name": "Example Institute",
                    "country_code": "DE",
                    "institution_type": "education",
                },
                {
                    "institution_id": "I2",
                    "ror_id": None,
                    "display_name": "No ROR",
                    "country_code": "US",
                    "institution_type": "education",
                },
            ]
        ),
        institutions,
    )
    dump = tmp_path / "ror.json"
    dump.write_text(json.dumps([_record()]), encoding="utf-8")
    dump_output = tmp_path / "dump.parquet"
    dump_qa = tmp_path / "dump-qa.parquet"
    summary = enrich_institutions_with_ror(
        institutions,
        output_path=dump_output,
        qa_path=dump_qa,
        cache_directory=tmp_path / "cache",
        mode="dump",
        dump_path=dump,
        dump_version="test-v1",
    )
    assert summary["status_counts"] == {"enriched": 1, "missing_ror_id": 1}
    cache = tmp_path / "cache" / "03ysstz10.json"
    cache.parent.mkdir()
    cache.write_text(json.dumps(_record()), encoding="utf-8")
    cache_output = tmp_path / "cache.parquet"
    cache_qa = tmp_path / "cache-qa.parquet"
    enrich_institutions_with_ror(
        institutions,
        output_path=cache_output,
        qa_path=cache_qa,
        cache_directory=cache.parent,
        mode="cache",
    )
    connection = duckdb.connect()
    try:
        dump_rows = connection.execute(
            "SELECT * FROM read_parquet(?) ORDER BY institution_id", [str(dump_output)]
        ).fetchall()
        cache_rows = connection.execute(
            "SELECT * FROM read_parquet(?) ORDER BY institution_id", [str(cache_output)]
        ).fetchall()
    finally:
        connection.close()
    assert dump_rows == cache_rows
