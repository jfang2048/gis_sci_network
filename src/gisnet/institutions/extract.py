"""Stream normalized Work authorships into distinct work-institution assertions."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_INSTITUTION_ID = re.compile(r"^I\d+$")
_STAGE_VERSION = "work-institution-extraction-2026-08-05-v1"

_EXTRACTED_SCHEMA = pa.schema(
    [
        ("work_id", pa.string()),
        ("publication_year", pa.int32()),
        ("institution_id", pa.string()),
        ("ror_id", pa.string()),
        ("display_name", pa.string()),
        ("country_code", pa.string()),
        ("institution_type", pa.string()),
        ("lineage", pa.list_(pa.string())),
        ("raw_affiliation_strings", pa.list_(pa.string())),
        ("authorship_count", pa.int32()),
        ("assertion_count", pa.int32()),
    ]
)
_UNRESOLVED_SCHEMA = pa.schema(
    [
        ("work_id", pa.string()),
        ("publication_year", pa.int32()),
        ("authorship_count", pa.int32()),
        ("raw_affiliation_strings", pa.list_(pa.string())),
        ("reason", pa.string()),
    ]
)


def extract_work_institutions(
    works_path: str | Path,
    *,
    extracted_path: str | Path,
    unresolved_path: str | Path,
    start_year: int,
    end_year: int,
    batch_size: int = 2000,
    force: bool = False,
) -> dict[str, Any]:
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    source = Path(works_path)
    extracted = Path(extracted_path)
    unresolved = Path(unresolved_path)
    for destination in (extracted, unresolved):
        destination.parent.mkdir(parents=True, exist_ok=True)
    extracted_temporary = extracted.with_suffix(".parquet.tmp")
    unresolved_temporary = unresolved.with_suffix(".parquet.tmp")
    if force:
        extracted_temporary.unlink(missing_ok=True)
        unresolved_temporary.unlink(missing_ok=True)
    logical_input_hash = semantic_hash(
        {
            "stage_version": _STAGE_VERSION,
            "works_sha256": file_sha256(source),
            "start_year": start_year,
            "end_year": end_year,
        }
    )
    input_work_count = 0
    extracted_row_count = 0
    resolved_work_count = 0
    unresolved_work_count = 0
    institution_ids: set[str] = set()
    extracted_rows: list[dict[str, Any]] = []
    unresolved_rows: list[dict[str, Any]] = []
    source_file = pq.ParquetFile(source)
    extracted_temporary.unlink(missing_ok=True)
    unresolved_temporary.unlink(missing_ok=True)
    extracted_writer = pq.ParquetWriter(extracted_temporary, _EXTRACTED_SCHEMA, compression="zstd")
    unresolved_writer = pq.ParquetWriter(
        unresolved_temporary, _UNRESOLVED_SCHEMA, compression="zstd"
    )
    try:
        for batch in source_file.iter_batches(
            batch_size=batch_size,
            columns=["work_id", "publication_year", "authorships_json"],
        ):
            for work_id, year, authorships_json in zip(
                batch.column(0).to_pylist(),
                batch.column(1).to_pylist(),
                batch.column(2).to_pylist(),
                strict=True,
            ):
                input_work_count += 1
                if not isinstance(year, int) or not start_year <= year <= end_year:
                    raise ValueError(f"Work {work_id} has an invalid publication year: {year}")
                work_rows, unresolved_row = _extract_work(str(work_id), year, str(authorships_json))
                if work_rows:
                    resolved_work_count += 1
                    extracted_row_count += len(work_rows)
                    institution_ids.update(str(row["institution_id"]) for row in work_rows)
                    extracted_rows.extend(work_rows)
                elif unresolved_row is not None:
                    unresolved_work_count += 1
                    unresolved_rows.append(unresolved_row)
                if len(extracted_rows) >= batch_size:
                    _write_rows(extracted_writer, extracted_rows, _EXTRACTED_SCHEMA)
                if len(unresolved_rows) >= batch_size:
                    _write_rows(unresolved_writer, unresolved_rows, _UNRESOLVED_SCHEMA)
        _write_rows(extracted_writer, extracted_rows, _EXTRACTED_SCHEMA)
        _write_rows(unresolved_writer, unresolved_rows, _UNRESOLVED_SCHEMA)
    except BaseException:
        extracted_writer.close()
        unresolved_writer.close()
        extracted_temporary.unlink(missing_ok=True)
        unresolved_temporary.unlink(missing_ok=True)
        raise
    else:
        extracted_writer.close()
        unresolved_writer.close()
    extracted_metrics = parquet_metrics(
        extracted_temporary,
        primary_key=["work_id", "institution_id"],
        required_columns=set(_EXTRACTED_SCHEMA.names),
        year_column="publication_year",
    )
    unresolved_metrics = parquet_metrics(
        unresolved_temporary,
        primary_key=["work_id"],
        required_columns=set(_UNRESOLVED_SCHEMA.names),
        year_column="publication_year",
    )
    if extracted_metrics["row_count"] != extracted_row_count:
        raise ValueError("extracted institution row count did not reconcile")
    if unresolved_metrics["row_count"] != unresolved_work_count:
        raise ValueError("unresolved institution row count did not reconcile")
    if resolved_work_count + unresolved_work_count != input_work_count:
        raise ValueError("resolved and unresolved Work counts did not reconcile")
    os.replace(extracted_temporary, extracted)
    os.replace(unresolved_temporary, unresolved)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": logical_input_hash,
        "input_work_count": input_work_count,
        "resolved_work_count": resolved_work_count,
        "unresolved_work_count": unresolved_work_count,
        "work_institution_count": extracted_row_count,
        "distinct_institution_count": len(institution_ids),
        "min_year": extracted_metrics["min_year"],
        "max_year": extracted_metrics["max_year"],
        "outputs": {
            "work_institutions_extracted": str(extracted),
            "work_institutions_unresolved": str(unresolved),
        },
        "generated_at_utc": _timestamp(),
    }


def _extract_work(
    work_id: str, publication_year: int, authorships_json: str
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    try:
        decoded = json.loads(authorships_json)
    except json.JSONDecodeError:
        return [], _unresolved(work_id, publication_year, 0, [], "invalid_authorships_json")
    if not isinstance(decoded, list):
        return [], _unresolved(work_id, publication_year, 0, [], "authorships_not_array")
    institutions: dict[str, dict[str, Any]] = {}
    all_affiliations: set[str] = set()
    for authorship_index, authorship in enumerate(decoded):
        if not isinstance(authorship, dict):
            continue
        affiliations = _raw_affiliations(authorship)
        all_affiliations.update(affiliations)
        raw_institutions = authorship.get("institutions")
        if not isinstance(raw_institutions, list):
            continue
        seen_in_authorship: set[str] = set()
        for assertion in raw_institutions:
            if not isinstance(assertion, dict):
                continue
            institution_id = _short_id(assertion.get("id"))
            if institution_id is None:
                continue
            row = institutions.setdefault(
                institution_id,
                {
                    "work_id": work_id,
                    "publication_year": publication_year,
                    "institution_id": institution_id,
                    "ror_id": _string(assertion.get("ror")),
                    "display_name": _string(assertion.get("display_name")),
                    "country_code": _country_code(assertion.get("country_code")),
                    "institution_type": _string(assertion.get("type")),
                    "lineage": set(_lineage(assertion.get("lineage"))),
                    "raw_affiliation_strings": set(),
                    "authorship_indexes": set(),
                    "assertion_count": 0,
                },
            )
            _fill_missing_metadata(row, assertion)
            row["lineage"].update(_lineage(assertion.get("lineage")))
            row["raw_affiliation_strings"].update(affiliations)
            row["authorship_indexes"].add(authorship_index)
            row["assertion_count"] += 1
            seen_in_authorship.add(institution_id)
        if not seen_in_authorship:
            continue
    rows = []
    for institution_id in sorted(institutions):
        value = institutions[institution_id]
        rows.append(
            {
                "work_id": value["work_id"],
                "publication_year": value["publication_year"],
                "institution_id": value["institution_id"],
                "ror_id": value["ror_id"],
                "display_name": value["display_name"],
                "country_code": value["country_code"],
                "institution_type": value["institution_type"],
                "lineage": sorted(value["lineage"]),
                "raw_affiliation_strings": sorted(value["raw_affiliation_strings"]),
                "authorship_count": len(value["authorship_indexes"]),
                "assertion_count": int(value["assertion_count"]),
            }
        )
    if rows:
        return rows, None
    reason = "no_authorships" if not decoded else "no_resolved_institution"
    return [], _unresolved(
        work_id, publication_year, len(decoded), sorted(all_affiliations), reason
    )


def _fill_missing_metadata(row: dict[str, Any], assertion: dict[str, Any]) -> None:
    values = {
        "ror_id": _string(assertion.get("ror")),
        "display_name": _string(assertion.get("display_name")),
        "country_code": _country_code(assertion.get("country_code")),
        "institution_type": _string(assertion.get("type")),
    }
    for key, value in values.items():
        if row[key] is None and value is not None:
            row[key] = value


def _raw_affiliations(authorship: dict[str, Any]) -> set[str]:
    values: set[str] = set()
    for value in authorship.get("raw_affiliation_strings") or []:
        if isinstance(value, str) and value.strip():
            values.add(value.strip())
    for affiliation in authorship.get("affiliations") or []:
        if isinstance(affiliation, dict):
            value = affiliation.get("raw_affiliation_string")
            if isinstance(value, str) and value.strip():
                values.add(value.strip())
    return values


def _lineage(value: Any) -> Iterable[str]:
    if not isinstance(value, list):
        return []
    return [identifier for item in value if (identifier := _short_id(item)) is not None]


def _short_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    identifier = value.rstrip("/").rsplit("/", 1)[-1]
    return identifier if _INSTITUTION_ID.fullmatch(identifier) else None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _country_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.upper()
    return normalized if len(normalized) == 2 and normalized.isalpha() else None


def _unresolved(
    work_id: str,
    publication_year: int,
    authorship_count: int,
    raw_affiliation_strings: list[str],
    reason: str,
) -> dict[str, Any]:
    return {
        "work_id": work_id,
        "publication_year": publication_year,
        "authorship_count": authorship_count,
        "raw_affiliation_strings": raw_affiliation_strings,
        "reason": reason,
    }


def _write_rows(writer: pq.ParquetWriter, rows: list[dict[str, Any]], schema: pa.Schema) -> None:
    if not rows:
        return
    writer.write_table(pa.Table.from_pylist(rows, schema=schema))
    rows.clear()


def write_extraction_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [".agent/manifests/works.json"]
    source_versions = {"openalex_works": "retrieved-2026-08-05"}
    write_json_artifact(
        path=summary_path,
        dataset_name="institution_extraction_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions=source_versions,
        source_manifests=source_manifests,
        command=command,
    )
    definitions = {
        "work_institutions_extracted": (
            ["work_id", "institution_id"],
            set(_EXTRACTED_SCHEMA.names),
        ),
        "work_institutions_unresolved": (["work_id"], set(_UNRESOLVED_SCHEMA.names)),
    }
    for dataset_name, raw_path in summary["outputs"].items():
        primary_key, required_columns = definitions[dataset_name]
        write_parquet_manifest(
            path=raw_path,
            dataset_name=dataset_name,
            primary_key=primary_key,
            required_columns=required_columns,
            year_column="publication_year",
            run_id=run_id,
            config_hashes=config_hashes,
            source_manifests=source_manifests,
            source_versions=source_versions,
            command=command,
        )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
