"""Evidence-bounded canonical school identities over preserved organizations."""

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
from gisnet.institutions.overrides import (
    InstitutionOverrideRegistry,
    InstitutionOverrideRule,
)

_STAGE_VERSION = "school-identity-2026-08-28-v1"
_IDENTITY_SCHEMA = pa.schema(
    [
        ("institution_id", pa.string()),
        ("canonical_school_id", pa.string()),
        ("is_collapsed", pa.bool_()),
        ("identity_status", pa.string()),
        ("resolution_method", pa.string()),
        ("resolution_confidence", pa.string()),
        ("canonicalization_rule_ids", pa.list_(pa.string())),
        ("canonicalization_reasons", pa.list_(pa.string())),
        ("evidence", pa.list_(pa.string())),
        ("provenance", pa.list_(pa.string())),
        ("candidate_school_ids", pa.list_(pa.string())),
        ("openalex_lineage_ids", pa.list_(pa.string())),
        ("openalex_parent_ids", pa.list_(pa.string())),
        ("ror_parent_ids", pa.list_(pa.string())),
        ("ror_parent_institution_ids", pa.list_(pa.string())),
        ("quality_flags", pa.list_(pa.string())),
    ]
)
_AUDIT_SCHEMA = pa.schema(
    [
        ("rule_id", pa.string()),
        ("action", pa.string()),
        ("source_institution_id", pa.string()),
        ("target_institution_id", pa.string()),
        ("resolved_canonical_school_id", pa.string()),
        ("reason", pa.string()),
        ("evidence", pa.string()),
        ("provenance", pa.string()),
        ("reversible", pa.bool_()),
    ]
)


def build_school_identities(
    institutions_path: str | Path,
    hierarchy_candidates_path: str | Path,
    overrides: InstitutionOverrideRegistry,
    *,
    identities_path: str | Path,
    audit_path: str | Path,
) -> dict[str, Any]:
    """Build a school view without inferring collapses from source relationships."""
    institutions_source = Path(institutions_path)
    candidates_source = Path(hierarchy_candidates_path)
    for source in (institutions_source, candidates_source):
        if not source.is_file():
            raise ValueError(f"school identity input does not exist: {source}")
    organization_hash_before = file_sha256(institutions_source)
    institution_rows = pq.read_table(institutions_source, columns=["institution_id"]).to_pylist()
    institution_ids = {str(row["institution_id"]) for row in institution_rows}
    candidate_table = pq.read_table(candidates_source)
    required_candidate_columns = {
        "institution_id",
        "openalex_lineage_ids",
        "openalex_parent_ids",
        "ror_parent_ids",
        "ror_parent_institution_ids",
        "candidate_umbrella_institution_ids",
    }
    missing = required_candidate_columns.difference(candidate_table.column_names)
    if missing:
        raise ValueError(f"school identity candidates lack columns: {sorted(missing)}")
    candidates = {str(row["institution_id"]): row for row in candidate_table.to_pylist()}
    if set(candidates) != institution_ids:
        raise ValueError("school identity candidates do not cover every source organization")

    relationship_rules = {
        rule.source_institution_id: rule
        for rule in overrides.rules
        if rule.action in {"collapse", "replace"}
    }
    for rule in relationship_rules.values():
        if rule.source_institution_id not in institution_ids:
            raise ValueError(f"school identity source is absent: {rule.source_institution_id}")
        if rule.target_institution_id not in institution_ids:
            raise ValueError(f"school identity target is absent: {rule.target_institution_id}")

    identity_rows: list[dict[str, Any]] = []
    for institution_id in sorted(institution_ids):
        candidate = candidates[institution_id]
        canonical_school_id, chain = _resolve_chain(institution_id, relationship_rules)
        candidate_ids = sorted(
            {
                str(value)
                for value in _strings(candidate.get("candidate_umbrella_institution_ids"))
                if str(value) in institution_ids and str(value) != institution_id
            }
        )
        collapsed = canonical_school_id != institution_id
        unresolved = bool(candidate_ids) and not collapsed
        status = (
            "explicit_evidence_collapse"
            if collapsed
            else (
                "unresolved_relationship_candidate"
                if unresolved
                else "retained_source_organization"
            )
        )
        identity_rows.append(
            {
                "institution_id": institution_id,
                "canonical_school_id": canonical_school_id,
                "is_collapsed": collapsed,
                "identity_status": status,
                "resolution_method": (
                    "explicit_override_registry" if collapsed else "organization_identity"
                ),
                "resolution_confidence": (
                    "verified_explicit"
                    if collapsed
                    else ("unresolved" if unresolved else "source_identity")
                ),
                "canonicalization_rule_ids": [rule.rule_id for rule in chain],
                "canonicalization_reasons": [rule.reason for rule in chain],
                "evidence": [
                    "Explicit versioned collapse/replace rule; no relationship candidate was "
                    "promoted automatically."
                    for _ in chain
                ],
                "provenance": [rule.provenance for rule in chain],
                "candidate_school_ids": candidate_ids,
                "openalex_lineage_ids": sorted(_strings(candidate.get("openalex_lineage_ids"))),
                "openalex_parent_ids": sorted(_strings(candidate.get("openalex_parent_ids"))),
                "ror_parent_ids": sorted(_strings(candidate.get("ror_parent_ids"))),
                "ror_parent_institution_ids": sorted(
                    _strings(candidate.get("ror_parent_institution_ids"))
                ),
                "quality_flags": ["ambiguous_fragmentation"] if unresolved else [],
            }
        )

    audit_rows = [
        {
            "rule_id": rule.rule_id,
            "action": rule.action,
            "source_institution_id": rule.source_institution_id,
            "target_institution_id": str(rule.target_institution_id),
            "resolved_canonical_school_id": overrides.canonical_id(
                rule.source_institution_id, "umbrella"
            ),
            "reason": rule.reason,
            "evidence": (
                "Explicit versioned collapse/replace rule; source relationships remain "
                "candidate evidence only."
            ),
            "provenance": rule.provenance,
            "reversible": True,
        }
        for rule in sorted(relationship_rules.values(), key=lambda item: item.rule_id)
    ]
    destinations = {
        "school_identities": Path(identities_path),
        "school_identity_audit": Path(audit_path),
    }
    temporary = {name: path.with_suffix(".parquet.tmp") for name, path in destinations.items()}
    for path in [*destinations.values(), *temporary.values()]:
        path.parent.mkdir(parents=True, exist_ok=True)
    for path in temporary.values():
        path.unlink(missing_ok=True)
    try:
        pq.write_table(
            pa.Table.from_pylist(identity_rows, schema=_IDENTITY_SCHEMA),
            temporary["school_identities"],
            compression="zstd",
        )
        pq.write_table(
            pa.Table.from_pylist(audit_rows, schema=_AUDIT_SCHEMA),
            temporary["school_identity_audit"],
            compression="zstd",
        )
        _validate_outputs(temporary, len(institution_ids))
        _promote_outputs(destinations, temporary)
    except BaseException:
        for path in temporary.values():
            path.unlink(missing_ok=True)
        raise

    organization_hash_after = file_sha256(institutions_source)
    if organization_hash_after != organization_hash_before:
        raise ValueError("organization source changed while building school identities")
    identity_metrics = parquet_metrics(
        destinations["school_identities"],
        primary_key=["institution_id"],
        required_columns=set(_IDENTITY_SCHEMA.names),
    )
    audit_metrics = parquet_metrics(
        destinations["school_identity_audit"],
        primary_key=["rule_id"],
        required_columns=set(_AUDIT_SCHEMA.names),
    )
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "institution_count": len(institution_ids),
        "canonical_school_count": len({str(row["canonical_school_id"]) for row in identity_rows}),
        "explicit_collapse_count": sum(bool(row["is_collapsed"]) for row in identity_rows),
        "unresolved_relationship_count": sum(
            row["identity_status"] == "unresolved_relationship_candidate" for row in identity_rows
        ),
        "automatic_collapse_count": 0,
        "umbrella_equivalent_to_organization": not any(
            bool(row["is_collapsed"]) for row in identity_rows
        ),
        "organization_source_sha256_before": organization_hash_before,
        "organization_source_sha256_after": organization_hash_after,
        "row_counts": {
            "school_identities": identity_metrics["row_count"],
            "school_identity_audit": audit_metrics["row_count"],
        },
        "checksums_sha256": {
            "school_identities": identity_metrics["checksum_sha256"],
            "school_identity_audit": audit_metrics["checksum_sha256"],
        },
        "outputs": {name: str(path) for name, path in destinations.items()},
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "institutions_sha256": organization_hash_before,
                "candidates_sha256": file_sha256(candidates_source),
                "overrides": [rule.model_dump(mode="json") for rule in overrides.rules],
            }
        ),
        "generated_at_utc": _timestamp(),
    }


