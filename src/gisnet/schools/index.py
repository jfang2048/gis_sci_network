"""Build the complete, visualization-independent school search index."""

from __future__ import annotations

import os
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "complete-school-index-2026-08-28-v1"
_SCHOOL_INDEX_SCHEMA = pa.schema(
    [
        ("institution_id", pa.string()),
        ("canonical_school_id", pa.string()),
        ("source_institution_ids", pa.list_(pa.string())),
        ("source_organization_count", pa.int32()),
        ("display_name", pa.string()),
        ("alternative_names", pa.list_(pa.string())),
        ("search_names", pa.list_(pa.string())),
        ("normalized_search_names", pa.list_(pa.string())),
        ("ambiguous_search_names", pa.list_(pa.string())),
        ("has_ambiguous_name_match", pa.bool_()),
        ("country_code", pa.string()),
        ("country_name", pa.string()),
        ("macro_region", pa.string()),
        ("subregion", pa.string()),
        ("institution_category", pa.string()),
        ("analytical_scope", pa.string()),
        ("openalex_id", pa.string()),
        ("ror_id", pa.string()),
        ("latitude", pa.float64()),
        ("longitude", pa.float64()),
        ("coordinate_source", pa.string()),
        ("has_coordinates", pa.bool_()),
        ("first_observed_year", pa.int32()),
        ("last_observed_year", pa.int32()),
        ("first_observed_date", pa.string()),
        ("last_observed_date", pa.string()),
        ("latest_supported_month", pa.string()),
        ("recent_window_start", pa.string()),
        ("recent_window_end", pa.string()),
        ("recent_window_months", pa.int32()),
        ("broad_work_count", pa.int64()),
        ("strict_work_count", pa.int64()),
        ("recent_24m_work_count", pa.int64()),
        ("topic_families", pa.list_(pa.string())),
        ("topic_family_count", pa.int32()),
        ("exact_date_eligible_work_count", pa.int64()),
        ("annual_only_work_count", pa.int64()),
        ("date_coverage_ratio", pa.float64()),
        ("identity_status", pa.string()),
        ("identity_resolution_confidence", pa.string()),
        ("identity_quality_flags", pa.list_(pa.string())),
        ("eligibility_status", pa.string()),
        ("support_status", pa.string()),
    ]
)
_NAME_INDEX_SCHEMA = pa.schema(
    [
        ("normalized_name", pa.string()),
        ("canonical_school_id", pa.string()),
        ("display_name", pa.string()),
        ("country_code", pa.string()),
        ("matched_names", pa.list_(pa.string())),
        ("match_types", pa.list_(pa.string())),
        ("ambiguity_count", pa.int32()),
        ("is_ambiguous", pa.bool_()),
    ]
)


