"""Deterministic, resumable normalization of validated raw OpenAlex Work pages."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]

from gisnet.atomic import atomic_write_json
from gisnet.config import semantic_hash
from gisnet.openalex.cache import RawResponseCache

_WORK_ID = re.compile(r"^W\d+$")
_TOPIC_ID = re.compile(r"^T\d+$")
_HIERARCHY_ID = re.compile(r"^\d+$")
_STAGE_VERSION = "works-normalizer-2026-08-05-v3"
_DEFAULT_DUCKDB_MEMORY_LIMIT = "6GB"
_DEFAULT_DUCKDB_THREADS = 1


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _short_id(value: Any, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str):
        return None
    identifier = value.rstrip("/").rsplit("/", 1)[-1]
    return identifier if pattern.fullmatch(identifier) else None


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _raw_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode()).hexdigest()


def _file_sha256(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _configure_duckdb(
    connection: duckdb.DuckDBPyConnection,
    *,
    memory_limit: str = _DEFAULT_DUCKDB_MEMORY_LIMIT,
    threads: int = _DEFAULT_DUCKDB_THREADS,
) -> dict[str, Any]:
    if not memory_limit.strip():
        raise ValueError("DuckDB memory limit must not be empty")
    if threads < 1:
        raise ValueError("DuckDB threads must be positive")
    connection.execute("SET memory_limit = ?", [memory_limit])
    connection.execute("SET threads = ?", [threads])
    connection.execute("SET preserve_insertion_order = false")
    settings = dict(
        connection.execute(
            """
            SELECT name, value
            FROM duckdb_settings()
            WHERE name IN ('memory_limit', 'threads', 'preserve_insertion_order')
            """
        ).fetchall()
    )
    return {
        "duckdb_memory_limit": settings["memory_limit"],
        "duckdb_threads": int(settings["threads"]),
        "duckdb_preserve_insertion_order": settings["preserve_insertion_order"] == "true",
    }


def _nested_value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _topic_classifications(topic_registry: dict[str, Any]) -> list[dict[str, str | None]]:
    classifications: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for raw_topic in topic_registry.get("topics", []):
        if not isinstance(raw_topic, dict):
            continue
        topic_id = _short_id(raw_topic.get("topic_id"), _TOPIC_ID)
        if topic_id is None or topic_id in seen:
            continue
        seen.add(topic_id)
        classifications.append(
            {
                "topic_id": topic_id,
                "corpus_membership": raw_topic.get("corpus_membership")
                if isinstance(raw_topic.get("corpus_membership"), str)
                else None,
                "method_family": raw_topic.get("method_family")
                if isinstance(raw_topic.get("method_family"), str)
                else None,
            }
        )
    return sorted(classifications, key=lambda row: str(row["topic_id"]))


_WORK_SCHEMA = pa.schema(
    [
        ("work_id", pa.string()),
        ("doi", pa.string()),
        ("title", pa.string()),
        ("publication_year", pa.int32()),
        ("publication_date", pa.string()),
        ("work_type", pa.string()),
        ("is_retracted", pa.bool_()),
        ("is_paratext", pa.bool_()),
        ("cited_by_count", pa.int64()),
        ("fwci", pa.float64()),
        ("primary_topic_id", pa.string()),
        ("primary_topic_name", pa.string()),
        ("topics_json", pa.string()),
        ("referenced_works_json", pa.string()),
        ("authorships_json", pa.string()),
        ("primary_location_json", pa.string()),
        ("locations_json", pa.string()),
        ("ids_json", pa.string()),
        ("source_updated_date", pa.string()),
        ("source_retrieved_at_utc", pa.string()),
        ("raw_record_hash", pa.string()),
    ]
)
_SOURCE_SCHEMA = pa.schema([("work_id", pa.string()), ("query_id", pa.string())])
_TOPIC_SCHEMA = pa.schema(
    [
        ("work_id", pa.string()),
        ("topic_id", pa.string()),
        ("topic_name", pa.string()),
        ("topic_score", pa.float64()),
        ("subfield_id", pa.string()),
        ("subfield_name", pa.string()),
        ("field_id", pa.string()),
        ("field_name", pa.string()),
        ("domain_id", pa.string()),
        ("domain_name", pa.string()),
    ]
)
_MALFORMED_SCHEMA = pa.schema(
    [
        ("record_key", pa.string()),
        ("query_id", pa.string()),
        ("cache_key", pa.string()),
        ("record_index", pa.int64()),
        ("reason", pa.string()),
        ("raw_record_hash", pa.string()),
    ]
)


def _create_staging(connection: duckdb.DuckDBPyConnection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS works (
            work_id VARCHAR PRIMARY KEY, doi VARCHAR, title VARCHAR,
            publication_year INTEGER, publication_date VARCHAR, work_type VARCHAR,
            is_retracted BOOLEAN, is_paratext BOOLEAN, cited_by_count BIGINT, fwci DOUBLE,
            primary_topic_id VARCHAR, primary_topic_name VARCHAR, topics_json VARCHAR,
            referenced_works_json VARCHAR, authorships_json VARCHAR,
            primary_location_json VARCHAR, locations_json VARCHAR, ids_json VARCHAR,
            source_updated_date VARCHAR, source_retrieved_at_utc VARCHAR,
            raw_record_hash VARCHAR
        );
        CREATE TABLE IF NOT EXISTS work_sources (
            work_id VARCHAR, query_id VARCHAR, PRIMARY KEY (work_id, query_id)
        );
        CREATE TABLE IF NOT EXISTS work_topics (
            work_id VARCHAR, topic_id VARCHAR, topic_name VARCHAR, topic_score DOUBLE,
            subfield_id VARCHAR, subfield_name VARCHAR, field_id VARCHAR, field_name VARCHAR,
            domain_id VARCHAR, domain_name VARCHAR, PRIMARY KEY (work_id, topic_id)
        );
        CREATE TABLE IF NOT EXISTS malformed (
            record_key VARCHAR PRIMARY KEY, query_id VARCHAR, cache_key VARCHAR,
            record_index BIGINT, reason VARCHAR, raw_record_hash VARCHAR
        );
        """
    )


