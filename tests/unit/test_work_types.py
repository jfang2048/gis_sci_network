from pathlib import Path
from typing import Any

from gisnet.corpus.work_types import load_work_type_policy, profile_work_types
from gisnet.openalex.cache import RawResponseCache
from gisnet.openalex.client import OpenAlexResponse


class WorkTypeClient:
    def get(self, _: str, *, params: dict[str, Any]) -> OpenAlexResponse:
        if params.get("group_by") == "type":
            data = {
                "group_by": [
                    {
                        "key": "https://openalex.org/types/article",
                        "key_display_name": "article",
                        "count": 100,
                    },
                    {
                        "key": "https://openalex.org/types/future",
                        "key_display_name": "future",
                        "count": 1,
                    },
                ]
            }
        else:
            inspected_type = str(params["filter"]).rsplit("type:", 1)[-1]
            data = {
                "results": [
                    {
                        "id": f"https://openalex.org/W{len(inspected_type)}",
                        "title": inspected_type,
                        "publication_year": 2020,
                        "type": inspected_type,
                        "is_retracted": False,
                        "is_paratext": False,
                        "primary_topic": {"id": "https://openalex.org/T1"},
                        "primary_location": {"source": {"display_name": "Journal"}},
                    }
                ]
            }
        return OpenAlexResponse(
            data=data,
            status_code=200,
            retrieved_at_utc="2026-08-05T00:00:00Z",
            rate_limit={},
        )


def test_work_type_policy_views_and_unknown_fallback() -> None:
    policy = load_work_type_policy()
    assert policy.map_type("article").primary
    assert not policy.map_type("preprint").primary
    assert policy.map_type("preprint").preprint_sensitivity
    assert policy.map_type("conference-abstract").expanded_sensitivity
    assert not policy.map_type("future").primary


def test_profile_covers_both_corpora_and_inspection_types(tmp_path: Path) -> None:
    payload = profile_work_types(
        WorkTypeClient(),  # type: ignore[arg-type]
        RawResponseCache(tmp_path / "cache"),
        load_work_type_policy(),
        {"strict_topic_ids": ["T1"], "broad_topic_ids": ["T1", "T2"]},
    )
    assert len(payload["records"]) == 4
    assert len(payload["inspection_samples"]) == 6
    assert payload["unmapped_observed_types"] == ["future"]
    assert {row["corpus_view"] for row in payload["records"]} == {"strict", "broad"}
