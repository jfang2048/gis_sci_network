import csv
from pathlib import Path

import pytest

from gisnet.institutions.overrides import InstitutionOverrideRegistry

FIELDS = [
    "rule_id",
    "action",
    "source_institution_id",
    "target_institution_id",
    "country_code",
    "reason",
    "provenance",
]


def _write(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _row(rule_id: str, action: str, source: str, target: str = "") -> dict[str, str]:
    return {
        "rule_id": rule_id,
        "action": action,
        "source_institution_id": source,
        "target_institution_id": target,
        "country_code": "",
        "reason": "synthetic test",
        "provenance": "unit fixture",
    }


def test_empty_production_registry_is_valid() -> None:
    assert InstitutionOverrideRegistry.load().rules == []


def test_canonicalization_is_deterministic_and_organization_unchanged(tmp_path: Path) -> None:
    path = tmp_path / "overrides.csv"
    _write(
        path,
        [
            _row("R1", "collapse", "I1", "I2"),
            _row("R2", "replace", "I2", "I3"),
            {
                **_row("R3", "manual_country", "I1"),
                "country_code": "it",
            },
            _row("R4", "exclude_from_primary", "I4"),
        ],
    )
    registry = InstitutionOverrideRegistry.load(path)
    assert registry.canonical_id("I1", "organization") == "I1"
    assert registry.canonical_id("I1", "umbrella") == "I3"
    assert registry.canonical_id("I1", "umbrella") == "I3"
    assert registry.manual_country("I1") == "IT"
    assert registry.is_excluded_from_primary("I4")
    assert registry.audit_records()[0]["resolved_umbrella_id"] == "I3"


def test_canonicalization_cycle_fails(tmp_path: Path) -> None:
    path = tmp_path / "overrides.csv"
    _write(
        path,
        [
            _row("R1", "collapse", "I1", "I2"),
            _row("R2", "replace", "I2", "I1"),
        ],
    )
    with pytest.raises(ValueError, match="cycle"):
        InstitutionOverrideRegistry.load(path)


def test_override_requires_reason_and_provenance(tmp_path: Path) -> None:
    path = tmp_path / "overrides.csv"
    row = _row("R1", "keep", "I1")
    row["reason"] = ""
    _write(path, [row])
    with pytest.raises(ValueError, match="non-empty"):
        InstitutionOverrideRegistry.load(path)
