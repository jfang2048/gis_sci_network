"""Build a stable-ID institution master from Work assertions and cached source metadata."""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.corpus.topics import cached_get
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest
from gisnet.institutions.types import InstitutionTypePolicy
from gisnet.openalex.cache import RawResponseCache
from gisnet.openalex.client import OpenAlexClient, OpenAlexError

_STAGE_VERSION = "institution-master-2026-08-06-v2"
_SELECT = (
    "id,ror,display_name,display_name_acronyms,display_name_alternatives,country_code,type,"
    "lineage,geo,associated_institutions,updated_date"
)

_MASTER_SCHEMA = pa.schema(
    [
        ("institution_id", pa.string()),
        ("ror_id", pa.string()),
        ("display_name", pa.string()),
        ("alternative_names", pa.list_(pa.string())),
        ("country_code", pa.string()),
        ("institution_type", pa.string()),
        ("normalized_category", pa.string()),
        ("analytical_scope", pa.string()),
        ("is_primary_research_scope", pa.bool_()),
        ("latitude", pa.float64()),
        ("longitude", pa.float64()),
        ("coordinate_source", pa.string()),
        ("lineage", pa.list_(pa.string())),
        ("parent_ids", pa.list_(pa.string())),
        ("child_ids", pa.list_(pa.string())),
        ("predecessor_ids", pa.list_(pa.string())),
        ("successor_ids", pa.list_(pa.string())),
        ("canonical_institution_id", pa.string()),
        ("canonicalization_rule_id", pa.string()),
        ("openalex_updated_date", pa.string()),
        ("ror_version", pa.string()),
        ("metadata_source", pa.string()),
        ("source_ror_ids", pa.list_(pa.string())),
        ("source_display_names", pa.list_(pa.string())),
        ("source_country_codes", pa.list_(pa.string())),
        ("source_institution_types", pa.list_(pa.string())),
        ("assertion_work_count", pa.int64()),
    ]
)
_QA_SCHEMA = pa.schema(
    [
        ("institution_id", pa.string()),
        ("issue_fields", pa.list_(pa.string())),
        ("source_ror_ids", pa.list_(pa.string())),
        ("source_display_names", pa.list_(pa.string())),
        ("source_country_codes", pa.list_(pa.string())),
        ("source_institution_types", pa.list_(pa.string())),
        ("lookup_status", pa.string()),
        ("lookup_resolved_fields", pa.list_(pa.string())),
    ]
)