def build_school_index(
    institutions_path: str | Path,
    identities_path: str | Path,
    work_institutions_path: str | Path,
    work_publication_dates_path: str | Path,
    *,
    index_path: str | Path,
    name_index_path: str | Path,
    prior_layout_path: str | Path | None = None,
    memory_limit: str = "4GB",
) -> dict[str, Any]:
    """Build a complete Broad-primary school index from stable Work memberships."""
    institutions_source = Path(institutions_path)
    identities_source = Path(identities_path)
    memberships_source = Path(work_institutions_path)
    dates_source = Path(work_publication_dates_path)
    for source in (
        institutions_source,
        identities_source,
        memberships_source,
        dates_source,
    ):
        if not source.is_file():
            raise ValueError(f"school index input does not exist: {source}")

    institution_rows = pq.read_table(institutions_source).to_pylist()
    institutions = {str(row["institution_id"]): row for row in institution_rows}
    identity_rows = pq.read_table(identities_source).to_pylist()
    identities = {str(row["institution_id"]): row for row in identity_rows}
    if set(identities) != set(institutions):
        raise ValueError("school identities must cover the institution master exactly")

    connection = duckdb.connect()
    try:
        connection.execute(f"SET memory_limit='{_sql_string(memory_limit)}'")
        connection.execute("SET threads=1")
        latest_row = connection.execute(
            """
            SELECT max(d.publication_month)
            FROM read_parquet(?) wi
            JOIN read_parquet(?) d USING (work_id, publication_year)
            WHERE wi.hierarchy_view = 'organization'
              AND wi.is_primary_research_scope
              AND wi.broad_primary
              AND d.subannual_date_eligible
            """,
            [str(memberships_source), str(dates_source)],
        ).fetchone()
        latest_month = latest_row[0] if latest_row is not None else None
        if not isinstance(latest_month, str):
            raise ValueError("school index has no exact-date-eligible Broad Work")
        recent_start = _shift_month(latest_month, -23)
        statistics = (
            connection.execute(
                """
            WITH mapped AS (
                SELECT
                    wi.work_id,
                    wi.publication_year,
                    i.canonical_school_id,
                    bool_or(wi.strict_primary) AS strict_primary,
                    bool_or(wi.broad_primary) AS broad_primary,
                    list_sort(list_distinct(flatten(list(wi.method_families))))
                        AS method_families
                FROM read_parquet(?) wi
                JOIN read_parquet(?) i USING (institution_id)
                WHERE wi.hierarchy_view = 'organization'
                  AND wi.is_primary_research_scope
                  AND wi.broad_primary
                GROUP BY wi.work_id, wi.publication_year, i.canonical_school_id
            ), dated AS (
                SELECT m.*, d.publication_date, d.publication_month,
                       d.subannual_date_eligible
                FROM mapped m
                JOIN read_parquet(?) d USING (work_id, publication_year)
            )
            SELECT
                canonical_school_id,
                min(publication_year)::INTEGER AS first_observed_year,
                max(publication_year)::INTEGER AS last_observed_year,
                min(cast(publication_date AS VARCHAR)) FILTER (
                    WHERE subannual_date_eligible
                ) AS first_observed_date,
                max(cast(publication_date AS VARCHAR)) FILTER (
                    WHERE subannual_date_eligible
                ) AS last_observed_date,
                count(*)::BIGINT AS broad_work_count,
                count(*) FILTER (WHERE strict_primary)::BIGINT AS strict_work_count,
                count(*) FILTER (
                    WHERE subannual_date_eligible AND publication_month BETWEEN ? AND ?
                )::BIGINT AS recent_24m_work_count,
                count(*) FILTER (WHERE subannual_date_eligible)::BIGINT
                    AS exact_date_eligible_work_count,
                count(*) FILTER (WHERE NOT subannual_date_eligible)::BIGINT
                    AS annual_only_work_count,
                count(*) FILTER (WHERE subannual_date_eligible)::DOUBLE / count(*)
                    AS date_coverage_ratio,
                list_sort(list_distinct(flatten(list(method_families)))) AS topic_families
            FROM dated
            GROUP BY canonical_school_id
            ORDER BY canonical_school_id
            """,
                [
                    str(memberships_source),
                    str(identities_source),
                    str(dates_source),
                    recent_start,
                    latest_month,
                ],
            )
            .to_arrow_table()
            .to_pylist()
        )
        source_ids = {
            str(row["canonical_school_id"]): sorted(str(value) for value in row["source_ids"])
            for row in connection.execute(
                """
                SELECT i.canonical_school_id,
                       list_sort(list_distinct(list(wi.institution_id))) AS source_ids
                FROM read_parquet(?) wi
                JOIN read_parquet(?) i USING (institution_id)
                WHERE wi.hierarchy_view = 'organization'
                  AND wi.is_primary_research_scope
                  AND wi.broad_primary
                GROUP BY i.canonical_school_id
                ORDER BY i.canonical_school_id
                """,
                [str(memberships_source), str(identities_source)],
            )
            .to_arrow_table()
            .to_pylist()
        }
    finally:
        connection.close()

    prepared: list[dict[str, Any]] = []
    aliases_by_school: dict[str, dict[str, dict[str, set[str]]]] = {}
    for statistic in statistics:
        school_id = str(statistic["canonical_school_id"])
        metadata = institutions.get(school_id)
        if metadata is None:
            raise ValueError(f"canonical school metadata is missing: {school_id}")
        member_ids = source_ids.get(school_id, [])
        if not member_ids:
            raise ValueError(f"eligible school has no source organizations: {school_id}")
        alias_map: dict[str, dict[str, set[str]]] = {}
        _add_alias(alias_map, str(metadata.get("display_name") or school_id), "display")
        for value in _strings(metadata.get("alternative_names")):
            _add_alias(alias_map, value, "alternative")
        for member_id in member_ids:
            source_metadata = institutions[member_id]
            _add_alias(
                alias_map,
                str(source_metadata.get("display_name") or member_id),
                "source_display",
            )
            for value in _strings(source_metadata.get("alternative_names")):
                _add_alias(alias_map, value, "source_alternative")
        aliases_by_school[school_id] = alias_map
        mapped_identities = [identities[value] for value in member_ids]
        quality_flags = sorted(
            {
                flag
                for identity in mapped_identities
                for flag in _strings(identity.get("quality_flags"))
            }
        )
        identity_statuses = {str(value["identity_status"]) for value in mapped_identities}
        confidences = {str(value["resolution_confidence"]) for value in mapped_identities}
        identity_status = (
            "explicit_evidence_collapse"
            if "explicit_evidence_collapse" in identity_statuses
            else (
                "unresolved_relationship_candidate"
                if "unresolved_relationship_candidate" in identity_statuses
                else "retained_source_organization"
            )
        )
        confidence = (
            "verified_explicit"
            if "verified_explicit" in confidences
            else ("unresolved" if "unresolved" in confidences else "source_identity")
        )
        topic_families = sorted(_strings(statistic.get("topic_families")))
        prepared.append(
            {
                "institution_id": school_id,
                "canonical_school_id": school_id,
                "source_institution_ids": member_ids,
                "source_organization_count": len(member_ids),
                "display_name": str(metadata.get("display_name") or school_id),
                "alternative_names": sorted(_strings(metadata.get("alternative_names"))),
                "country_code": _optional_string(metadata.get("country_code")),
                "country_name": _optional_string(metadata.get("country_name")),
                "macro_region": _optional_string(metadata.get("macro_region")) or "Unknown",
                "subregion": _optional_string(metadata.get("subregion")) or "Unknown",
                "institution_category": _optional_string(metadata.get("normalized_category"))
                or "unknown",
                "analytical_scope": _optional_string(metadata.get("analytical_scope")) or "unknown",
                "openalex_id": school_id,
                "ror_id": _optional_string(metadata.get("ror_id")),
                "latitude": _optional_float(metadata.get("latitude")),
                "longitude": _optional_float(metadata.get("longitude")),
                "coordinate_source": _optional_string(metadata.get("coordinate_source")),
                "first_observed_year": int(statistic["first_observed_year"]),
                "last_observed_year": int(statistic["last_observed_year"]),
                "first_observed_date": _optional_string(statistic.get("first_observed_date")),
                "last_observed_date": _optional_string(statistic.get("last_observed_date")),
                "latest_supported_month": latest_month,
                "recent_window_start": recent_start,
                "recent_window_end": latest_month,
                "recent_window_months": 24,
                "broad_work_count": int(statistic["broad_work_count"]),
                "strict_work_count": int(statistic["strict_work_count"]),
                "recent_24m_work_count": int(statistic["recent_24m_work_count"]),
                "topic_families": topic_families,
                "topic_family_count": len(topic_families),
                "exact_date_eligible_work_count": int(statistic["exact_date_eligible_work_count"]),
                "annual_only_work_count": int(statistic["annual_only_work_count"]),
                "date_coverage_ratio": float(statistic["date_coverage_ratio"]),
                "identity_status": identity_status,
                "identity_resolution_confidence": confidence,
                "identity_quality_flags": quality_flags,
                "eligibility_status": "eligible_primary_research_broad",
                "support_status": "supported",
            }
        )

    schools_by_alias: dict[str, set[str]] = {}
    for school_id, alias_map in aliases_by_school.items():
        for normalized_name in alias_map:
            schools_by_alias.setdefault(normalized_name, set()).add(school_id)
    index_rows: list[dict[str, Any]] = []
    name_rows: list[dict[str, Any]] = []
    prepared_by_id = {str(row["canonical_school_id"]): row for row in prepared}
    for school_id in sorted(prepared_by_id):
        row = prepared_by_id[school_id]
        alias_map = aliases_by_school[school_id]
        search_names = sorted(
            {name for values in alias_map.values() for name in values["names"]},
            key=lambda value: (value.casefold(), value),
        )
        normalized_names = sorted(alias_map)
        ambiguous_names = sorted(
            value for value in normalized_names if len(schools_by_alias[value]) > 1
        )
        latitude = row["latitude"]
        longitude = row["longitude"]
        row.update(
            {
                "search_names": search_names,
                "normalized_search_names": normalized_names,
                "ambiguous_search_names": ambiguous_names,
                "has_ambiguous_name_match": bool(ambiguous_names),
                "has_coordinates": latitude is not None and longitude is not None,
            }
        )
        index_rows.append(row)
        for normalized_name in normalized_names:
            alias = alias_map[normalized_name]
            ambiguity_count = len(schools_by_alias[normalized_name])
            name_rows.append(
                {
                    "normalized_name": normalized_name,
                    "canonical_school_id": school_id,
                    "display_name": row["display_name"],
                    "country_code": row["country_code"],
                    "matched_names": sorted(
                        alias["names"], key=lambda value: (value.casefold(), value)
                    ),
                    "match_types": sorted(alias["types"]),
                    "ambiguity_count": ambiguity_count,
                    "is_ambiguous": ambiguity_count > 1,
                }
            )

    destinations = {
        "school_index": Path(index_path),
        "school_name_index": Path(name_index_path),
    }
    temporary = {name: path.with_suffix(".parquet.tmp") for name, path in destinations.items()}
    for path in [*destinations.values(), *temporary.values()]:
        path.parent.mkdir(parents=True, exist_ok=True)
    for path in temporary.values():
        path.unlink(missing_ok=True)
    try:
        pq.write_table(
            pa.Table.from_pylist(index_rows, schema=_SCHOOL_INDEX_SCHEMA),
            temporary["school_index"],
            compression="zstd",
        )
        pq.write_table(
            pa.Table.from_pylist(name_rows, schema=_NAME_INDEX_SCHEMA),
            temporary["school_name_index"],
            compression="zstd",
        )
        _validate_outputs(temporary, len(index_rows))
        _promote_outputs(destinations, temporary)
    except BaseException:
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise

    core_ids = _prior_core_ids(prior_layout_path)
    outside_prior_core_count = sum(
        not set(row["source_institution_ids"]).intersection(core_ids) for row in index_rows
    )
    metrics = {
        name: parquet_metrics(
            path,
            primary_key=(
                ["canonical_school_id"]
                if name == "school_index"
                else ["normalized_name", "canonical_school_id"]
            ),
            required_columns=(
                set(_SCHOOL_INDEX_SCHEMA.names)
                if name == "school_index"
                else set(_NAME_INDEX_SCHEMA.names)
            ),
        )
        for name, path in destinations.items()
    }
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "eligibility_policy": (
            "At least one Broad-primary Work in primary research scope after canonical-school "
            "deduplication; independent of visualization rank and coordinates."
        ),
        "latest_supported_month": latest_month,
        "recent_window_start": recent_start,
        "recent_window_end": latest_month,
        "recent_window_months": 24,
        "eligible_school_count": len(index_rows),
        "strict_available_school_count": sum(row["strict_work_count"] > 0 for row in index_rows),
        "outside_prior_core_count": outside_prior_core_count,
        "missing_coordinate_school_count": sum(not row["has_coordinates"] for row in index_rows),
        "ambiguous_name_school_count": sum(row["has_ambiguous_name_match"] for row in index_rows),
        "ambiguous_normalized_name_count": sum(
            len(values) > 1 for values in schools_by_alias.values()
        ),
        "macro_region_counts": _counts(index_rows, "macro_region"),
        "row_counts": {name: value["row_count"] for name, value in metrics.items()},
        "checksums_sha256": {name: value["checksum_sha256"] for name, value in metrics.items()},
        "outputs": {name: str(path) for name, path in destinations.items()},
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "institutions_sha256": file_sha256(institutions_source),
                "identities_sha256": file_sha256(identities_source),
                "work_institutions_sha256": file_sha256(memberships_source),
                "publication_dates_sha256": file_sha256(dates_source),
                "recent_window_months": 24,
            }
        ),
        "generated_at_utc": _timestamp(),
    }