def _insert_arrow(
    connection: duckdb.DuckDBPyConnection,
    relation_name: str,
    table_name: str,
    rows: list[dict[str, Any]],
    schema: pa.Schema,
) -> None:
    if not rows:
        return
    table = pa.Table.from_pylist(rows, schema=schema)
    connection.register(relation_name, table)
    try:
        connection.execute(f"INSERT OR IGNORE INTO {table_name} SELECT * FROM {relation_name}")
    finally:
        connection.unregister(relation_name)
    rows.clear()


def _normalize_record(
    record: Any,
    *,
    query_id: str,
    cache_key: str,
    record_index: int,
    retrieved_at: str,
    start_year: int,
    end_year: int,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any] | None]:
    record_hash = _raw_hash(record)
    record_key = hashlib.sha256(
        f"{query_id}:{cache_key}:{record_index}:{record_hash}".encode()
    ).hexdigest()
    if not isinstance(record, dict):
        return (
            None,
            [],
            {
                "record_key": record_key,
                "query_id": query_id,
                "cache_key": cache_key,
                "record_index": record_index,
                "reason": "record_not_object",
                "raw_record_hash": record_hash,
            },
        )
    work_id = _short_id(record.get("id"), _WORK_ID)
    year = record.get("publication_year")
    reason = None
    if work_id is None:
        reason = "invalid_or_missing_work_id"
    elif not isinstance(year, int):
        reason = "invalid_or_missing_publication_year"
    elif not start_year <= year <= end_year:
        reason = "publication_year_outside_analysis_window"
    if reason:
        return (
            None,
            [],
            {
                "record_key": record_key,
                "query_id": query_id,
                "cache_key": cache_key,
                "record_index": record_index,
                "reason": reason,
                "raw_record_hash": record_hash,
            },
        )
    primary_topic = record.get("primary_topic")
    raw_topics = record.get("topics")
    topics: list[Any] = raw_topics if isinstance(raw_topics, list) else []
    work = {
        "work_id": work_id,
        "doi": record.get("doi") if isinstance(record.get("doi"), str) else None,
        "title": record.get("title") if isinstance(record.get("title"), str) else None,
        "publication_year": year,
        "publication_date": record.get("publication_date"),
        "work_type": record.get("type"),
        "is_retracted": record.get("is_retracted"),
        "is_paratext": record.get("is_paratext"),
        "cited_by_count": record.get("cited_by_count"),
        "fwci": float(record["fwci"]) if isinstance(record.get("fwci"), (int, float)) else None,
        "primary_topic_id": _short_id(_nested_value(primary_topic, "id"), _TOPIC_ID),
        "primary_topic_name": _nested_value(primary_topic, "display_name"),
        "topics_json": _canonical_json(topics),
        "referenced_works_json": _canonical_json(
            sorted(
                {
                    referenced_work_id
                    for value in record.get("referenced_works") or []
                    if (referenced_work_id := _short_id(value, _WORK_ID)) is not None
                }
            )
        ),
        "authorships_json": _canonical_json(record.get("authorships") or []),
        "primary_location_json": _canonical_json(record.get("primary_location")),
        "locations_json": _canonical_json(record.get("locations") or []),
        "ids_json": _canonical_json(record.get("ids") or {}),
        "source_updated_date": record.get("updated_date"),
        "source_retrieved_at_utc": retrieved_at,
        "raw_record_hash": record_hash,
    }
    topic_rows: list[dict[str, Any]] = []
    for topic in topics:
        if not isinstance(topic, dict):
            continue
        topic_id = _short_id(topic.get("id"), _TOPIC_ID)
        if topic_id is None:
            continue
        topic_rows.append(
            {
                "work_id": work_id,
                "topic_id": topic_id,
                "topic_name": topic.get("display_name"),
                "topic_score": float(topic["score"])
                if isinstance(topic.get("score"), (int, float))
                else None,
                "subfield_id": _short_id(_nested_value(topic.get("subfield"), "id"), _HIERARCHY_ID),
                "subfield_name": _nested_value(topic.get("subfield"), "display_name"),
                "field_id": _short_id(_nested_value(topic.get("field"), "id"), _HIERARCHY_ID),
                "field_name": _nested_value(topic.get("field"), "display_name"),
                "domain_id": _short_id(_nested_value(topic.get("domain"), "id"), _HIERARCHY_ID),
                "domain_name": _nested_value(topic.get("domain"), "display_name"),
            }
        )
    return work, topic_rows, None


