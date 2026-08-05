from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from gisnet.corpus.build import build_work_corpus
from gisnet.corpus.work_types import load_work_type_policy


def test_strict_subset_broad_and_exclusions_are_explicit(tmp_path: Path) -> None:
    works = tmp_path / "works.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "work_id": "W1",
                    "title": "One",
                    "doi": None,
                    "publication_year": 2020,
                    "publication_date": "2020-01-01",
                    "work_type": "article",
                    "primary_topic_id": "T1",
                    "primary_topic_name": "Strict",
                    "is_retracted": False,
                    "is_paratext": False,
                },
                {
                    "work_id": "W2",
                    "title": "Two",
                    "doi": None,
                    "publication_year": 2020,
                    "publication_date": "2020-01-01",
                    "work_type": "preprint",
                    "primary_topic_id": "T2",
                    "primary_topic_name": "Broad",
                    "is_retracted": False,
                    "is_paratext": False,
                },
                {
                    "work_id": "W3",
                    "title": "Three",
                    "doi": None,
                    "publication_year": 2021,
                    "publication_date": "2021-01-01",
                    "work_type": "article",
                    "primary_topic_id": "T3",
                    "primary_topic_name": "Excluded",
                    "is_retracted": False,
                    "is_paratext": False,
                },
            ]
        ),
        works,
    )
    topics = tmp_path / "topics.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "work_id": "W1",
                    "topic_id": "T1",
                    "corpus_membership": "strict",
                    "method_family": "gis",
                },
                {
                    "work_id": "W2",
                    "topic_id": "T2",
                    "corpus_membership": "broad_only",
                    "method_family": "rs",
                },
                {
                    "work_id": "W3",
                    "topic_id": "T3",
                    "corpus_membership": "excluded",
                    "method_family": "none",
                },
            ]
        ),
        topics,
    )
    versions = tmp_path / "versions.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "work_id": work_id,
                    "version_family_id": f"work:{work_id}",
                    "primary_representative_work_id": work_id,
                    "is_recommended_primary_representative": True,
                    "ambiguous_possible_family": False,
                }
                for work_id in ("W1", "W2", "W3")
            ]
        ),
        versions,
    )
    corpus = tmp_path / "corpus.parquet"
    annual = tmp_path / "annual.parquet"
    families = tmp_path / "families.parquet"
    summary = build_work_corpus(
        works,
        topics,
        versions,
        load_work_type_policy(),
        corpus_path=corpus,
        annual_counts_path=annual,
        topic_family_counts_path=families,
    )
    assert summary["strict_primary_count"] == 1
    assert summary["broad_primary_count"] == 1
    assert summary["broad_preprint_sensitivity_count"] == 2
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            "select work_id,strict_primary,broad_primary,broad_preprint_sensitivity,"
            "broad_exclusion_reasons from read_parquet(?) order by work_id",
            [str(corpus)],
        ).fetchall()
    finally:
        connection.close()
    assert rows == [
        ("W1", True, True, True, []),
        ("W2", False, False, True, ["work_type_not_primary"]),
        ("W3", False, False, False, ["no_broad_topic"]),
    ]
