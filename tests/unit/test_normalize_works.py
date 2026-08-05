import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import yaml

from gisnet.atomic import atomic_write_json
from gisnet.corpus.normalize import normalize_raw_works, write_normalization_artifacts
from gisnet.openalex.cache import RawResponseCache


def work(
    identifier: str,
    year: int,
    topic: str = "T1",
    referenced_works: tuple[str, ...] = (),
) -> dict[str, Any]:
    return {
        "id": f"https://openalex.org/{identifier}",
        "doi": f"https://doi.org/10.1/{identifier}",
        "title": identifier,
        "publication_year": year,
        "publication_date": f"{year}-01-01",
        "type": "article",
        "is_retracted": False,
        "is_paratext": False,
        "cited_by_count": 1,
        "fwci": 1.5,
        "primary_topic": {"id": f"https://openalex.org/{topic}", "display_name": topic},
        "topics": [{"id": f"https://openalex.org/{topic}", "display_name": topic, "score": 0.9}],
        "referenced_works": [f"https://openalex.org/{item}" for item in referenced_works],
        "authorships": [],
        "primary_location": None,
        "locations": [],
        "ids": {"openalex": f"https://openalex.org/{identifier}"},
        "updated_date": "2026-08-05T00:00:00Z",
    }


def build_raw_inputs(tmp_path: Path) -> tuple[dict[str, Any], RawResponseCache, Path]:
    cache = RawResponseCache(tmp_path / "cache")
    checkpoints = tmp_path / "raw-checkpoints"
    checkpoints.mkdir()
    query_records = {
        "Q1": [work("W1", 2020, referenced_works=("W999",)), work("W2", 2021, "T2")],
        "Q2": [work("W1", 2020, referenced_works=("W999",)), work("W3", 2009)],
    }
    for query_id, records in query_records.items():
        parameters = {"filter": query_id, "select": "id", "per-page": 200, "cursor": "*"}
        entry = cache.put(
            endpoint="/works",
            parameters=parameters,
            data={"meta": {"next_cursor": None}, "results": records},
            status_code=200,
            retrieved_at_utc="2026-08-05T00:00:00Z",
        )
        atomic_write_json(
            checkpoints / f"{query_id}.json",
            {
                "query_id": query_id,
                "status": "complete",
                "pages": [
                    {
                        "cache_key": entry.key,
                        "checksum_sha256": entry.metadata["checksum_sha256"],
                    }
                ],
            },
        )
    plan = {
        "logical_plan_hash": "test-plan",
        "queries": [{"query_id": "Q1"}, {"query_id": "Q2"}],
    }
    return plan, cache, checkpoints


def test_normalization_deduplicates_quarantines_and_is_deterministic(tmp_path: Path) -> None:
    plan, cache, checkpoints = build_raw_inputs(tmp_path)
    topic_registry = {
        "topics": [
            {
                "topic_id": "T1",
                "corpus_membership": "strict",
                "method_family": "gis",
            },
            {
                "topic_id": "T2",
                "corpus_membership": "broad_only",
                "method_family": "remote_sensing",
            },
        ]
    }
    outputs = tmp_path / "processed"
    kwargs = {
        "checkpoint_directory": checkpoints,
        "staging_path": tmp_path / "stage.duckdb",
        "normalization_checkpoint_path": tmp_path / "normalize-checkpoint.json",
        "output_directory": outputs,
        "start_year": 2010,
        "end_year": 2025,
        "batch_size": 2,
        "duckdb_memory_limit": "64MB",
        "duckdb_threads": 1,
    }
    summary = normalize_raw_works(plan, cache, topic_registry, **kwargs)

    assert summary["duckdb_memory_limit"] in {"61.0 MiB", "64.0 MiB"}
    assert summary["duckdb_threads"] == 1
    assert summary["duckdb_preserve_insertion_order"] is False
    assert summary["work_count"] == 2
    assert summary["work_topic_count"] == 2
    assert summary["work_query_source_count"] == 3
    assert summary["duplicate_source_occurrence_count"] == 1
    assert summary["malformed_record_count"] == 1
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """
            SELECT work_id, source_query_ids, referenced_work_ids
            FROM read_parquet(?) ORDER BY work_id
            """,
            [str(outputs / "works.parquet")],
        ).fetchall()
        topic_work_ids = connection.execute(
            "SELECT DISTINCT work_id FROM read_parquet(?) ORDER BY work_id",
            [str(outputs / "work_topics.parquet")],
        ).fetchall()
    finally:
        connection.close()
    assert rows == [("W1", ["Q1", "Q2"], ["W999"]), ("W2", ["Q1"], [])]
    assert topic_work_ids == [("W1",), ("W2",)]

    first_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in outputs.glob("*.parquet")
    }
    rerun = normalize_raw_works(plan, cache, topic_registry, **kwargs)
    second_hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in outputs.glob("*.parquet")
    }
    assert rerun["work_count"] == 2
    assert first_hashes == second_hashes
    checkpoint = json.loads((tmp_path / "normalize-checkpoint.json").read_text())
    assert checkpoint["completed_query_count"] == 2