def normalize_raw_works(
    plan: dict[str, Any],
    cache: RawResponseCache,
    topic_registry: dict[str, Any],
    *,
    checkpoint_directory: str | Path,
    staging_path: str | Path,
    normalization_checkpoint_path: str | Path,
    output_directory: str | Path,
    start_year: int,
    end_year: int,
    resume: bool = True,
    force: bool = False,
    batch_size: int = 5000,
    duckdb_memory_limit: str = _DEFAULT_DUCKDB_MEMORY_LIMIT,
    duckdb_threads: int = _DEFAULT_DUCKDB_THREADS,
) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    stage = Path(staging_path)
    normalization_checkpoint = Path(normalization_checkpoint_path)
    classifications = _topic_classifications(topic_registry)
    logical_input_hash = semantic_hash(
        {
            "stage_version": _STAGE_VERSION,
            "plan_hash": plan.get("logical_plan_hash"),
            "topic_classifications": classifications,
            "start_year": start_year,
            "end_year": end_year,
        }
    )
    completed: list[str] = []
    if force or not resume:
        stage.unlink(missing_ok=True)
        normalization_checkpoint.unlink(missing_ok=True)
    elif normalization_checkpoint.exists():
        checkpoint = json.loads(normalization_checkpoint.read_text(encoding="utf-8"))
        if checkpoint.get("logical_input_hash") != logical_input_hash:
            raise ValueError("normalization checkpoint input hash differs from current inputs")
        completed = list(map(str, checkpoint.get("completed_query_ids", [])))
    elif stage.exists():
        raise ValueError("normalization stage exists without a checkpoint; use --force")
    stage.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(str(stage))
    resource_settings = _configure_duckdb(
        connection,
        memory_limit=duckdb_memory_limit,
        threads=duckdb_threads,
    )
    _create_staging(connection)
    completed_set = set(completed)
    page_count = 0
    raw_record_count = 0
    try:
        for query in plan["queries"]:
            query_id = str(query["query_id"])
            if query_id in completed_set:
                continue
            checkpoint_path = Path(checkpoint_directory) / f"{query_id}.json"
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if checkpoint.get("status") != "complete":
                raise ValueError(f"raw query is not complete: {query_id}")
            works: list[dict[str, Any]] = []
            sources: list[dict[str, Any]] = []
            topics: list[dict[str, Any]] = []
            malformed: list[dict[str, Any]] = []
            connection.execute("BEGIN TRANSACTION")
            try:
                for page in checkpoint["pages"]:
                    entry = cache.validate(page["cache_key"], page["checksum_sha256"])
                    records = entry.data.get("results")
                    if not isinstance(records, list):
                        raise ValueError(f"raw page lacks results: {page['cache_key']}")
                    retrieved_at = str(entry.metadata["retrieved_at_utc"])
                    page_count += 1
                    raw_record_count += len(records)
                    for index, record in enumerate(records):
                        work, topic_rows, bad = _normalize_record(
                            record,
                            query_id=query_id,
                            cache_key=str(page["cache_key"]),
                            record_index=index,
                            retrieved_at=retrieved_at,
                            start_year=start_year,
                            end_year=end_year,
                        )
                        if bad:
                            malformed.append(bad)
                        elif work:
                            works.append(work)
                            sources.append({"work_id": work["work_id"], "query_id": query_id})
                            topics.extend(topic_rows)
                        if len(works) + len(malformed) >= batch_size:
                            _insert_arrow(connection, "work_batch", "works", works, _WORK_SCHEMA)
                            _insert_arrow(
                                connection, "source_batch", "work_sources", sources, _SOURCE_SCHEMA
                            )
                            _insert_arrow(
                                connection, "topic_batch", "work_topics", topics, _TOPIC_SCHEMA
                            )
                            _insert_arrow(
                                connection, "bad_batch", "malformed", malformed, _MALFORMED_SCHEMA
                            )
                _insert_arrow(connection, "work_batch", "works", works, _WORK_SCHEMA)
                _insert_arrow(connection, "source_batch", "work_sources", sources, _SOURCE_SCHEMA)
                _insert_arrow(connection, "topic_batch", "work_topics", topics, _TOPIC_SCHEMA)
                _insert_arrow(connection, "bad_batch", "malformed", malformed, _MALFORMED_SCHEMA)
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            completed.append(query_id)
            completed_set.add(query_id)
            atomic_write_json(
                normalization_checkpoint,
                {
                    "schema_version": 1,
                    "stage_version": _STAGE_VERSION,
                    "logical_input_hash": logical_input_hash,
                    "completed_query_ids": completed,
                    "completed_query_count": len(completed),
                    "updated_at_utc": _timestamp(),
                },
            )
        summary = _export_parquet(
            connection,
            topic_classifications=classifications,
            output_directory=output_directory,
            start_year=start_year,
            end_year=end_year,
        )
    finally:
        connection.close()
    summary.update(
        {
            "schema_version": 1,
            "stage_version": _STAGE_VERSION,
            "logical_input_hash": logical_input_hash,
            "input_query_count": len(plan["queries"]),
            "processed_query_count": len(completed),
            "input_page_count_this_invocation": page_count,
            "input_record_count_this_invocation": raw_record_count,
            "generated_at_utc": _timestamp(),
            **resource_settings,
        }
    )
    return summary


