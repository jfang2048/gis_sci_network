from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from gisnet.visualization.dashboard_data import (
    _validate_public_metadata,
    _write_filter_dimensions,
    _write_geography_anchors,
    _write_geography_dimensions,
    _write_geography_outputs,
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


def test_geography_anchors_use_sourced_coordinates_and_record_license(tmp_path: Path) -> None:
    nodes = tmp_path / "nodes.parquet"
    institutions = tmp_path / "institutions.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "institution_id": "I1",
                    "hierarchy_view": "organization",
                    "country_code": "JP",
                    "country_name": "Japan",
                    "macro_region": "Asia",
                    "subregion": "Eastern Asia",
                    "latitude": 35.0,
                    "longitude": 140.0,
                },
                {
                    "institution_id": "I2",
                    "hierarchy_view": "organization",
                    "country_code": "JP",
                    "country_name": "Japan",
                    "macro_region": "Asia",
                    "subregion": "Eastern Asia",
                    "latitude": 35.0,
                    "longitude": 130.0,
                },
                {
                    "institution_id": "I3",
                    "hierarchy_view": "organization",
                    "country_code": "FR",
                    "country_name": "France",
                    "macro_region": "Europe",
                    "subregion": "Western Europe",
                    "latitude": None,
                    "longitude": None,
                },
            ]
        ),
        nodes,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"institution_id": "I1", "coordinate_source": "openalex"},
                {"institution_id": "I2", "coordinate_source": "openalex"},
                {"institution_id": "I3", "coordinate_source": None},
            ]
        ),
        institutions,
    )
    output = tmp_path / "geography_anchors.parquet"
    connection = duckdb.connect()
    try:
        _write_geography_anchors(
            connection,
            complete_nodes_path=nodes,
            institutions_path=institutions,
            destination=output,
            source_dataset_sha256="abc123",
        )
    finally:
        connection.close()

    rows = pq.read_table(output).to_pylist()
    country = next(
        row for row in rows if row["geographic_level"] == "country" and row["geography"] == "JP"
    )
    assert country["display_name"] == "Japan"
    assert country["macro_region"] == "Asia"
    assert country["latitude"] == pytest.approx(35.1027, abs=1e-3)
    assert country["longitude"] == pytest.approx(135.0, abs=1e-8)
    assert country["supporting_institution_count"] == 2
    assert country["coordinate_source"] == "openalex"
    assert country["coordinate_license"] == "CC0 1.0 Universal"
    assert country["source_dataset_sha256"] == "abc123"
    assert not any(row["geography"] == "FR" for row in rows)


def test_geography_outputs_are_exact_scope_denominators(tmp_path: Path) -> None:
    nodes = tmp_path / "nodes.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2025,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "country_code": "JP",
                    "macro_region": "Asia",
                    "subregion": "Eastern Asia",
                    "work_count": 10,
                    "fractional_work_count": 6.0,
                },
                {
                    "year": 2025,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "country_code": "JP",
                    "macro_region": "Asia",
                    "subregion": "Eastern Asia",
                    "work_count": 5,
                    "fractional_work_count": 4.0,
                },
            ]
        ),
        nodes,
    )
    output = tmp_path / "geography_outputs.parquet"
    connection = duckdb.connect()
    try:
        _write_geography_outputs(
            connection,
            complete_nodes_path=nodes,
            destination=output,
        )
    finally:
        connection.close()

    rows = pq.read_table(output).to_pylist()
    assert len(rows) == 3
    assert {row["geographic_level"] for row in rows} == {
        "macro_region",
        "subregion",
        "country",
    }
    assert all(row["full_work_count"] == 15 for row in rows)
    assert all(row["fractional_work_count"] == 10.0 for row in rows)
