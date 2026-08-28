from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.schools.index import build_school_index


def test_school_index_is_complete_searchable_and_keeps_name_ambiguity(tmp_path: Path) -> None:
    institutions = tmp_path / "institutions.parquet"
    identities = tmp_path / "identities.parquet"
    memberships = tmp_path / "memberships.parquet"
    dates = tmp_path / "dates.parquet"
    layout = tmp_path / "layout.parquet"
    index = tmp_path / "school_index.parquet"
    names = tmp_path / "school_names.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "institution_id": "I1",
                    "display_name": "Alpha University",
                    "alternative_names": ["Common Institute"],
                    "country_code": "NL",
                    "country_name": "Netherlands",
                    "macro_region": "Europe",
                    "subregion": "Western Europe",
                    "normalized_category": "education",
                    "analytical_scope": "primary",
                    "ror_id": "https://ror.org/alpha",
                    "latitude": 52.0,
                    "longitude": 6.0,
                    "coordinate_source": "OpenAlex",
                },
                {
                    "institution_id": "I2",
                    "display_name": "Beta University",
                    "alternative_names": ["Common Institute"],
                    "country_code": "US",
                    "country_name": "United States",
                    "macro_region": "Americas",
                    "subregion": "Northern America",
                    "normalized_category": "education",
                    "analytical_scope": "primary",
                    "ror_id": None,
                    "latitude": None,
                    "longitude": None,
                    "coordinate_source": None,
                },
            ]
        ),
        institutions,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "institution_id": institution_id,
                    "canonical_school_id": institution_id,
                    "identity_status": "retained_source_organization",
                    "resolution_confidence": "source_identity",
                    "quality_flags": [],
                }
                for institution_id in ("I1", "I2")
            ]
        ),
        identities,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "work_id": "W1",
                    "publication_year": 2025,
                    "hierarchy_view": "organization",
                    "institution_id": "I1",
                    "is_primary_research_scope": True,
                    "strict_primary": True,
                    "broad_primary": True,
                    "method_families": ["core_gis"],
                },
                {
                    "work_id": "W2",
                    "publication_year": 2025,
                    "hierarchy_view": "organization",
                    "institution_id": "I2",
                    "is_primary_research_scope": True,
                    "strict_primary": False,
                    "broad_primary": True,
                    "method_families": ["remote_sensing_earth_observation"],
                },
            ]
        ),
        memberships,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "work_id": "W1",
                    "publication_year": 2025,
                    "publication_date": "2025-11-01",
                    "publication_month": "2025-11",
                    "subannual_date_eligible": True,
                },
                {
                    "work_id": "W2",
                    "publication_year": 2025,
                    "publication_date": None,
                    "publication_month": None,
                    "subannual_date_eligible": False,
                },
            ]
        ),
        dates,
    )
    pq.write_table(
        pa.Table.from_pylist([{"institution_id": "I1", "is_core": True}]),
        layout,
    )

    summary = build_school_index(
        institutions,
        identities,
        memberships,
        dates,
        index_path=index,
        name_index_path=names,
        prior_layout_path=layout,
    )

    rows = {row["canonical_school_id"]: row for row in pq.read_table(index).to_pylist()}
    assert set(rows) == {"I1", "I2"}
    assert rows["I1"]["recent_24m_work_count"] == 1
    assert rows["I2"]["annual_only_work_count"] == 1
    assert rows["I2"]["date_coverage_ratio"] == 0.0
    assert rows["I2"]["has_coordinates"] is False
    assert rows["I1"]["has_ambiguous_name_match"] is True
    common = [
        row
        for row in pq.read_table(names).to_pylist()
        if row["normalized_name"] == "common institute"
    ]
    assert {row["canonical_school_id"] for row in common} == {"I1", "I2"}
    assert all(row["is_ambiguous"] for row in common)
    assert summary["eligible_school_count"] == 2
    assert summary["outside_prior_core_count"] == 1
