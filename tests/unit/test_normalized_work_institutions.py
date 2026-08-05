from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.network.work_institutions import build_normalized_work_institutions


def test_deduplicates_after_umbrella_collapse_and_retains_singletons(tmp_path: Path) -> None:
    extracted = tmp_path / "extracted.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "work_id": "W1",
                    "publication_year": 2020,
                    "institution_id": "I1",
                    "display_name": "One",
                    "raw_affiliation_strings": ["One"],
                    "authorship_count": 1,
                    "assertion_count": 1,
                },
                {
                    "work_id": "W1",
                    "publication_year": 2020,
                    "institution_id": "I2",
                    "display_name": "Two",
                    "raw_affiliation_strings": ["Two"],
                    "authorship_count": 1,
                    "assertion_count": 1,
                },
                {
                    "work_id": "W2",
                    "publication_year": 2021,
                    "institution_id": "I2",
                    "display_name": "Two",
                    "raw_affiliation_strings": ["Two"],
                    "authorship_count": 2,
                    "assertion_count": 1,
                },
            ]
        ),
        extracted,
    )
    corpus = tmp_path / "corpus.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "work_id": w,
                    "title": w,
                    "doi": None,
                    "work_type": "article",
                    "primary_topic_id": "T1",
                    "primary_topic_name": "GIS",
                    "method_families": ["gis"],
                    "strict_primary": True,
                    "broad_primary": True,
                    "strict_preprint_sensitivity": True,
                    "broad_preprint_sensitivity": True,
                    "strict_expanded_sensitivity": True,
                    "broad_expanded_sensitivity": True,
                    "strict_all_versions_sensitivity": True,
                    "broad_all_versions_sensitivity": True,
                    "uncertain_topic_sensitivity": False,
                }
                for w in ("W1", "W2")
            ]
        ),
        corpus,
    )
    hierarchy = tmp_path / "hierarchy.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "hierarchy_view": view,
                    "institution_id": source,
                    "canonical_institution_id": target,
                    "is_collapsed": source != target,
                    "canonicalization_rule_ids": ["r1"] if source != target else [],
                }
                for view, source, target in [
                    ("organization", "I1", "I1"),
                    ("organization", "I2", "I2"),
                    ("umbrella", "I1", "I2"),
                    ("umbrella", "I2", "I2"),
                ]
            ]
        ),
        hierarchy,
    )
    institutions = tmp_path / "institutions.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "institution_id": "I1",
                    "ror_id": None,
                    "display_name": "One",
                    "institution_type": "education",
                    "normalized_category": "higher_education",
                    "analytical_scope": "primary",
                    "is_primary_research_scope": True,
                    "country_code": "DE",
                    "country_name": "Germany",
                    "macro_region": "Europe",
                    "subregion": "Western Europe",
                    "latitude": 1.0,
                    "longitude": 2.0,
                },
                {
                    "institution_id": "I2",
                    "ror_id": None,
                    "display_name": "Two",
                    "institution_type": "education",
                    "normalized_category": "higher_education",
                    "analytical_scope": "primary",
                    "is_primary_research_scope": True,
                    "country_code": "US",
                    "country_name": "United States",
                    "macro_region": "Americas",
                    "subregion": "Northern America",
                    "latitude": 3.0,
                    "longitude": 4.0,
                },
            ]
        ),
        institutions,
    )
    output = tmp_path / "output.parquet"
    summary = build_normalized_work_institutions(
        extracted, corpus, institutions, hierarchy, output_path=output
    )
    assert summary["organization_row_count"] == 3
    assert summary["umbrella_row_count"] == 2
    assert summary["organization_single_institution_work_count"] == 1
    assert summary["umbrella_single_institution_work_count"] == 2
    c = duckdb.connect()
    try:
        row = c.execute(
            "select original_institution_ids,contributing_organization_count,was_collapsed "
            "from read_parquet(?) where work_id='W1' and hierarchy_view='umbrella'",
            [str(output)],
        ).fetchone()
    finally:
        c.close()
    assert row == (["I1", "I2"], 2, True)
