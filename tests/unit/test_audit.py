from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.validation.audit import build_top_entity_audit


def _write(path: Path, rows: list[dict[str, object]]) -> None:
    pq.write_table(pa.Table.from_pylist(rows), path)


def test_top_audit_preserves_samples_and_never_auto_corrects(tmp_path: Path) -> None:
    nodes = tmp_path / "nodes.parquet"
    _write(
        nodes,
        [
            {
                "year": 2020,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "institution_id": "I1",
                "display_name": "One",
                "country_code": "FR",
                "macro_region": "Europe",
                "institution_category": "education",
                "work_count": 2,
                "pagerank": 0.6,
                "betweenness": 0.2,
            },
            {
                "year": 2020,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "institution_id": "I2",
                "display_name": "Two",
                "country_code": "JP",
                "macro_region": "Asia",
                "institution_category": "education",
                "work_count": 1,
                "pagerank": 0.4,
                "betweenness": 0.1,
            },
        ],
    )
    edges = tmp_path / "edges.parquet"
    _write(
        edges,
        [
            {
                "year": 2020,
                "corpus_view": "broad",
                "hierarchy_view": "organization",
                "source_id": "I1",
                "target_id": "I2",
                "source_name": "One",
                "target_name": "Two",
                "source_region": "Europe",
                "target_region": "Asia",
                "source_country": "France",
                "target_country": "Japan",
                "full_count": 1,
                "fractional_count": 0.5,
                "normalized_intensity": 0.2,
                "persistence_5y": 0.2,
                "work_ids_sample": ["W1"],
            }
        ],
    )
    extracted = tmp_path / "extracted.parquet"
    _write(
        extracted,
        [
            {
                "institution_id": "I1",
                "work_id": "W1",
                "raw_affiliation_strings": ["One University"],
            },
            {
                "institution_id": "I2",
                "work_id": "W1",
                "raw_affiliation_strings": ["Two University"],
            },
        ],
    )
    institutions = tmp_path / "institutions.parquet"
    _write(
        institutions,
        [
            {
                "institution_id": identifier,
                "ror_id": None,
                "canonical_institution_id": identifier,
                "canonicalization_rule_id": "identity",
                "metadata_source": "fixture",
            }
            for identifier in ("I1", "I2")
        ],
    )
    hierarchy = tmp_path / "hierarchy.parquet"
    _write(
        hierarchy,
        [
            {
                "hierarchy_view": "organization",
                "institution_id": identifier,
                "canonical_institution_id": identifier,
                "is_collapsed": False,
                "canonicalization_rule_ids": ["identity"],
                "canonicalization_reasons": ["identity"],
                "canonicalization_provenance": "fixture",
            }
            for identifier in ("I1", "I2")
        ],
    )
    institution_output = tmp_path / "institutions_audit.parquet"
    edge_output = tmp_path / "edges_audit.parquet"
    summary = build_top_entity_audit(
        nodes,
        edges,
        extracted,
        institutions,
        hierarchy,
        institution_output_path=institution_output,
        edge_output_path=edge_output,
        sample_size=1,
    )
    assert summary["automatic_correction_count"] == 0
    c = duckdb.connect()
    try:
        row = c.execute(
            "select work_ids_sample,source_raw_affiliation_samples,"
            "correction_applied_automatically from read_parquet(?)",
            [str(edge_output)],
        ).fetchone()
    finally:
        c.close()
    assert row == (["W1"], ["One University"], False)
