"""Build original-organization and explicit-rule umbrella hierarchy views."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest
from gisnet.institutions.overrides import InstitutionOverrideRegistry
from gisnet.ror.enrich import normalize_ror_id

_STAGE_VERSION = "institution-hierarchy-2026-08-05-v1"
_HIERARCHY_SCHEMA = pa.schema(
    [
        ("hierarchy_view", pa.string()),
        ("institution_id", pa.string()),
        ("canonical_institution_id", pa.string()),
        ("is_collapsed", pa.bool_()),
        ("canonicalization_rule_ids", pa.list_(pa.string())),
        ("canonicalization_reasons", pa.list_(pa.string())),
        ("canonicalization_provenance", pa.list_(pa.string())),
    ]
)
_AUDIT_SCHEMA = pa.schema(
    [
        ("rule_id", pa.string()),
        ("action", pa.string()),
        ("source_institution_id", pa.string()),
        ("target_institution_id", pa.string()),
        ("resolved_umbrella_id", pa.string()),
        ("reason", pa.string()),
        ("provenance", pa.string()),
    ]
)
_CANDIDATE_SCHEMA = pa.schema(
    [
        ("institution_id", pa.string()),
        ("openalex_lineage_ids", pa.list_(pa.string())),
        ("openalex_parent_ids", pa.list_(pa.string())),
        ("ror_parent_ids", pa.list_(pa.string())),
        ("ror_parent_institution_ids", pa.list_(pa.string())),
        ("candidate_umbrella_institution_ids", pa.list_(pa.string())),
        ("resolution", pa.string()),
    ]
)


def build_institution_hierarchy(
    institutions_path: str | Path,
    overrides: InstitutionOverrideRegistry,
    *,
    hierarchy_path: str | Path,
    audit_path: str | Path,
    candidates_path: str | Path,
) -> dict[str, Any]:
    """Create comparable hierarchy mappings without making inferred federated collapses."""
    source = Path(institutions_path)
    table = pq.read_table(source)
    institution_ids = {str(value) for value in table.column("institution_id").to_pylist()}
    relationship_rules = {
        rule.source_institution_id: rule
        for rule in overrides.rules
        if rule.action in {"collapse", "replace"}
    }
    for rule in relationship_rules.values():
        if rule.source_institution_id not in institution_ids:
            raise ValueError(f"canonicalization source is absent: {rule.source_institution_id}")
        if rule.target_institution_id not in institution_ids:
            raise ValueError(f"canonicalization target is absent: {rule.target_institution_id}")
    ror_to_institution: dict[str, str] = {}
    for raw in table.select(["institution_id", "ror_id"]).to_pylist():
        identifier = normalize_ror_id(raw.get("ror_id"))
        if identifier is not None:
            ror_to_institution.setdefault(identifier, str(raw["institution_id"]))
    hierarchy_rows: list[dict[str, Any]] = []
    candidate_rows: list[dict[str, Any]] = []
    for raw in table.to_pylist():
        institution_id = str(raw["institution_id"])
        hierarchy_rows.append(
            {
                "hierarchy_view": "organization",
                "institution_id": institution_id,
                "canonical_institution_id": institution_id,
                "is_collapsed": False,
                "canonicalization_rule_ids": [],
                "canonicalization_reasons": [],
                "canonicalization_provenance": [],
            }
        )
        target, chain = _resolve_chain(institution_id, relationship_rules)
        hierarchy_rows.append(
            {
                "hierarchy_view": "umbrella",
                "institution_id": institution_id,
                "canonical_institution_id": target,
                "is_collapsed": target != institution_id,
                "canonicalization_rule_ids": [rule.rule_id for rule in chain],
                "canonicalization_reasons": [rule.reason for rule in chain],
                "canonicalization_provenance": [rule.provenance for rule in chain],
            }
        )
        lineage = sorted(
            identifier
            for value in _strings(raw.get("lineage"))
            if (identifier := _openalex_id(value)) is not None and identifier != institution_id
        )
        openalex_parents = sorted(
            identifier
            for value in _strings(raw.get("parent_ids"))
            if (identifier := _openalex_id(value)) is not None and identifier != institution_id
        )
        ror_parents = sorted(
            identifier
            for value in _strings(raw.get("ror_parent_ids"))
            if (identifier := normalize_ror_id(value)) is not None
        )
        ror_parent_institutions = sorted(
            {ror_to_institution[value] for value in ror_parents if value in ror_to_institution}
            - {institution_id}
        )
        candidate_ids = sorted(
            (set(lineage) | set(openalex_parents) | set(ror_parent_institutions)) & institution_ids
        )
        candidate_rows.append(
            {
                "institution_id": institution_id,
                "openalex_lineage_ids": lineage,
                "openalex_parent_ids": openalex_parents,
                "ror_parent_ids": ror_parents,
                "ror_parent_institution_ids": ror_parent_institutions,
                "candidate_umbrella_institution_ids": candidate_ids,
                "resolution": (
                    "explicit_rule"
                    if target != institution_id
                    else (
                        "retained_separate_without_explicit_rule"
                        if candidate_ids
                        else "no_parent_evidence"
                    )
                ),
            }
        )
    audit_rows = [
        {
            "rule_id": rule.rule_id,
            "action": rule.action,
            "source_institution_id": rule.source_institution_id,
            "target_institution_id": rule.target_institution_id,
            "resolved_umbrella_id": overrides.canonical_id(rule.source_institution_id, "umbrella"),
            "reason": rule.reason,
            "provenance": rule.provenance,
        }
        for rule in sorted(relationship_rules.values(), key=lambda value: value.rule_id)
    ]
    hierarchy = Path(hierarchy_path)
    audit = Path(audit_path)
    candidate_output = Path(candidates_path)
    _write_atomic(
        hierarchy_rows, _HIERARCHY_SCHEMA, hierarchy, ["hierarchy_view", "institution_id"]
    )
    _write_atomic(audit_rows, _AUDIT_SCHEMA, audit, ["rule_id"])
    _write_atomic(candidate_rows, _CANDIDATE_SCHEMA, candidate_output, ["institution_id"])
    hierarchy_metrics = parquet_metrics(
        hierarchy,
        primary_key=["hierarchy_view", "institution_id"],
        required_columns=set(_HIERARCHY_SCHEMA.names),
    )
    if int(hierarchy_metrics["row_count"]) != table.num_rows * 2:
        raise ValueError("institution hierarchy row count did not reconcile")
    organization_count = sum(row["hierarchy_view"] == "organization" for row in hierarchy_rows)
    umbrella_count = sum(row["hierarchy_view"] == "umbrella" for row in hierarchy_rows)
    if organization_count != umbrella_count:
        raise ValueError("organization and umbrella views are not comparable")
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "institutions_sha256": file_sha256(source),
                "overrides": [rule.model_dump(mode="json") for rule in overrides.rules],
            }
        ),
        "institution_count": table.num_rows,
        "hierarchy_row_count": len(hierarchy_rows),
        "explicit_collapse_count": sum(row["is_collapsed"] for row in hierarchy_rows),
        "canonicalization_rule_count": len(audit_rows),
        "relationship_candidate_count": sum(
            bool(row["candidate_umbrella_institution_ids"]) for row in candidate_rows
        ),
        "automatic_collapse_count": 0,
        "outputs": {
            "institution_hierarchy": str(hierarchy),
            "institution_canonicalization_audit": str(audit),
            "institution_hierarchy_candidates": str(candidate_output),
        },
        "generated_at_utc": _timestamp(),
    }


def _resolve_chain(institution_id: str, rules: dict[str, Any]) -> tuple[str, list[Any]]:
    current = institution_id
    chain: list[Any] = []
    seen: set[str] = set()
    while current in rules:
        if current in seen:
            raise ValueError(f"canonicalization cycle encountered at {current}")
        seen.add(current)
        rule = rules[current]
        chain.append(rule)
        current = str(rule.target_institution_id)
    return current, chain


def _openalex_id(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    identifier = value.rstrip("/").rsplit("/", 1)[-1]
    return identifier if identifier.startswith("I") and identifier[1:].isdigit() else None


def _strings(value: Any) -> list[str]:
    return (
        [item for item in value if isinstance(item, str) and item]
        if isinstance(value, list)
        else []
    )


def _write_atomic(
    rows: list[dict[str, Any]], schema: pa.Schema, path: Path, primary_key: list[str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".parquet.tmp")
    temporary.unlink(missing_ok=True)
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), temporary, compression="zstd")
    parquet_metrics(temporary, primary_key=primary_key, required_columns=set(schema.names))
    os.replace(temporary, path)


def write_hierarchy_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    overrides_path: str | Path,
    command: str,
) -> None:
    config_hashes = {
        "project": config_file_hash(project_config_path),
        "institution_overrides": file_sha256(overrides_path),
    }
    source_manifests = [
        ".agent/manifests/institutions_ror.json",
        ".agent/manifests/institutions_geographic.json",
    ]
    source_versions = {"hierarchy_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="institution_hierarchy_summary",
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
        "institution_hierarchy": (
            ["hierarchy_view", "institution_id"],
            set(_HIERARCHY_SCHEMA.names),
        ),
        "institution_canonicalization_audit": (["rule_id"], set(_AUDIT_SCHEMA.names)),
        "institution_hierarchy_candidates": (["institution_id"], set(_CANDIDATE_SCHEMA.names)),
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


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