def write_school_identity_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    school_decision_path: str | Path,
    overrides_path: str | Path,
    command: str,
) -> None:
    """Write the school-identity summary and dataset manifests."""
    config_hashes = {
        "project": config_file_hash(project_config_path),
        "school_decision": config_file_hash(school_decision_path),
        "institution_overrides": file_sha256(overrides_path),
    }
    source_manifests = [
        ".agent/manifests/institutions_ror.json",
        ".agent/manifests/institution_hierarchy_candidates.json",
    ]
    source_versions = {"school_identity_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="school_identity_summary",
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
        "school_identities": (["institution_id"], set(_IDENTITY_SCHEMA.names)),
        "school_identity_audit": (["rule_id"], set(_AUDIT_SCHEMA.names)),
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


def _resolve_chain(
    institution_id: str,
    rules: dict[str, InstitutionOverrideRule],
) -> tuple[str, list[InstitutionOverrideRule]]:
    current = institution_id
    chain: list[InstitutionOverrideRule] = []
    seen: set[str] = set()
    while current in rules:
        if current in seen:
            raise ValueError(f"school canonicalization cycle encountered at {current}")
        seen.add(current)
        rule = rules[current]
        chain.append(rule)
        current = str(rule.target_institution_id)
    return current, chain


def _strings(value: Any) -> list[str]:
    return [str(item) for item in value if isinstance(item, str) and item] if value else []


def _validate_outputs(temporary: dict[str, Path], expected_identity_rows: int) -> None:
    identity_metrics = parquet_metrics(
        temporary["school_identities"],
        primary_key=["institution_id"],
        required_columns=set(_IDENTITY_SCHEMA.names),
    )
    if int(identity_metrics["row_count"]) != expected_identity_rows:
        raise ValueError("school identity rows do not reconcile with source organizations")
    parquet_metrics(
        temporary["school_identity_audit"],
        primary_key=["rule_id"],
        required_columns=set(_AUDIT_SCHEMA.names),
    )
    table = pq.read_table(
        temporary["school_identities"],
        columns=["institution_id", "canonical_school_id"],
    )
    institution_ids = {str(value) for value in table.column("institution_id").to_pylist()}
    canonical_ids = {str(value) for value in table.column("canonical_school_id").to_pylist()}
    if not canonical_ids.issubset(institution_ids):
        raise ValueError("school identity mapping contains an unknown canonical target")


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


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