def _export_parquet(
    connection: duckdb.DuckDBPyConnection,
    *,
    topic_classifications: list[dict[str, str | None]],
    output_directory: str | Path,
    start_year: int,
    end_year: int,
) -> dict[str, Any]:
    orphan_topic_count = connection.execute(
        "SELECT count(*) FROM work_topics wt ANTI JOIN works w USING (work_id)"
    ).fetchone()
    if orphan_topic_count is None or orphan_topic_count[0]:
        count = "unknown" if orphan_topic_count is None else str(orphan_topic_count[0])
        raise ValueError(f"normalization staging contains {count} orphan work-Topic rows")
    destination = Path(output_directory)
    destination.mkdir(parents=True, exist_ok=True)
    targets = {
        "works": destination / "works.parquet",
        "work_topics": destination / "work_topics.parquet",
        "work_malformed": destination / "work_malformed.parquet",
    }
    registry_schema = pa.schema(
        [
            ("topic_id", pa.string()),
            ("corpus_membership", pa.string()),
            ("method_family", pa.string()),
        ]
    )
    registry_table = pa.Table.from_pylist(topic_classifications, schema=registry_schema)
    connection.register("topic_registry", registry_table)
    queries = {
        "works": """
            SELECT
                w.* EXCLUDE (topics_json, referenced_works_json, source_updated_date),
                coalesce(t.topic_ids, []::VARCHAR[]) AS topic_ids,
                from_json(w.referenced_works_json, '["VARCHAR"]') AS referenced_work_ids,
                w.source_updated_date AS updated_date,
                q.source_query_ids
            FROM works w
            JOIN (
                SELECT work_id, list(query_id ORDER BY query_id) AS source_query_ids
                FROM work_sources GROUP BY work_id
            ) q USING (work_id)
            LEFT JOIN (
                SELECT work_id, list(topic_id ORDER BY topic_id) AS topic_ids
                FROM work_topics GROUP BY work_id
            ) t USING (work_id)
            ORDER BY work_id
        """,
        "work_topics": """
            SELECT
                wt.*,
                wt.topic_id = w.primary_topic_id AS is_primary_topic,
                r.corpus_membership,
                r.method_family
            FROM work_topics wt
            JOIN works w USING (work_id)
            LEFT JOIN topic_registry r USING (topic_id)
            ORDER BY wt.work_id, wt.topic_id
        """,
        "work_malformed": "SELECT * FROM malformed ORDER BY record_key",
    }
    try:
        for name, path in targets.items():
            temporary = path.with_suffix(".parquet.tmp")
            temporary.unlink(missing_ok=True)
            escaped = str(temporary).replace("'", "''")
            connection.execute(
                f"COPY ({queries[name]}) TO '{escaped}' "
                "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)"
            )
            if not temporary.exists():
                raise ValueError(f"Parquet export did not create {temporary}")
            parquet_count = connection.execute(
                "SELECT count(*) FROM read_parquet(?)", [str(temporary)]
            ).fetchone()
            if parquet_count is None:
                raise ValueError(f"Parquet export could not be read: {temporary}")
            expected_table = "malformed" if name == "work_malformed" else name
            expected_count_row = connection.execute(
                f"SELECT count(*) FROM {expected_table}"
            ).fetchone()
            if expected_count_row is None or parquet_count[0] != expected_count_row[0]:
                raise ValueError(f"Parquet export row count mismatch: {temporary}")
            if name == "works":
                unique_row = connection.execute(
                    "SELECT count(DISTINCT work_id) FROM read_parquet(?)", [str(temporary)]
                ).fetchone()
                if unique_row is None or unique_row[0] != parquet_count[0]:
                    raise ValueError("works Parquet contains duplicate work IDs")
            elif name == "work_topics":
                unique_row = connection.execute(
                    "SELECT count(DISTINCT (work_id, topic_id)) FROM read_parquet(?)",
                    [str(temporary)],
                ).fetchone()
                if unique_row is None or unique_row[0] != parquet_count[0]:
                    raise ValueError("work-topics Parquet contains duplicate keys")
            os.replace(temporary, path)
    finally:
        connection.unregister("topic_registry")
    work_summary = connection.execute(
        "SELECT count(*), min(publication_year), max(publication_year) FROM works"
    ).fetchone()
    source_summary = connection.execute("SELECT count(*) FROM work_sources").fetchone()
    topic_summary = connection.execute("SELECT count(*) FROM work_topics").fetchone()
    malformed_summary = connection.execute("SELECT count(*) FROM malformed").fetchone()
    if None in (work_summary, source_summary, topic_summary, malformed_summary):
        raise ValueError("normalization staging summary query returned no row")
    assert work_summary is not None
    assert source_summary is not None
    assert topic_summary is not None
    assert malformed_summary is not None
    work_count, min_year, max_year = work_summary
    source_count = source_summary[0]
    topic_count = topic_summary[0]
    malformed_count = malformed_summary[0]
    if work_count and (min_year < start_year or max_year > end_year):
        raise ValueError("normalized work years exceed configured bounds")
    return {
        "work_count": int(work_count),
        "work_topic_count": int(topic_count),
        "work_query_source_count": int(source_count),
        "duplicate_source_occurrence_count": int(source_count) - int(work_count),
        "malformed_record_count": int(malformed_count),
        "min_year": int(min_year) if min_year is not None else None,
        "max_year": int(max_year) if max_year is not None else None,
        "outputs": {name: str(path) for name, path in targets.items()},
    }