def write_school_index_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    school_decision_path: str | Path,
    command: str,
) -> None:
    """Write summary and manifests for the complete school index."""
    config_hashes = {
        "project": config_file_hash(project_config_path),
        "school_decision": config_file_hash(school_decision_path),
    }
    source_manifests = [
        ".agent/manifests/school_identities.json",
        ".agent/manifests/work_institutions.json",
        ".agent/manifests/work_publication_dates.json",
    ]
    source_versions = {"school_index_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="school_index_summary",
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
        "school_index": (["canonical_school_id"], set(_SCHOOL_INDEX_SCHEMA.names)),
        "school_name_index": (
            ["normalized_name", "canonical_school_id"],
            set(_NAME_INDEX_SCHEMA.names),
        ),
    }
    for dataset_name, raw_path in summary["outputs"].items():
        primary_key, required_columns = definitions[dataset_name]
        write_parquet_manifest(
            path=raw_path,
            dataset_name=dataset_name,
            primary_key=primary_key,
            required_columns=required_columns,
            year_column=None,
            run_id=run_id,
            config_hashes=config_hashes,
            source_manifests=source_manifests,
            source_versions=source_versions,
            command=command,
        )


def _add_alias(alias_map: dict[str, dict[str, set[str]]], value: str, kind: str) -> None:
    cleaned = " ".join(value.split())
    normalized = normalize_school_name(cleaned)
    if not normalized:
        return
    entry = alias_map.setdefault(normalized, {"names": set(), "types": set()})
    entry["names"].add(cleaned)
    entry["types"].add(kind)


