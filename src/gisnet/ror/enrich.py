"""Optional stable-ID ROR enrichment with a shared API/dump normalizer."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.atomic import atomic_write_json
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "institution-ror-2026-08-06-v2"
_ROR_API = "https://api.ror.org/v2/organizations"
_ROR_ID = re.compile(r"^[0-9a-hj-km-np-tv-z]{9}$")
_ENRICHMENT_FIELDS = [
    pa.field("ror_record_id", pa.string()),
    pa.field("ror_display_name", pa.string()),
    pa.field("ror_alternative_names", pa.list_(pa.string())),
    pa.field("ror_acronyms", pa.list_(pa.string())),
    pa.field("ror_types", pa.list_(pa.string())),
    pa.field("ror_status", pa.string()),
    pa.field("ror_country_code", pa.string()),
    pa.field("ror_country_name", pa.string()),
    pa.field("ror_city_name", pa.string()),
    pa.field("ror_latitude", pa.float64()),
    pa.field("ror_longitude", pa.float64()),
    pa.field("ror_parent_ids", pa.list_(pa.string())),
    pa.field("ror_child_ids", pa.list_(pa.string())),
    pa.field("ror_related_ids", pa.list_(pa.string())),
    pa.field("ror_predecessor_ids", pa.list_(pa.string())),
    pa.field("ror_successor_ids", pa.list_(pa.string())),
    pa.field("ror_schema_version", pa.string()),
    pa.field("ror_last_modified_date", pa.string()),
    pa.field("ror_enrichment_status", pa.string()),
    pa.field("ror_enrichment_source", pa.string()),
]
_QA_SCHEMA = pa.schema(
    [
        ("institution_id", pa.string()),
        ("ror_id", pa.string()),
        ("issue_fields", pa.list_(pa.string())),
        ("openalex_display_name", pa.string()),
        ("ror_display_name", pa.string()),
        ("openalex_country_code", pa.string()),
        ("ror_country_code", pa.string()),
        ("openalex_institution_type", pa.string()),
        ("ror_types", pa.list_(pa.string())),
        ("enrichment_status", pa.string()),
    ]
)

RorMode = Literal["cache", "api", "dump"]


def normalize_ror_id(value: Any) -> str | None:
    """Return the canonical nine-character ROR identifier, never a name-derived guess."""
    if not isinstance(value, str):
        return None
    identifier = value.strip().rstrip("/").rsplit("/", 1)[-1].lower()
    return identifier if _ROR_ID.fullmatch(identifier) else None


def normalize_ror_record(record: dict[str, Any]) -> dict[str, Any]:
    """Normalize one ROR v2 record identically regardless of its transport."""
    identifier = normalize_ror_id(record.get("id"))
    if identifier is None:
        raise ValueError("ROR record has no valid stable identifier")
    raw_names = record.get("names")
    names: list[Any] = raw_names if isinstance(raw_names, list) else []
    display_name = None
    alternatives: set[str] = set()
    acronyms: set[str] = set()
    for raw_name in names:
        if not isinstance(raw_name, dict):
            continue
        value = _string(raw_name.get("value"))
        types = set(_strings(raw_name.get("types")))
        if value is None:
            continue
        if "ror_display" in types:
            display_name = value
        elif "acronym" in types:
            acronyms.add(value)
        else:
            alternatives.add(value)
    if display_name is None:
        display_name = min(alternatives | acronyms, default=None)
    alternatives.discard(display_name)
    raw_locations = record.get("locations")
    locations: list[Any] = raw_locations if isinstance(raw_locations, list) else []
    location = next((item for item in locations if isinstance(item, dict)), {})
    details = location.get("geonames_details") if isinstance(location, dict) else {}
    if not isinstance(details, dict):
        details = {}
    relationships: dict[str, set[str]] = {
        "parent": set(),
        "child": set(),
        "related": set(),
        "predecessor": set(),
        "successor": set(),
    }
    raw_relationships = record.get("relationships")
    if isinstance(raw_relationships, list):
        for relationship in raw_relationships:
            if not isinstance(relationship, dict):
                continue
            relation_type = relationship.get("type")
            related_id = normalize_ror_id(relationship.get("id"))
            if isinstance(relation_type, str) and relation_type in relationships and related_id:
                relationships[relation_type].add(related_id)
    admin = record.get("admin") if isinstance(record.get("admin"), dict) else {}
    modified = admin.get("last_modified") if isinstance(admin, dict) else {}
    if not isinstance(modified, dict):
        modified = {}
    return {
        "ror_record_id": identifier,
        "ror_display_name": display_name,
        "ror_alternative_names": sorted(alternatives),
        "ror_acronyms": sorted(acronyms),
        "ror_types": sorted(_strings(record.get("types"))),
        "ror_status": _string(record.get("status")),
        "ror_country_code": _country_code(details.get("country_code")),
        "ror_country_name": _string(details.get("country_name")),
        "ror_city_name": _string(details.get("name")),
        "ror_latitude": _number(details.get("lat")),
        "ror_longitude": _number(details.get("lng")),
        "ror_parent_ids": sorted(relationships["parent"]),
        "ror_child_ids": sorted(relationships["child"]),
        "ror_related_ids": sorted(relationships["related"]),
        "ror_predecessor_ids": sorted(relationships["predecessor"]),
        "ror_successor_ids": sorted(relationships["successor"]),
        "ror_schema_version": _string(modified.get("schema_version")),
        "ror_last_modified_date": _string(modified.get("date")),
    }


def enrich_institutions_with_ror(
    institutions_path: str | Path,
    *,
    output_path: str | Path,
    qa_path: str | Path,
    cache_directory: str | Path,
    mode: RorMode = "cache",
    dump_path: str | Path | None = None,
    dump_version: str | None = None,
    max_lookups: int = 0,
    timeout_seconds: float = 30.0,
) -> dict[str, Any]:
    """Enrich every institution while allowing intentionally partial ROR retrieval."""
    if mode not in {"cache", "api", "dump"}:
        raise ValueError(f"unsupported ROR mode: {mode}")
    if max_lookups < 0:
        raise ValueError("max_lookups must be non-negative")
    if mode == "dump" and dump_path is None:
        raise ValueError("dump mode requires dump_path")
    source = Path(institutions_path)
    table = pq.read_table(source)
    cache_root = Path(cache_directory)
    dump_records = _load_dump(Path(dump_path)) if dump_path is not None else {}
    requested_ids = sorted(
        {
            identifier
            for value in table.column("ror_id").to_pylist()
            if (identifier := normalize_ror_id(value)) is not None
        }
    )
    records: dict[str, tuple[dict[str, Any], str]] = {}
    for identifier in requested_ids:
        cached = _read_cache(cache_root, identifier)
        if cached is not None:
            records[identifier] = (cached, "cache")
        elif identifier in dump_records:
            records[identifier] = (dump_records[identifier], "dump")
    api_requested = api_found = api_failed = 0
    if mode == "api" and max_lookups:
        missing = [identifier for identifier in requested_ids if identifier not in records]
        targets = missing[:max_lookups]
        api_requested = len(targets)
        headers = {"User-Agent": "gis-sci-network/0.1 (public research pipeline)"}
        client_id = os.environ.get("ROR_CLIENT_ID")
        if client_id:
            headers["Client-Id"] = client_id
        with httpx.Client(
            timeout=timeout_seconds, headers=headers, follow_redirects=True
        ) as client:
            for identifier in targets:
                try:
                    response = client.get(f"{_ROR_API}/{identifier}")
                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise ValueError("ROR response was not an object")
                    normalized = normalize_ror_record(payload)
                    if normalized["ror_record_id"] != identifier:
                        raise ValueError("ROR response identifier did not match request")
                    _write_cache(cache_root, identifier, payload)
                    records[identifier] = (payload, "api")
                    api_found += 1
                except (httpx.HTTPError, json.JSONDecodeError, ValueError):
                    api_failed += 1
    rows: list[dict[str, Any]] = []
    qa_rows: list[dict[str, Any]] = []
    status_counts: dict[str, int] = {}
    schema_versions: set[str] = set()
    coordinate_fallback_count = 0
    for raw in table.to_pylist():
        institution_id = str(raw["institution_id"])
        identifier = normalize_ror_id(raw.get("ror_id"))
        record_entry = records.get(identifier) if identifier else None
        if identifier is None:
            status = "missing_ror_id"
            normalized = _empty_normalized()
            enrichment_source = None
        elif record_entry is None:
            status = "not_retrieved"
            normalized = _empty_normalized()
            enrichment_source = None
        else:
            record, _transport = record_entry
            try:
                normalized = normalize_ror_record(record)
                status = "enriched"
                enrichment_source = "ror"
            except ValueError:
                normalized = _empty_normalized()
                status = "invalid_record"
        version = normalized.get("ror_schema_version")
        if isinstance(version, str):
            schema_versions.add(version)
        status_counts[status] = status_counts.get(status, 0) + 1
        latitude = _number(raw.get("latitude"))
        longitude = _number(raw.get("longitude"))
        coordinate_source = _string(raw.get("coordinate_source"))
        ror_latitude = _number(normalized.get("ror_latitude"))
        ror_longitude = _number(normalized.get("ror_longitude"))
        if (latitude is None or longitude is None) and (
            ror_latitude is not None and ror_longitude is not None
        ):
            latitude = ror_latitude
            longitude = ror_longitude
            coordinate_source = "ror"
            coordinate_fallback_count += 1
        issues = _conflicts(raw, normalized, status)
        if issues:
            qa_rows.append(
                {
                    "institution_id": institution_id,
                    "ror_id": identifier,
                    "issue_fields": issues,
                    "openalex_display_name": _string(raw.get("display_name")),
                    "ror_display_name": normalized.get("ror_display_name"),
                    "openalex_country_code": _country_code(
                        raw.get("openalex_country_code", raw.get("country_code"))
                    ),
                    "ror_country_code": normalized.get("ror_country_code"),
                    "openalex_institution_type": _string(raw.get("institution_type")),
                    "ror_types": normalized.get("ror_types", []),
                    "enrichment_status": status,
                }
            )
        rows.append(
            {
                **raw,
                "latitude": latitude,
                "longitude": longitude,
                "coordinate_source": coordinate_source,
                **normalized,
                "ror_enrichment_status": status,
                "ror_enrichment_source": enrichment_source,
            }
        )
    output = Path(output_path)
    qa = Path(qa_path)
    input_fields = list(table.schema)
    if "coordinate_source" not in table.schema.names:
        input_fields.append(pa.field("coordinate_source", pa.string()))
    output_schema = pa.schema([*input_fields, *_ENRICHMENT_FIELDS])
    _write_atomic(rows, output_schema, output)
    _write_atomic(qa_rows, _QA_SCHEMA, qa)
    output_metrics = parquet_metrics(
        output, primary_key=["institution_id"], required_columns=set(output_schema.names)
    )
    parquet_metrics(qa, primary_key=["institution_id"], required_columns=set(_QA_SCHEMA.names))
    if int(output_metrics["row_count"]) != table.num_rows:
        raise ValueError("ROR enrichment row count did not reconcile")
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "institutions_sha256": file_sha256(source),
                "mode": mode,
                "dump_sha256": file_sha256(dump_path) if dump_path is not None else None,
                "dump_version": dump_version,
                "max_lookups": max_lookups,
            }
        ),
        "institution_count": len(rows),
        "unique_valid_ror_id_count": len(requested_ids),
        "record_count": len(records),
        "api_requested_count": api_requested,
        "api_found_count": api_found,
        "api_failed_count": api_failed,
        "status_counts": dict(sorted(status_counts.items())),
        "ror_coordinate_count": sum(
            row["ror_latitude"] is not None and row["ror_longitude"] is not None for row in rows
        ),
        "coordinate_fallback_count": coordinate_fallback_count,
        "resolved_coordinate_count": sum(_has_coordinate_pair(row) for row in rows),
        "missing_resolved_coordinate_count": sum(not _has_coordinate_pair(row) for row in rows),
        "partial_resolved_coordinate_count": sum(_has_partial_coordinate_pair(row) for row in rows),
        "coordinate_source_counts": dict(
            sorted(
                {
                    source: sum(
                        row.get("coordinate_source") == source and _has_coordinate_pair(row)
                        for row in rows
                    )
                    for source in {
                        str(row["coordinate_source"])
                        for row in rows
                        if row.get("coordinate_source") is not None
                    }
                }.items()
            )
        ),
        "conflict_or_missing_qa_count": len(qa_rows),
        "ror_schema_versions": sorted(schema_versions),
        "ror_dump_version": dump_version,
        "mode": mode,
        "outputs": {
            "institutions_ror": str(output),
            "institution_ror_qa": str(qa),
        },
        "generated_at_utc": _timestamp(),
    }


def _load_dump(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"ROR dump does not exist: {path}")
    with path.open(encoding="utf-8") as handle:
        first = handle.read(1)
        handle.seek(0)
        if first == "[":
            payload = json.load(handle)
            values = payload if isinstance(payload, list) else []
        else:
            values = [json.loads(line) for line in handle if line.strip()]
    records: dict[str, dict[str, Any]] = {}
    for raw in values:
        if not isinstance(raw, dict):
            continue
        identifier = normalize_ror_id(raw.get("id"))
        if identifier is not None:
            records[identifier] = raw
    return records


def _cache_path(root: Path, identifier: str) -> Path:
    return root / f"{identifier}.json"


def _read_cache(root: Path, identifier: str) -> dict[str, Any] | None:
    path = _cache_path(root, identifier)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    try:
        normalized = normalize_ror_record(payload)
    except ValueError:
        return None
    return payload if normalized["ror_record_id"] == identifier else None


def _write_cache(root: Path, identifier: str, payload: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_json(_cache_path(root, identifier), payload)


def _empty_normalized() -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in _ENRICHMENT_FIELDS:
        if field.name in {"ror_enrichment_status", "ror_enrichment_source"}:
            continue
        values[field.name] = [] if pa.types.is_list(field.type) else None
    return values


def _conflicts(raw: dict[str, Any], normalized: dict[str, Any], status: str) -> list[str]:
    issues: list[str] = []
    if status != "enriched":
        issues.append(status)
    source_country = _country_code(raw.get("openalex_country_code", raw.get("country_code")))
    ror_country = _country_code(normalized.get("ror_country_code"))
    if source_country and ror_country and source_country != ror_country:
        issues.append("country_conflict")
    source_name = _string(raw.get("display_name"))
    ror_name = _string(normalized.get("ror_display_name"))
    if source_name and ror_name and source_name.casefold() != ror_name.casefold():
        issues.append("display_name_difference")
    return sorted(set(issues))


def _write_atomic(rows: list[dict[str, Any]], schema: pa.Schema, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".parquet.tmp")
    temporary.unlink(missing_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), temporary, compression="zstd")
    parquet_metrics(temporary, primary_key=[schema.names[0]], required_columns=set(schema.names))
    os.replace(temporary, path)


def _has_coordinate_pair(row: dict[str, Any]) -> bool:
    return row.get("latitude") is not None and row.get("longitude") is not None


def _has_partial_coordinate_pair(row: dict[str, Any]) -> bool:
    return (row.get("latitude") is None) != (row.get("longitude") is None)


def write_ror_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_versions = {
        "institution_ror_policy": _STAGE_VERSION,
        "ror_schema": ",".join(summary["ror_schema_versions"]) or "not-retrieved",
        "ror_dump": summary.get("ror_dump_version") or "not-used",
    }
    source_manifests = [".agent/manifests/institutions_geographic.json"]
    write_json_artifact(
        path=summary_path,
        dataset_name="institution_ror_summary",
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
        "institutions_ror": (["institution_id"], {"institution_id", "ror_enrichment_status"}),
        "institution_ror_qa": (["institution_id"], set(_QA_SCHEMA.names)),
    }
    for dataset_name, raw_path in summary["outputs"].items():
        primary_key, required = definitions[dataset_name]
        write_parquet_manifest(
            path=raw_path,
            dataset_name=dataset_name,
            primary_key=primary_key,
            required_columns=required,
            year_column=None,
            run_id=run_id,
            config_hashes=config_hashes,
            source_manifests=source_manifests,
            source_versions=source_versions,
            command=command,
        )


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _strings(value: Any) -> list[str]:
    return (
        [item for item in value if isinstance(item, str) and item]
        if isinstance(value, list)
        else []
    )


def _country_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.upper()
    return normalized if len(normalized) == 2 and normalized.isalpha() else None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
