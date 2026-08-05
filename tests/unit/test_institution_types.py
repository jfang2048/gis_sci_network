from pathlib import Path
from typing import Any

from gisnet.institutions.types import (
    load_institution_type_policy,
    profile_institution_types,
)
from gisnet.openalex.cache import RawResponseCache
from gisnet.openalex.client import OpenAlexResponse


class InstitutionClient:
    def get(self, _: str, *, params: dict[str, Any]) -> OpenAlexResponse:
        assert params["group_by"] == "type"
        return OpenAlexResponse(
            data={
                "group_by": [
                    {"key": "education", "key_display_name": "education", "count": 10},
                    {"key": "future_type", "key_display_name": "future_type", "count": 2},
                ]
            },
            status_code=200,
            retrieved_at_utc="2026-08-05T00:00:00Z",
            rate_limit={},
        )


def test_policy_maps_primary_secondary_excluded_and_unknown() -> None:
    policy = load_institution_type_policy()
    assert policy.map_type("education").is_primary_research_scope
    assert policy.map_type("company").analytical_scope == "secondary"
    assert policy.map_type("funder").analytical_scope == "excluded"
    assert policy.map_type("future_type").analytical_scope == "unknown"
    assert policy.map_type(None).normalized_category == "unknown"


def test_profile_records_unknown_future_type_without_exception(tmp_path: Path) -> None:
    payload = profile_institution_types(
        InstitutionClient(),  # type: ignore[arg-type]
        RawResponseCache(tmp_path / "cache"),
        load_institution_type_policy(),
    )
    assert payload["observed_type_count"] == 2
    assert payload["unmapped_observed_types"] == ["future_type"]
    future = next(row for row in payload["records"] if row["source_type"] == "future_type")
    assert future["analytical_scope"] == "unknown"
    assert not future["is_explicitly_configured"]
