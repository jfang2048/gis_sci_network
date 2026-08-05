"""Apply frozen geographic conventions and explicit institution country overrides."""

from __future__ import annotations

import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest
from gisnet.geography import UNKNOWN_REGION, RegionRegistry
from gisnet.institutions.overrides import InstitutionOverrideRegistry

_STAGE_VERSION = "institution-geography-2026-08-05-v1"
_GEOGRAPHY_FIELDS = [
    pa.field("openalex_country_code", pa.string()),
    pa.field("country_name", pa.string()),
    pa.field("macro_region", pa.string()),
    pa.field("subregion", pa.string()),
    pa.field("region_mapping_version", pa.string()),
    pa.field("manual_country_override", pa.bool_()),
    pa.field("manual_country_rule_id", pa.string()),
]
_QA_SCHEMA = pa.schema(
    [
        ("institution_id", pa.string()),
        ("openalex_country_code", pa.string()),
        ("effective_country_code", pa.string()),
        ("issue", pa.string()),
        ("manual_country_rule_id", pa.string()),
        ("override_reason", pa.string()),
    ]
)


def apply_institution_geography(
    institutions_path: str | Path,
    regions: RegionRegistry,
    overrides: InstitutionOverrideRegistry,
    *,
    output_path: str | Path,
    qa_path: str | Path,
) -> dict[str, Any]:
    source = Path(institutions_path)
    table = pq.read_table(source)
    output_schema = pa.schema([*table.schema, *_GEOGRAPHY_FIELDS])
    region_by_code = regions.by_code()
    manual_rules = {
        rule.source_institution_id: rule
        for rule in overrides.rules
        if rule.action == "manual_country"
    }
    rows: list[dict[str, Any]] = []
    qa_rows: list[dict[str, Any]] = []
    macro_counts: Counter[str] = Counter()
    for raw in table.to_pylist():
        institution_id = str(raw["institution_id"])
        source_code = _country_code(raw.get("country_code"))
        rule = manual_rules.get(institution_id)
        requested_code = rule.country_code if rule is not None else source_code
        mapped = region_by_code.get(requested_code or "", UNKNOWN_REGION)
        effective_code = mapped.country_code
        issue = None
        if rule is not None and source_code is not None and source_code != rule.country_code:
            issue = "manual_country_differs_from_source"
        elif requested_code is None:
            issue = "missing_source_country"
        elif requested_code not in region_by_code:
            issue = "unmapped_source_country"
        if issue is not None:
            qa_rows.append(
                {
                    "institution_id": institution_id,
                    "openalex_country_code": source_code,
                    "effective_country_code": effective_code,
                    "issue": issue,
                    "manual_country_rule_id": rule.rule_id if rule is not None else None,
                    "override_reason": rule.reason if rule is not None else None,
                }
            )
        row = {
            **raw,
            "country_code": effective_code,
            "openalex_country_code": source_code,
            "country_name": mapped.country_name,
            "macro_region": mapped.macro_region,
            "subregion": mapped.subregion,
            "region_mapping_version": regions.mapping_version,
            "manual_country_override": rule is not None,
            "manual_country_rule_id": rule.rule_id if rule is not None else None,
        }
        rows.append(row)
        macro_counts[mapped.macro_region] += 1
    output = Path(output_path)
    qa = Path(qa_path)
    _write_atomic(rows, output_schema, output)
    _write_atomic(qa_rows, _QA_SCHEMA, qa)
    output_metrics = parquet_metrics(
        output,
        primary_key=["institution_id"],
        required_columns=set(output_schema.names),
    )
    qa_metrics = parquet_metrics(
        qa,
        primary_key=["institution_id"],
        required_columns=set(_QA_SCHEMA.names),
    )
    if int(output_metrics["row_count"]) != table.num_rows:
        raise ValueError("geographic institution row count did not reconcile")
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "institutions_sha256": file_sha256(source),
                "regions_hash": regions.semantic_hash,
                "overrides": [rule.model_dump(mode="json") for rule in overrides.rules],
            }
        ),
        "institution_count": len(rows),
        "geography_qa_count": len(qa_rows),
        "manual_override_count": sum(row["manual_country_override"] for row in rows),
        "macro_region_counts": dict(sorted(macro_counts.items())),
        "outputs": {"institutions_geographic": str(output), "institution_geography_qa": str(qa)},
        "generated_at_utc": _timestamp(),
        "qa_row_count": int(qa_metrics["row_count"]),
    }


def _write_atomic(rows: list[dict[str, Any]], schema: pa.Schema, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".parquet.tmp")
    temporary.unlink(missing_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), temporary, compression="zstd")
    parquet_metrics(temporary, primary_key=[schema.names[0]], required_columns=set(schema.names))
    os.replace(temporary, path)


def _country_code(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.upper()
    return normalized if len(normalized) == 2 and normalized.isalpha() else None


def write_geography_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    regions_path: str | Path,
    overrides_path: str | Path,
    command: str,
) -> None:
    config_hashes = {
        "project": config_file_hash(project_config_path),
        "regions": config_file_hash(regions_path),
        "institution_overrides": file_sha256(overrides_path),
    }
    source_manifests = [".agent/manifests/institutions.json"]
    source_versions = {"un_m49": "un-m49-retrieved-2026-08-05"}
    write_json_artifact(
        path=summary_path,
        dataset_name="institution_geography_summary",
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
        "institutions_geographic": (["institution_id"], None),
        "institution_geography_qa": (["institution_id"], _QA_SCHEMA),
    }
    for dataset_name, raw_path in summary["outputs"].items():
        primary_key, schema = definitions[dataset_name]
        required = (
            set(schema.names)
            if schema is not None
            else {
                "institution_id",
                "country_code",
                "country_name",
                "macro_region",
                "subregion",
                "openalex_country_code",
            }
        )
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


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
