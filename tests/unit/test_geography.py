from pathlib import Path

import pytest
from pydantic import ValidationError

from gisnet.geography import (
    RegionRegistry,
    load_region_registry,
    map_country_code,
    write_mapping_csv,
)


@pytest.fixture(scope="module")
def registry() -> RegionRegistry:
    return load_region_registry("config/regions.yml")


def test_mapping_is_complete_and_unique(registry: RegionRegistry) -> None:
    codes = [country.country_code for country in registry.countries]
    assert len(codes) == len(set(codes)) == 251
    assert len([code for code in codes if code not in {"XK", "ZZ"}]) == 249
    assert all(country.macro_region and country.subregion for country in registry.countries)


def test_primary_macro_regions_are_geographically_broad(registry: RegionRegistry) -> None:
    macro_counts: dict[str, int] = {}
    for country in registry.countries:
        macro_counts[country.macro_region] = macro_counts.get(country.macro_region, 0) + 1
    assert macro_counts["Europe"] > 10
    assert macro_counts["Asia"] > 10
    assert macro_counts["Americas"] > 10

    asia = {c.subregion for c in registry.countries if c.macro_region == "Asia"}
    americas = {c.subregion for c in registry.countries if c.macro_region == "Americas"}
    assert asia == {
        "Eastern Asia",
        "Southeastern Asia",
        "Southern Asia",
        "Central Asia",
        "Western Asia",
    }
    assert americas == {"Northern America", "Central America", "Caribbean", "South America"}


@pytest.mark.parametrize(
    ("code", "macro", "subregion"),
    [
        ("RU", "Europe", "Eastern Europe"),
        ("TR", "Asia", "Western Asia"),
        ("KZ", "Asia", "Central Asia"),
        ("CY", "Asia", "Western Asia"),
        ("GL", "Americas", "Northern America"),
        ("HK", "Asia", "Eastern Asia"),
        ("MO", "Asia", "Eastern Asia"),
        ("TW", "Asia", "Eastern Asia"),
        ("XK", "Europe", "Southern Europe"),
    ],
)
def test_special_country_conventions(
    registry: RegionRegistry, code: str, macro: str, subregion: str
) -> None:
    mapped = map_country_code(code, registry)
    assert (mapped.macro_region, mapped.subregion) == (macro, subregion)


def test_unknown_codes_remain_unknown(registry: RegionRegistry) -> None:
    mapped = map_country_code("XX", registry)
    assert mapped.macro_region == mapped.subregion == "Unknown"


def test_duplicate_country_rules_fail() -> None:
    row = {
        "country_code": "AA",
        "country_name": "Test",
        "macro_region": "Unknown",
        "subregion": "Unknown",
        "mapping_source": "test",
        "mapping_version": "1",
    }
    with pytest.raises(ValidationError, match="duplicate country"):
        RegionRegistry.model_validate(
            {
                "mapping_version": "1",
                "mapping_source": "test",
                "convention_note": "test",
                "countries": [row, row],
            }
        )


def test_csv_export_reconciles_rows(registry: RegionRegistry, tmp_path: Path) -> None:
    destination = tmp_path / "regions.csv"
    write_mapping_csv(registry, destination)
    assert len(destination.read_text(encoding="utf-8").splitlines()) == 252