def write_normalization_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    download_plan_path: str | Path,
    command: str,
) -> None:
    from gisnet.artifacts import current_git_commit, write_json_artifact
    from gisnet.config import config_file_hash
    from gisnet.manifest import DatasetManifest

    config_hashes = {
        "project": config_file_hash(project_config_path),
        "download_plan": config_file_hash(download_plan_path),
    }
    write_json_artifact(
        path=summary_path,
        dataset_name="works_normalization_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions={"openalex_works": "retrieved-2026-08-05"},
        source_manifests=[".agent/manifests/raw_works_download_status.json"],
        command=command,
    )
    primary_keys = {
        "works": ["work_id"],
        "work_topics": ["work_id", "topic_id"],
        "work_malformed": ["record_key"],
    }
    connection = duckdb.connect()
    try:
        _configure_duckdb(
            connection,
            memory_limit=str(summary["duckdb_memory_limit"]),
            threads=int(summary["duckdb_threads"]),
        )
        for dataset_name, raw_path in summary["outputs"].items():
            path = Path(raw_path)
            description = connection.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]
            ).fetchall()
            columns = [str(row[0]) for row in description]
            quoted = [f'"{column.replace(chr(34), chr(34) * 2)}"' for column in columns]
            aggregate = ", ".join(f"count(*) FILTER (WHERE {column} IS NULL)" for column in quoted)
            null_row = connection.execute(
                f"SELECT {aggregate} FROM read_parquet(?)", [str(path)]
            ).fetchone()
            if null_row is None:
                raise ValueError(f"could not summarize Parquet nulls: {path}")
            row_count_row = connection.execute(
                "SELECT count(*) FROM read_parquet(?)", [str(path)]
            ).fetchone()
            if row_count_row is None:
                raise ValueError(f"could not summarize Parquet rows: {path}")
            min_year = max_year = None
            if "publication_year" in columns:
                year_row = connection.execute(
                    "SELECT min(publication_year), max(publication_year) FROM read_parquet(?)",
                    [str(path)],
                ).fetchone()
                if year_row:
                    min_year, max_year = year_row
            checksum = _file_sha256(path)
            manifest = DatasetManifest(
                dataset_name=dataset_name,
                created_at_utc=_timestamp(),
                run_id=run_id,
                git_commit=current_git_commit(),
                config_hashes=config_hashes,
                source_manifests=[".agent/manifests/raw_works_download_status.json"],
                source_versions={"openalex_works": "retrieved-2026-08-05"},
                row_count=int(row_count_row[0]),
                column_count=len(columns),
                primary_key=primary_keys[dataset_name],
                min_year=int(min_year) if min_year is not None else None,
                max_year=int(max_year) if max_year is not None else None,
                null_counts={
                    column: int(value) for column, value in zip(columns, null_row, strict=True)
                },
                checksum_sha256=checksum,
                command=command,
            )
            manifest.write(f".agent/manifests/{dataset_name}.json")
    finally:
        connection.close()
