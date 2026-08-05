"""Frozen country-to-region mapping and explicit override validation."""

from __future__ import annotations

import csv
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from gisnet.config import load_yaml, semantic_hash

MacroRegion = str
Subregion = str

REQUIRED_MACRO_REGIONS = {"Europe", "Asia", "Americas", "Africa", "Oceania", "Unknown"}
REQUIRED_SUBREGIONS = {
    "Northern Europe",
    "Western Europe",
    "Southern Europe",
    "Eastern Europe",
    "Eastern Asia",
    "Southeastern Asia",
    "Southern Asia",
    "Central Asia",
    "Western Asia",
    "Northern America",
    "Central America",
    "Caribbean",
    "South America",
    "Northern Africa",
    "Sub-Saharan Africa",
    "Australia and New Zealand",
    "Melanesia",
    "Micronesia",
    "Polynesia",
    "Unknown",
}


class CountryRegion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    country_code: str
    country_name: str
    macro_region: MacroRegion
    subregion: Subregion
    mapping_source: str
    mapping_version: str
    manual_override: bool = False
    override_reason: str = ""

    @field_validator("country_code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        normalized = value.upper()
        if len(normalized) != 2 or not normalized.isalpha():
            raise ValueError("country_code must be a two-letter analytical code")
        return normalized

    @model_validator(mode="after")
    def validate_override_reason(self) -> CountryRegion:
        if self.manual_override and not self.override_reason.strip():
            raise ValueError("manual overrides require a reason")
        return self


class RegionRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    mapping_version: str
    mapping_source: str
    convention_note: str
    countries: list[CountryRegion]

    @model_validator(mode="after")
    def validate_registry(self) -> RegionRegistry:
        codes = [country.country_code for country in self.countries]
        if len(codes) != len(set(codes)):
            raise ValueError("duplicate country rules are not allowed")
        unexpected_macro = {country.macro_region for country in self.countries}.difference(
            REQUIRED_MACRO_REGIONS
        )
        unexpected_subregions = {country.subregion for country in self.countries}.difference(
            REQUIRED_SUBREGIONS
        )
        if unexpected_macro or unexpected_subregions:
            raise ValueError(
                "unknown geographic values: "
                f"macro={unexpected_macro}, subregion={unexpected_subregions}"
            )
        present_macro = {country.macro_region for country in self.countries}
        if not {"Europe", "Asia", "Americas"}.issubset(present_macro):
            raise ValueError("mapping must include Europe, Asia, and Americas")
        return self

    def by_code(self) -> dict[str, CountryRegion]:
        return {country.country_code: country for country in self.countries}

    @property
    def semantic_hash(self) -> str:
        return semantic_hash(self)


UNKNOWN_REGION = CountryRegion(
    country_code="ZZ",
    country_name="Unknown",
    macro_region="Unknown",
    subregion="Unknown",
    mapping_source="Fallback for unmapped source codes",
    mapping_version="1",
)


def load_region_registry(path: str | Path = "config/regions.yml") -> RegionRegistry:
    return RegionRegistry.model_validate(load_yaml(path))


def map_country_code(code: str | None, registry: RegionRegistry) -> CountryRegion:
    if not code:
        return UNKNOWN_REGION
    return registry.by_code().get(code.upper(), UNKNOWN_REGION)


def write_mapping_csv(registry: RegionRegistry, path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    columns = list(CountryRegion.model_fields)
    temporary = destination.with_suffix(f"{destination.suffix}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
        writer.writeheader()
        for country in sorted(registry.countries, key=lambda item: item.country_code):
            writer.writerow(country.model_dump(mode="json"))
    with temporary.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if len(rows) != len(registry.countries):
        temporary.unlink(missing_ok=True)
        raise ValueError("region CSV row count validation failed")
    temporary.replace(destination)
