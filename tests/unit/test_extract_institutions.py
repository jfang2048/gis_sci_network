import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.institutions.extract import extract_work_institutions


def authorship(
    institution_id: str | None,
    *,
    affiliation: str,
    display_name: str = "Institution",
) -> dict[str, Any]:
    institutions = []
    if institution_id is not None:
        institutions.append(
            {
                "id": f"https://openalex.org/{institution_id}",
                "ror": "https://ror.org/012345678",
                "display_name": display_name,
                "country_code": "DE",
                "type": "education",
                "lineage": [f"https://openalex.org/{institution_id}"],
            }
        )
    return {
        "author": {"id": "https://openalex.org/A1"},
        "institutions": institutions,
        "raw_affiliation_strings": [affiliation],
        "affiliations": [{"raw_affiliation_string": affiliation}],
    }


def write_works(path: Path) -> None:
    rows = [
        {
            "work_id": "W1",
            "publication_year": 2020,
            "authorships_json": json.dumps(
                [
                    authorship("I1", affiliation="Beta University"),
                    authorship("I1", affiliation="Alpha University"),
                    authorship("I2", affiliation="Institute Two", display_name="Two"),
                ],
                sort_keys=True,
            ),
        },
        {
            "work_id": "W2",
            "publication_year": 2021,
            "authorships_json": json.dumps(
                [authorship(None, affiliation="Unmatched laboratory")], sort_keys=True
            ),
        },
        {"work_id": "W3", "publication_year": 2022, "authorships_json": "[]"},
    ]
    pq.write_table(pa.Table.from_pylist(rows), path, compression="zstd")


def test_extracts_distinct_institutions_and_unresolved_works(tmp_path: Path) -> None:
    works = tmp_path / "works.parquet"
    extracted = tmp_path / "extracted.parquet"
    unresolved = tmp_path / "unresolved.parquet"
    write_works(works)
    kwargs = {
        "extracted_path": extracted,
        "unresolved_path": unresolved,
        "start_year": 2010,
        "end_year": 2025,
        "batch_size": 2,
        "force": True,
    }
    summary = extract_work_institutions(works, **kwargs)
    assert summary["input_work_count"] == 3
    assert summary["resolved_work_count"] == 1
    assert summary["unresolved_work_count"] == 2
    assert summary["work_institution_count"] == 2
    assert summary["distinct_institution_count"] == 2
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """
            SELECT work_id, institution_id, raw_affiliation_strings, authorship_count
            FROM read_parquet(?) ORDER BY work_id, institution_id
            """,
            [str(extracted)],
        ).fetchall()
        unresolved_rows = connection.execute(
            "SELECT work_id, reason FROM read_parquet(?) ORDER BY work_id", [str(unresolved)]
        ).fetchall()
    finally:
        connection.close()
    assert rows == [
        ("W1", "I1", ["Alpha University", "Beta University"], 2),
        ("W1", "I2", ["Institute Two"], 1),
    ]
    assert unresolved_rows == [("W2", "no_resolved_institution"), ("W3", "no_authorships")]

    first = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in [extracted, unresolved]
    }
    rerun = extract_work_institutions(works, **kwargs)
    second = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in [extracted, unresolved]
    }
    assert rerun["work_institution_count"] == 2
    assert first == second
