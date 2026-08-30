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
    _write_school_dashboard_index,
    _write_school_ego_partners,
    _write_school_profile_table,
)


def test_dashboard_metadata_rejects_secrets_and_private_paths() -> None:
    _validate_public_metadata({"source_policy": "processed only"})
    with pytest.raises(ValueError, match="forbidden"):
        _validate_public_metadata({"path": "/home/person/private"})
    with pytest.raises(ValueError, match="forbidden"):
        _validate_public_metadata({"value": "OPENALEX_API_KEY=secret"})


def test_school_profile_publication_uses_stable_school_id_without_changing_evidence(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.parquet"
    destination = tmp_path / "dashboard.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "canonical_school_id": "I1",
                    "corpus_view": "broad",
                    "hierarchy_view": "school",
                    "window_start": "2024-01",
                    "window_end": "2025-12",
                    "window_months": 24,
                    "full_work_count": 7,
                    "profile_support_status": "supported",
                }
            ]
        ),
        source,
    )
    connection = duckdb.connect()
    try:
        _write_school_profile_table(
            connection,
            source=source,
            destination=destination,
        )
    finally:
        connection.close()

    assert pq.read_table(destination).to_pylist() == [
        {
            "school_id": "I1",
            "corpus_view": "broad",
            "hierarchy_view": "school",
            "window_start": "2024-01",
            "window_end": "2025-12",
            "window_months": 24,
            "full_work_count": 7,
            "profile_support_status": "supported",
        }
    ]


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
    assert "rounded to 10 decimal degrees" in country["anchor_method"]
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


def test_school_dashboard_index_and_partner_periods_ignore_global_core_thresholds(
    tmp_path: Path,
) -> None:
    school_index = tmp_path / "school_index.parquet"
    rolling_partners = tmp_path / "school_partner_index.parquet"
    annual_edges = tmp_path / "edges_metrics_year.parquet"
    quarter_edges = tmp_path / "collaboration_edges_quarter.parquet"
    month_edges = tmp_path / "collaboration_edges_month.parquet"
    quarter_outputs = tmp_path / "institution_outputs_quarter.parquet"
    network_nodes = tmp_path / "network_nodes.parquet"

    schools = []
    for identifier, name, country, region, latitude, longitude in (
        ("I1", "Source School", "FR", "Europe", 48.0, 2.0),
        ("I2", "Rolling Partner", "JP", "Asia", 35.0, 139.0),
        ("I3", "Annual Partner", "US", "Americas", 38.0, -97.0),
    ):
        schools.append(
            {
                "institution_id": identifier,
                "canonical_school_id": identifier,
                "display_name": name,
                "alternative_names": [name],
                "search_names": [name],
                "has_ambiguous_name_match": False,
                "country_code": country,
                "country_name": country,
                "macro_region": region,
                "subregion": region,
                "institution_category": "education",
                "analytical_scope": "primary",
                "openalex_id": identifier,
                "ror_id": f"ror-{identifier}",
                "latitude": latitude,
                "longitude": longitude,
                "coordinate_source": "openalex",
                "has_coordinates": True,
                "first_observed_date": "2020-01-01",
                "last_observed_date": "2025-12-31",
                "latest_supported_month": "2025-12",
                "broad_work_count": 20,
                "strict_work_count": 10,
                "recent_24m_work_count": 5,
                "topic_families": ["GIS"],
                "date_coverage_ratio": 1.0,
                "identity_status": "organization_identity",
                "identity_resolution_confidence": "source_stable_id",
                "identity_quality_flags": [],
                "eligibility_status": "eligible",
                "support_status": "supported",
            }
        )
    pq.write_table(pa.Table.from_pylist(schools), school_index)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "window_start": "2024-01",
                    "window_end": "2025-12",
                    "window_months": 24,
                    "coverage_ratio": 1.0,
                    "is_complete_window": True,
                    "corpus_view": "broad",
                    "hierarchy_view": "school",
                    "school_id": "I1",
                    "partner_id": "I2",
                    "full_count": 6,
                    "fractional_count": 3.0,
                    "distinct_work_count": 6,
                    "source_work_count": 20,
                    "target_work_count": 15,
                    "normalized_intensity": 0.2,
                    "active_month_count": 12,
                    "edge_persistence": 0.5,
                    "partner_rank": 1,
                    "support_status": "supported",
                }
            ]
        ),
        rolling_partners,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "year": 2025,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "source_id": "I1",
                    "target_id": "I3",
                    "full_count": 4,
                    "fractional_count": 2.0,
                    "distinct_work_count": 4,
                    "source_work_count": 20,
                    "target_work_count": 10,
                    "normalized_intensity": 0.1,
                    "active_years_5y": 3,
                    "persistence_5y": 0.6,
                    "persistence_5y_incomplete_window": False,
                }
            ]
        ),
        annual_edges,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "publication_quarter": "2025-Q4",
                    "publication_year": 2025,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "source_id": "I1",
                    "target_id": "I2",
                    "full_count": 3,
                    "fractional_count": 2.0,
                    "distinct_work_count": 3,
                }
            ]
        ),
        quarter_edges,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "publication_month": month,
                    "publication_year": 2025,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "source_id": "I1",
                    "target_id": "I2",
                }
                for month in ("2025-10", "2025-11")
            ]
        ),
        month_edges,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "publication_quarter": "2025-Q4",
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "institution_id": identifier,
                    "work_count": count,
                }
                for identifier, count in (("I1", 16), ("I2", 9), ("I3", 4))
            ]
        ),
        quarter_outputs,
    )
    pq.write_table(pa.Table.from_pylist([{"institution_id": "I1"}]), network_nodes)

    public_index = tmp_path / "public_school_index.parquet"
    public_partners = tmp_path / "school_ego_partners.parquet"
    connection = duckdb.connect()
    try:
        _write_school_dashboard_index(
            connection,
            school_index_path=school_index,
            rolling_partner_path=rolling_partners,
            network_nodes_path=network_nodes,
            destination=public_index,
        )
        _write_school_ego_partners(
            connection,
            school_index_path=school_index,
            rolling_partner_path=rolling_partners,
            annual_edges_path=annual_edges,
            quarter_edges_path=quarter_edges,
            month_edges_path=month_edges,
            quarter_outputs_path=quarter_outputs,
            destination=public_partners,
            top_k=50,
        )
    finally:
        connection.close()

    index_rows = {row["school_id"]: row for row in pq.read_table(public_index).to_pylist()}
    assert index_rows["I1"]["in_prior_visualization_core"] is True
    assert index_rows["I1"]["has_retained_ego_partners"] is True
    assert index_rows["I2"]["in_prior_visualization_core"] is False

    partner_rows = pq.read_table(public_partners).to_pylist()
    assert {row["period_key"] for row in partner_rows} == {
        "rolling_24m",
        "quarter_2025-Q4",
        "annual_2025",
    }
    quarterly = next(row for row in partner_rows if row["time_basis"] == "quarterly")
    annual = next(
        row for row in partner_rows if row["time_basis"] == "annual" and row["school_id"] == "I1"
    )
    assert quarterly["persistence"] == pytest.approx(2 / 3)
    assert quarterly["normalized_intensity"] == pytest.approx(2 / (16 * 9) ** 0.5)
    assert annual["partner_id"] == "I3"
    assert annual["persistence"] == pytest.approx(0.6)
