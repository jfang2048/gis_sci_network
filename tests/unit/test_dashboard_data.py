from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gisnet.visualization.dashboard_data import (
    _validate_public_metadata,
    _write_filter_dimensions,
    _write_geography_dimensions,
)


def test_dashboard_metadata_rejects_secrets_and_private_paths() -> None:
    _validate_public_metadata({"source_policy": "processed only"})
    with pytest.raises(ValueError, match="forbidden"):
        _validate_public_metadata({"path": "/home/person/private"})
    with pytest.raises(ValueError, match="forbidden"):
        _validate_public_metadata({"value": "OPENALEX_API_KEY=secret"})


def test_filter_dimensions_include_nodes_without_coordinates(tmp_path: Path) -> None:
    nodes = tmp_path / "nodes.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2025,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "country_name": "France",
                    "subregion": "Western Europe",
                    "institution_category": "education",
                    "latitude": 48.0,
                    "longitude": 2.0,
                },
                {
                    "year": 2025,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "country_name": "Japan",
                    "subregion": "Eastern Asia",
                    "institution_category": "research_facility",
                    "latitude": None,
                    "longitude": None,
                },
            ]
        ),
        nodes,
    )
    output = tmp_path / "filter_dimensions.parquet"
    connection = duckdb.connect()
    try:
        _write_filter_dimensions(connection, nodes, output)
    finally:
        connection.close()

    rows = pq.read_table(output).to_pylist()
    assert {(row["dimension"], row["value"]) for row in rows} >= {
        ("country", "Japan"),
        ("subregion", "Eastern Asia"),
        ("institution_type", "research_facility"),
    }


def test_geography_dimensions_map_country_codes_without_requiring_coordinates(
    tmp_path: Path,
) -> None:
    nodes = tmp_path / "nodes.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "country_code": "FR",
                    "country_name": "France",
                    "macro_region": "Europe",
                    "subregion": "Western Europe",
                    "latitude": 48.0,
                },
                {
                    "country_code": "JP",
                    "country_name": "Japan",
                    "macro_region": "Asia",
                    "subregion": "Eastern Asia",
                    "latitude": None,
                },
            ]
        ),
        nodes,
    )
    output = tmp_path / "geography_dimensions.parquet"
    connection = duckdb.connect()
    try:
        _write_geography_dimensions(connection, nodes, output)
    finally:
        connection.close()

    rows = pq.read_table(output).to_pylist()
    assert rows == [
        {
            "country_code": "FR",
            "country_name": "France",
            "macro_region": "Europe",
            "subregion": "Western Europe",
        },
        {
            "country_code": "JP",
            "country_name": "Japan",
            "macro_region": "Asia",
            "subregion": "Eastern Asia",
        },
    ]