def build_institution_master(
    extracted_path: str | Path,
    policy: InstitutionTypePolicy,
    *,
    master_path: str | Path,
    qa_path: str | Path,
    client: OpenAlexClient | None = None,
    cache: RawResponseCache | None = None,
    lookup_batch_size: int = 25,
    force: bool = False,
) -> dict[str, Any]:
    if not 1 <= lookup_batch_size <= 50:
        raise ValueError("lookup_batch_size must be between 1 and 50")
    source = Path(extracted_path)
    aggregates = _aggregate_assertions(source)
    targets = sorted(aggregates)
    lookup_results: dict[str, tuple[dict[str, Any], str]] = {}
    if cache is not None and not force:
        lookup_results.update(_cached_institution_records(cache, set(targets)))
    cache_found_count = len(lookup_results)
    lookup_failure_count = 0
    if client is not None:
        if cache is None:
            raise ValueError("OpenAlex lookups require a raw response cache")
        needed = [
            institution_id for institution_id in targets if institution_id not in lookup_results
        ]
        for batch in _batches(needed, lookup_batch_size):
            parameters = {
                "filter": f"openalex:{'|'.join(batch)}",
                "select": _SELECT,
                "per-page": 200,
            }
            try:
                data, retrieved_at = cached_get(
                    client, cache, "/institutions", parameters, force=force
                )
            except OpenAlexError:
                lookup_failure_count += len(batch)
                continue
            results = data.get("results")
            if not isinstance(results, list):
                lookup_failure_count += len(batch)
                continue
            for raw in results:
                if not isinstance(raw, dict):
                    continue
                institution_id = _short_id(raw.get("id"), "I")
                if institution_id is not None:
                    lookup_results[institution_id] = (raw, retrieved_at)
    master_rows: list[dict[str, Any]] = []
    qa_rows: list[dict[str, Any]] = []
    for institution_id in sorted(aggregates):
        aggregate = aggregates[institution_id]
        issues = _issue_fields(aggregate)
        lookup = lookup_results.get(institution_id)
        row, resolved_fields = _master_row(institution_id, aggregate, lookup, policy)
        master_rows.append(row)
        if issues or (client is not None and lookup is None):
            if not issues:
                issues = ["missing_openalex_metadata"]
            qa_rows.append(
                {
                    "institution_id": institution_id,
                    "issue_fields": issues,
                    "source_ror_ids": sorted(aggregate["ror_ids"]),
                    "source_display_names": sorted(aggregate["display_names"]),
                    "source_country_codes": sorted(aggregate["country_codes"]),
                    "source_institution_types": sorted(aggregate["institution_types"]),
                    "lookup_status": "found"
                    if lookup is not None
                    else ("offline" if client is None else "not_found_or_failed"),
                    "lookup_resolved_fields": resolved_fields,
                }
            )
    master = Path(master_path)
    qa = Path(qa_path)
    _write_atomic_parquet(master_rows, _MASTER_SCHEMA, master)
    _write_atomic_parquet(qa_rows, _QA_SCHEMA, qa)
    master_metrics = parquet_metrics(
        master,
        primary_key=["institution_id"],
        required_columns=set(_MASTER_SCHEMA.names),
    )
    qa_metrics = parquet_metrics(
        qa,
        primary_key=["institution_id"],
        required_columns=set(_QA_SCHEMA.names),
    )
    if int(master_metrics["row_count"]) != len(aggregates):
        raise ValueError("institution master row count did not reconcile")
    logical_input_hash = semantic_hash(
        {
            "stage_version": _STAGE_VERSION,
            "extracted_sha256": file_sha256(source),
            "policy_version": policy.policy_version,
        }
    )
    retrieval_times = sorted(
        retrieved_at for _record, retrieved_at in lookup_results.values() if retrieved_at
    )
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": logical_input_hash,
        "institution_count": len(master_rows),
        "metadata_qa_count": len(qa_rows),
        "lookup_requested_count": len(targets),
        "lookup_cache_found_count": cache_found_count,
        "lookup_network_target_count": (
            len(targets) - cache_found_count if client is not None else 0
        ),
        "lookup_found_count": len(lookup_results),
        "lookup_failure_or_missing_count": len(targets) - len(lookup_results),
        "lookup_batch_failure_id_count": lookup_failure_count,
        "lookup_retrieved_at_min": retrieval_times[0] if retrieval_times else None,
        "lookup_retrieved_at_max": retrieval_times[-1] if retrieval_times else None,
        "missing_country_count": sum(row["country_code"] is None for row in master_rows),
        "missing_type_count": sum(row["institution_type"] is None for row in master_rows),
        "missing_ror_count": sum(row["ror_id"] is None for row in master_rows),
        "coordinate_count": sum(_has_coordinate_pair(row) for row in master_rows),
        "missing_coordinate_count": sum(not _has_coordinate_pair(row) for row in master_rows),
        "partial_coordinate_count": sum(_has_partial_coordinate_pair(row) for row in master_rows),
        "coordinate_source_counts": dict(
            sorted(
                Counter(
                    str(row["coordinate_source"])
                    for row in master_rows
                    if row["coordinate_source"] is not None and _has_coordinate_pair(row)
                ).items()
            )
        ),
        "outputs": {"institutions": str(master), "institution_metadata_qa": str(qa)},
        "generated_at_utc": _timestamp(),
        "qa_row_count": int(qa_metrics["row_count"]),
    }


def _aggregate_assertions(path: Path) -> dict[str, dict[str, Any]]:
    aggregates: dict[str, dict[str, Any]] = {}
    source = pq.ParquetFile(path)
    columns = [
        "institution_id",
        "ror_id",
        "display_name",
        "country_code",
        "institution_type",
        "lineage",
    ]
    for batch in source.iter_batches(batch_size=5000, columns=columns):
        for values in zip(
            *(batch.column(index).to_pylist() for index in range(len(columns))), strict=True
        ):
            institution_id, ror_id, display_name, country_code, institution_type, lineage = values
            aggregate = aggregates.setdefault(
                str(institution_id),
                {
                    "ror_counts": Counter(),
                    "display_name_counts": Counter(),
                    "country_code_counts": Counter(),
                    "institution_type_counts": Counter(),
                    "ror_ids": set(),
                    "display_names": set(),
                    "country_codes": set(),
                    "institution_types": set(),
                    "lineage": set(),
                    "assertion_work_count": 0,
                },
            )
            _count_value(aggregate, "ror", ror_id)
            _count_value(aggregate, "display_name", display_name)
            _count_value(aggregate, "country_code", country_code)
            _count_value(aggregate, "institution_type", institution_type)
            if isinstance(lineage, list):
                aggregate["lineage"].update(
                    item for item in lineage if isinstance(item, str) and item
                )
            aggregate["assertion_work_count"] += 1
    return aggregates


