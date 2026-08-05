"""Shared validation and manifest helpers for generated Parquet datasets."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import current_git_commit
from gisnet.manifest import DatasetManifest


def file_sha256(path: str | Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parquet_metrics(
    path: str | Path,
    *,
    primary_key: list[str],
    required_columns: set[str] | None = None,
    year_column: str | None = None,
    memory_limit: str = "2GB",
) -> dict[str, Any]:
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"Parquet dataset does not exist: {source}")
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = 1")
        connection.execute("SET preserve_insertion_order = false")
        description = connection.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(source)]
        ).fetchall()
        columns = [str(row[0]) for row in description]
        missing = (required_columns or set()).difference(columns)
        if missing:
            raise ValueError(f"Parquet dataset {source} lacks required columns: {sorted(missing)}")
        missing_keys = set(primary_key).difference(columns)
        if missing_keys:
            raise ValueError(f"Parquet dataset {source} lacks primary keys: {sorted(missing_keys)}")
        quoted_columns = [_quoted(column) for column in columns]
        null_aggregate = ", ".join(
            f"count(*) FILTER (WHERE {column} IS NULL)" for column in quoted_columns
        )
        null_row = connection.execute(
            f"SELECT {null_aggregate} FROM read_parquet(?)", [str(source)]
        ).fetchone()
        row_count_row = connection.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(source)]
        ).fetchone()
        if null_row is None or row_count_row is None:
            raise ValueError(f"Parquet dataset could not be summarized: {source}")
        row_count = int(row_count_row[0])
        if primary_key:
            if len(primary_key) == 1:
                key_expression = _quoted(primary_key[0])
            else:
                key_expression = f"({', '.join(_quoted(item) for item in primary_key)})"
            distinct_row = connection.execute(
                f"SELECT count(DISTINCT {key_expression}) FROM read_parquet(?)", [str(source)]
            ).fetchone()
            if distinct_row is None or int(distinct_row[0]) != row_count:
                raise ValueError(f"Parquet primary key is not unique: {source}")
        min_year = max_year = None
        if year_column is not None:
            if year_column not in columns:
                raise ValueError(f"Parquet dataset {source} lacks year column {year_column}")
            year = _quoted(year_column)
            year_row = connection.execute(
                f"SELECT min({year}), max({year}) FROM read_parquet(?)", [str(source)]
            ).fetchone()
            if year_row is not None:
                min_year, max_year = year_row
        return {
            "row_count": row_count,
            "column_count": len(columns),
            "columns": columns,
            "null_counts": {
                column: int(value) for column, value in zip(columns, null_row, strict=True)
            },
            "min_year": int(min_year) if min_year is not None else None,
            "max_year": int(max_year) if max_year is not None else None,
            "checksum_sha256": file_sha256(source),
        }
    finally:
        connection.close()


def write_parquet_manifest(
    *,
    path: str | Path,
    dataset_name: str,
    primary_key: list[str],
    required_columns: set[str],
    year_column: str | None,
    run_id: str,
    config_hashes: dict[str, str],
    source_manifests: list[str],
    source_versions: dict[str, str],
    command: str,
    manifest_path: str | Path | None = None,
) -> DatasetManifest:
    metrics = parquet_metrics(
        path,
        primary_key=primary_key,
        required_columns=required_columns,
        year_column=year_column,
    )
    manifest = DatasetManifest(
        dataset_name=dataset_name,
        created_at_utc=_timestamp(),
        run_id=run_id,
        git_commit=current_git_commit(),
        config_hashes=config_hashes,
        source_manifests=source_manifests,
        source_versions=source_versions,
        row_count=int(metrics["row_count"]),
        column_count=int(metrics["column_count"]),
        primary_key=primary_key,
        min_year=metrics["min_year"],
        max_year=metrics["max_year"],
        null_counts=dict(metrics["null_counts"]),
        checksum_sha256=str(metrics["checksum_sha256"]),
        command=command,
    )
    target = Path(manifest_path or f".agent/manifests/{dataset_name}.json")
    manifest.write(str(target))
    return manifest


def _quoted(identifier: str) -> str:
    return f'"{identifier.replace(chr(34), chr(34) * 2)}"'


def _timestamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
