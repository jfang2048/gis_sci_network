"""Versioned, cycle-safe institution canonicalization override registry."""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

OverrideAction = Literal["keep", "collapse", "replace", "exclude_from_primary", "manual_country"]
HierarchyView = Literal["organization", "umbrella"]


class InstitutionOverrideRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rule_id: str
    action: OverrideAction
    source_institution_id: str
    target_institution_id: str | None = None
    country_code: str | None = None
    reason: str
    provenance: str

    @field_validator("rule_id", "source_institution_id", "reason", "provenance")
    @classmethod
    def nonempty(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("override identifiers, reasons, and provenance must be non-empty")
        return normalized

    @field_validator("target_institution_id", "country_code", mode="before")
    @classmethod
    def empty_to_none(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_action_fields(self) -> InstitutionOverrideRule:
        is_relationship = self.action in {"collapse", "replace"}
        if is_relationship and not self.target_institution_id:
            raise ValueError(f"{self.action} requires target_institution_id")
        if self.target_institution_id == self.source_institution_id:
            raise ValueError("an override cannot target itself")
        if self.action == "manual_country":
            if (
                not self.country_code
                or len(self.country_code) != 2
                or not self.country_code.isalpha()
            ):
                raise ValueError("manual_country requires a two-letter country_code")
            self.country_code = self.country_code.upper()
        elif self.country_code:
            raise ValueError("country_code is allowed only for manual_country")
        return self


class InstitutionOverrideRegistry:
    def __init__(self, rules: list[InstitutionOverrideRule]) -> None:
        self.rules = rules
        self._validate()
        self._relationships = {
            rule.source_institution_id: str(rule.target_institution_id)
            for rule in rules
            if rule.action in {"collapse", "replace"}
        }

    @classmethod
    def load(
        cls, path: str | Path = "config/institution_overrides.csv"
    ) -> InstitutionOverrideRegistry:
        with Path(path).open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            required = set(InstitutionOverrideRule.model_fields)
            if set(reader.fieldnames or []) != required:
                raise ValueError(f"institution override columns must be exactly {sorted(required)}")
            rules = [InstitutionOverrideRule.model_validate(row) for row in reader]
        return cls(rules)

    def _validate(self) -> None:
        rule_ids = [rule.rule_id for rule in self.rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("institution override rule_id values must be unique")
        by_source: dict[str, list[InstitutionOverrideRule]] = defaultdict(list)
        for rule in self.rules:
            by_source[rule.source_institution_id].append(rule)
        for source, source_rules in by_source.items():
            relationship_rules = [
                rule for rule in source_rules if rule.action in {"collapse", "replace"}
            ]
            if len(relationship_rules) > 1:
                raise ValueError(f"multiple canonical targets configured for {source}")
            actions = {rule.action for rule in source_rules}
            if "keep" in actions and actions.intersection({"collapse", "replace"}):
                raise ValueError(f"keep conflicts with canonicalization for {source}")
            if len([rule for rule in source_rules if rule.action == "manual_country"]) > 1:
                raise ValueError(f"multiple manual countries configured for {source}")
        relationship_graph = {
            rule.source_institution_id: str(rule.target_institution_id)
            for rule in self.rules
            if rule.action in {"collapse", "replace"}
        }
        for source in relationship_graph:
            seen: set[str] = set()
            current = source
            while current in relationship_graph:
                if current in seen:
                    path = " -> ".join([*sorted(seen), current])
                    raise ValueError(f"institution canonicalization cycle detected: {path}")
                seen.add(current)
                current = relationship_graph[current]

    def canonical_id(self, institution_id: str, view: HierarchyView) -> str:
        """Organization preserves the source ID; umbrella follows the full rule chain."""
        if view == "organization":
            return institution_id
        current = institution_id
        while current in self._relationships:
            current = self._relationships[current]
        return current

    def manual_country(self, institution_id: str) -> str | None:
        return next(
            (
                rule.country_code
                for rule in self.rules
                if rule.source_institution_id == institution_id and rule.action == "manual_country"
            ),
            None,
        )

    def is_excluded_from_primary(self, institution_id: str) -> bool:
        return any(
            rule.source_institution_id == institution_id and rule.action == "exclude_from_primary"
            for rule in self.rules
        )

    def audit_records(self) -> list[dict[str, str | None]]:
        records = []
        for rule in sorted(self.rules, key=lambda item: item.rule_id):
            records.append(
                {
                    **rule.model_dump(mode="json"),
                    "resolved_umbrella_id": self.canonical_id(
                        rule.source_institution_id, "umbrella"
                    ),
                }
            )
        return records