def _count_value(aggregate: dict[str, Any], field: str, value: Any) -> None:
    if isinstance(value, str) and value:
        aggregate[f"{field}_counts"][value] += 1
        plural = {
            "ror": "ror_ids",
            "display_name": "display_names",
            "country_code": "country_codes",
            "institution_type": "institution_types",
        }[field]
        aggregate[plural].add(value)


def _issue_fields(aggregate: dict[str, Any]) -> list[str]:
    fields = []
    for field in ("ror", "display_name", "country_code", "institution_type"):
        key = {
            "ror": "ror_ids",
            "display_name": "display_names",
            "country_code": "country_codes",
            "institution_type": "institution_types",
        }[field]
        values = aggregate[key]
        if not values:
            fields.append(f"missing_{field}")
        elif len(values) > 1:
            fields.append(f"conflicting_{field}")
    return sorted(fields)


def _master_row(
    institution_id: str,
    aggregate: dict[str, Any],
    lookup: tuple[dict[str, Any], str] | None,
    policy: InstitutionTypePolicy,
) -> tuple[dict[str, Any], list[str]]:
    selected = {
        "ror_id": _most_common(aggregate["ror_counts"]),
        "display_name": _most_common(aggregate["display_name_counts"]),
        "country_code": _most_common(aggregate["country_code_counts"]),
        "institution_type": _most_common(aggregate["institution_type_counts"]),
    }
    alternatives = set(aggregate["display_names"])
    lineage = set(aggregate["lineage"])
    parent_ids: set[str] = set()
    child_ids: set[str] = set()
    predecessor_ids: set[str] = set()
    successor_ids: set[str] = set()
    latitude = longitude = None
    coordinate_source = None
    updated_date = None
    resolved_fields: list[str] = []
    metadata_source = "work_assertion"
    if lookup is not None:
        raw, _retrieved_at = lookup
        mapping = {
            "ror_id": _string(raw.get("ror")),
            "display_name": _string(raw.get("display_name")),
            "country_code": _country_code(raw.get("country_code")),
            "institution_type": _string(raw.get("type")),
        }
        for field, value in mapping.items():
            if value is not None:
                if selected[field] != value:
                    resolved_fields.append(field)
                selected[field] = value
        alternatives.update(_strings(raw.get("display_name_alternatives")))
        alternatives.update(_strings(raw.get("display_name_acronyms")))
        lineage.update(_short_ids(raw.get("lineage")))
        geo = raw.get("geo")
        if isinstance(geo, dict):
            latitude = _number(geo.get("latitude"))
            longitude = _number(geo.get("longitude"))
            if latitude is not None or longitude is not None:
                coordinate_source = "openalex"
            geo_country = _country_code(geo.get("country_code"))
            if selected["country_code"] is None and geo_country is not None:
                selected["country_code"] = geo_country
                resolved_fields.append("country_code")
        for relationship in raw.get("associated_institutions") or []:
            if not isinstance(relationship, dict):
                continue
            related_id = _short_id(relationship.get("id"), "I")
            relation = relationship.get("relationship")
            if related_id is None:
                continue
            destinations = {
                "parent": parent_ids,
                "child": child_ids,
                "predecessor": predecessor_ids,
                "successor": successor_ids,
            }
            if relation in destinations:
                destinations[str(relation)].add(related_id)
        updated_date = _string(raw.get("updated_date"))
        metadata_source = "openalex_lookup"
    alternatives.discard(selected["display_name"])
    type_rule = policy.map_type(selected["institution_type"])
    return (
        {
            "institution_id": institution_id,
            **selected,
            "alternative_names": sorted(alternatives),
            "normalized_category": type_rule.normalized_category,
            "analytical_scope": type_rule.analytical_scope,
            "is_primary_research_scope": type_rule.is_primary_research_scope,
            "latitude": latitude,
            "longitude": longitude,
            "coordinate_source": coordinate_source,
            "lineage": sorted(lineage),
            "parent_ids": sorted(parent_ids),
            "child_ids": sorted(child_ids),
            "predecessor_ids": sorted(predecessor_ids),
            "successor_ids": sorted(successor_ids),
            "canonical_institution_id": institution_id,
            "canonicalization_rule_id": None,
            "openalex_updated_date": updated_date,
            "ror_version": None,
            "metadata_source": metadata_source,
            "source_ror_ids": sorted(aggregate["ror_ids"]),
            "source_display_names": sorted(aggregate["display_names"]),
            "source_country_codes": sorted(aggregate["country_codes"]),
            "source_institution_types": sorted(aggregate["institution_types"]),
            "assertion_work_count": int(aggregate["assertion_work_count"]),
        },
        sorted(set(resolved_fields)),
    )