def normalize_school_name(value: str) -> str:
    """Normalize a search alias without using it as an identity join key."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _prior_core_ids(path: str | Path | None) -> set[str]:
    if path is None or not Path(path).is_file():
        return set()
    table = pq.read_table(path, columns=["institution_id", "is_core"])
    return {str(row["institution_id"]) for row in table.to_pylist() if bool(row.get("is_core"))}


def _validate_outputs(temporary: dict[str, Path], expected_school_count: int) -> None:
    index_metrics = parquet_metrics(
        temporary["school_index"],
        primary_key=["canonical_school_id"],
        required_columns=set(_SCHOOL_INDEX_SCHEMA.names),
    )
    if int(index_metrics["row_count"]) != expected_school_count:
        raise ValueError("school index row count does not match eligible school count")
    name_metrics = parquet_metrics(
        temporary["school_name_index"],
        primary_key=["normalized_name", "canonical_school_id"],
        required_columns=set(_NAME_INDEX_SCHEMA.names),
    )
    if int(name_metrics["row_count"]) < expected_school_count:
        raise ValueError("every eligible school requires at least one search name")


def _promote_outputs(destinations: dict[str, Path], temporary: dict[str, Path]) -> None:
    backups = {name: path.with_suffix(".parquet.bak") for name, path in destinations.items()}
    for backup in backups.values():
        backup.unlink(missing_ok=True)
    promoted: list[str] = []
    try:
        for name, destination in destinations.items():
            if destination.exists():
                os.replace(destination, backups[name])
            os.replace(temporary[name], destination)
            promoted.append(name)
    except BaseException:
        for name in promoted:
            destinations[name].unlink(missing_ok=True)
        for name, backup in backups.items():
            if backup.exists():
                os.replace(backup, destinations[name])
        raise
    for backup in backups.values():
        backup.unlink(missing_ok=True)


def _shift_month(value: str, offset: int) -> str:
    year, month = (int(item) for item in value.split("-"))
    index = year * 12 + month - 1 + offset
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _strings(value: Any) -> list[str]:
    return [str(item) for item in value if isinstance(item, str) and item] if value else []


def _optional_string(value: Any) -> str | None:
    return str(value) if value is not None and str(value) else None


def _optional_float(value: Any) -> float | None:
    return float(value) if value is not None else None


def _counts(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        value = str(row.get(key) or "Unknown")
        result[value] = result.get(value, 0) + 1
    return dict(sorted(result.items()))


def _sql_string(value: str) -> str:
    return value.replace("'", "''")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