def test_normalization_contract_and_empty_qa_manifest(tmp_path: Path, monkeypatch: Any) -> None:
    plan, cache, checkpoints = build_raw_inputs(tmp_path)
    outputs = tmp_path / "processed"
    topic_registry = {
        "topics": [
            {
                "topic_id": "T1",
                "corpus_membership": "strict",
                "method_family": "gis",
            },
            {
                "topic_id": "T2",
                "corpus_membership": "broad_only",
                "method_family": "remote_sensing",
            },
        ]
    }
    summary = normalize_raw_works(
        plan,
        cache,
        topic_registry,
        checkpoint_directory=checkpoints,
        staging_path=tmp_path / "stage.duckdb",
        normalization_checkpoint_path=tmp_path / "normalize-checkpoint.json",
        output_directory=outputs,
        start_year=2010,
        end_year=2025,
        batch_size=2,
    )
    connection = duckdb.connect()
    try:
        work_columns = {
            row[0]
            for row in connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(outputs / "works.parquet")]
            ).fetchall()
        }
        topic_rows = connection.execute(
            """
            SELECT topic_id, is_primary_topic, corpus_membership, method_family
            FROM read_parquet(?) ORDER BY work_id, topic_id
            """,
            [str(outputs / "work_topics.parquet")],
        ).fetchall()
    finally:
        connection.close()
    assert {
        "topic_ids",
        "referenced_work_ids",
        "updated_date",
        "source_query_ids",
    } <= work_columns
    assert topic_rows == [
        ("T1", True, "strict", "gis"),
        ("T2", True, "broad_only", "remote_sensing"),
    ]

    empty_outputs = tmp_path / "empty-processed"
    empty_outputs.mkdir()
    connection = duckdb.connect()
    try:
        connection.execute(
            "COPY (SELECT NULL::VARCHAR AS record_key WHERE false) TO ? (FORMAT PARQUET)",
            [str(empty_outputs / "work_malformed.parquet")],
        )
    finally:
        connection.close()
    summary["outputs"] = {"work_malformed": str(empty_outputs / "work_malformed.parquet")}
    project_path = tmp_path / "project.yml"
    plan_path = tmp_path / "plan.json"
    project_path.write_text(yaml.safe_dump({"project_version": "test"}), encoding="utf-8")
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    write_normalization_artifacts(
        summary,
        summary_path=tmp_path / "summary.json",
        run_id="test-run",
        project_config_path=project_path,
        download_plan_path=plan_path,
        command="test",
    )
    manifest = json.loads(
        (tmp_path / ".agent/manifests/work_malformed.json").read_text(encoding="utf-8")
    )
    assert manifest["row_count"] == 0
    assert manifest["null_counts"] == {"record_key": 0}