def _most_common(counter: Counter[str]) -> str | None:
    if not counter:
        return None
    return min(counter, key=lambda value: (-counter[value], value))


def _batches(values: list[str], size: int) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _cached_institution_records(
    cache: RawResponseCache, target_ids: set[str]
) -> dict[str, tuple[dict[str, Any], str]]:
    """Reuse valid institution records even when an earlier request used different batches."""
    records: dict[str, tuple[dict[str, Any], str]] = {}
    for metadata_path in sorted(cache.pages.rglob("*.meta.json")):
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if metadata.get("endpoint") != "/institutions":
            continue
        cache_key = metadata.get("cache_key")
        if not isinstance(cache_key, str):
            continue
        entry = cache.get(cache_key)
        if entry is None:
            continue
        results = entry.data.get("results")
        if not isinstance(results, list):
            continue
        retrieved_at = str(entry.metadata.get("retrieved_at_utc") or "")
        for raw in results:
            if not isinstance(raw, dict):
                continue
            if not set(_SELECT.split(",")).issubset(raw):
                continue
            institution_id = _short_id(raw.get("id"), "I")
            if institution_id in target_ids:
                existing = records.get(institution_id)
                if existing is None or retrieved_at >= existing[1]:
                    records[institution_id] = (raw, retrieved_at)
    return records


def _has_coordinate_pair(row: dict[str, Any]) -> bool:
    return row.get("latitude") is not None and row.get("longitude") is not None


def _has_partial_coordinate_pair(row: dict[str, Any]) -> bool:
    return (row.get("latitude") is None) != (row.get("longitude") is None)


def _write_atomic_parquet(rows: list[dict[str, Any]], schema: pa.Schema, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".parquet.tmp")
    temporary.unlink(missing_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), temporary, compression="zstd")
    parquet_metrics(temporary, primary_key=[schema.names[0]], required_columns=set(schema.names))
    os.replace(temporary, path)


def _short_id(value: Any, prefix: str) -> str | None:
    if not isinstance(value, str):
        return None
    identifier = value.rstrip("/").rsplit("/", 1)[-1]
    return (
        identifier
        if identifier.startswith(prefix) and identifier[len(prefix) :].isdigit()
        else None
    )


def _short_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [identifier for item in value if (identifier := _short_id(item, "I")) is not None]


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _country_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.upper()
    return normalized if len(normalized) == 2 and normalized.isalpha() else None


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def write_institution_master_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    institution_type_path: str | Path,
    command: str,
) -> None:
    config_hashes = {
        "project": config_file_hash(project_config_path),
        "institution_types": config_file_hash(institution_type_path),
    }
    source_manifests = [".agent/manifests/work_institutions_extracted.json"]
    source_versions = {
        "openalex_institutions": summary.get("lookup_retrieved_at_max") or "not-retrieved",
        "institution_master_policy": _STAGE_VERSION,
    }
    write_json_artifact(
        path=summary_path,
        dataset_name="institution_master_summary",
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
        "institutions": (_MASTER_SCHEMA, ["institution_id"]),
        "institution_metadata_qa": (_QA_SCHEMA, ["institution_id"]),
    }
    for dataset_name, raw_path in summary["outputs"].items():
        schema, primary_key = definitions[dataset_name]
        write_parquet_manifest(
            path=raw_path,
            dataset_name=dataset_name,
            primary_key=primary_key,
            required_columns=set(schema.names),
            year_column=None,
            run_id=run_id,
            config_hashes=config_hashes,
            source_manifests=source_manifests,
            source_versions=source_versions,
            command=command,
        )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
