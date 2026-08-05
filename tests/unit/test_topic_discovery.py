from pathlib import Path
from typing import Any

from gisnet.corpus.topics import (
    DiscoveryTermRegistry,
    TopicDecisionRegistry,
    discover_candidate_topics,
    freeze_topic_registry,
    load_discovery_terms,
    sample_candidate_works,
    topic_review_markdown,
)
from gisnet.openalex.cache import RawResponseCache
from gisnet.openalex.client import OpenAlexResponse


class TopicClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get(self, _: str, *, params: dict[str, Any]) -> OpenAlexResponse:
        self.calls.append(params)
        term = str(params["search"])
        topic = {
            "id": "https://openalex.org/T10757",
            "display_name": "Geographic Information Systems Studies",
            "description": f"Geospatial methods including {term}.",
            "keywords": [term, "Spatial Data Infrastructure"],
            "domain": {
                "id": "https://openalex.org/domains/2",
                "display_name": "Social Sciences",
            },
            "field": {
                "id": "https://openalex.org/fields/33",
                "display_name": "Social Sciences",
            },
            "subfield": {
                "id": "https://openalex.org/subfields/3305",
                "display_name": "Geography",
            },
            "works_count": 100,
        }
        return OpenAlexResponse(
            data={"results": [topic], "meta": {}},
            status_code=200,
            retrieved_at_utc="2026-08-05T00:00:00Z",
            rate_limit={},
        )


class WorkClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get(self, _: str, *, params: dict[str, Any]) -> OpenAlexResponse:
        self.calls.append(params)
        filters = str(params["filter"])
        start_year = int(filters.split("from_publication_date:", 1)[1][:4])
        citation_label = "low" if "cited_by_count:<11" in filters else "high"
        suffix = "L" if citation_label == "low" else "H"
        work = {
            "id": f"https://openalex.org/W{start_year}{1 if suffix == 'L' else 2}",
            "title": f"GIS sample {start_year} {suffix}",
            "publication_year": start_year,
            "publication_date": f"{start_year}-01-01",
            "cited_by_count": 2 if suffix == "L" else 20,
            "abstract_inverted_index": {"gis": [0]},
            "primary_topic": {"id": "https://openalex.org/T10757"},
            "topics": [
                {
                    "id": "https://openalex.org/T10757",
                    "display_name": "GIS",
                    "score": 0.9,
                }
            ],
            "authorships": [
                {
                    "institutions": [
                        {
                            "id": "https://openalex.org/I1",
                            "display_name": "Example University",
                        }
                    ]
                }
            ],
            "primary_location": {
                "source": {"id": "https://openalex.org/S1", "display_name": "GIS Journal"}
            },
            "type": "article",
            "doi": "https://doi.org/10.test/example",
            "is_retracted": False,
            "is_paratext": False,
        }
        return OpenAlexResponse(
            data={"results": [work], "meta": {}},
            status_code=200,
            retrieved_at_utc="2026-08-05T00:00:00Z",
            rate_limit={},
        )


def test_required_discovery_terms_are_complete_and_source_id_independent() -> None:
    registry = load_discovery_terms()
    assert len(registry.terms) == 25
    assert {term.candidate_scope for term in registry.terms} == {
        "strict_candidate",
        "broad_candidate",
    }
    assert all("T" not in term.term_id for term in registry.terms)
    assert all(term.rationale for term in registry.terms)


def test_discovery_deduplicates_and_traces_every_term(tmp_path: Path) -> None:
    terms = load_discovery_terms()
    client = TopicClient()
    cache = RawResponseCache(tmp_path / "cache")
    payload = discover_candidate_topics(terms, client, cache, max_results_per_term=3)
    assert payload["candidate_count"] == 1
    candidate = payload["candidates"][0]
    assert candidate["topic_id"] == "T10757"
    assert len(candidate["discovery_term_ids"]) == 25
    assert candidate["domain_id"] == "https://openalex.org/domains/2"
    assert len(payload["query_summaries"]) == 25
    assert len(client.calls) == 25

    cached_client = TopicClient()
    discover_candidate_topics(terms, cached_client, cache, max_results_per_term=3)
    assert cached_client.calls == []


def test_work_sampling_is_deterministic_and_stratified(tmp_path: Path) -> None:
    candidates = {
        "candidates": [{"topic_id": "T10757", "display_name": "GIS"}],
    }
    client = WorkClient()
    cache = RawResponseCache(tmp_path / "cache")
    payload = sample_candidate_works(candidates, client, cache)
    assert payload["sample_count"] == 6
    assert {sample["year_stratum"] for sample in payload["samples"]} == {
        "2010-2014",
        "2015-2019",
        "2020-2025",
    }
    assert {sample["citation_stratum"] for sample in payload["samples"]} == {"low", "high"}
    assert len(client.calls) == 6
    assert payload["samples"][0]["institutions"][0]["institution_id"] == "I1"
    assert "T10757" in topic_review_markdown(candidates, payload)


def test_discovery_registry_rejects_missing_required_term_coverage() -> None:
    try:
        DiscoveryTermRegistry.model_validate(
            {
                "registry_version": "bad",
                "terms": [
                    {
                        "term_id": "gis",
                        "term": "GIS",
                        "candidate_scope": "strict_candidate",
                        "method_family": "gis",
                        "rationale": "test",
                    }
                ],
            }
        )
    except ValueError as exc:
        assert "at least 25" in str(exc)
    else:
        raise AssertionError("incomplete term registry unexpectedly validated")


def test_freeze_registry_requires_complete_decisions_and_keeps_strict_in_broad() -> None:
    candidates = {
        "candidates": [
            {
                "topic_id": "T1",
                "display_name": "GIS",
                "description": "GIS methods",
                "retrieved_at": "2026-08-05T00:00:00Z",
                "source_version": "test",
            },
            {
                "topic_id": "T2",
                "display_name": "Remote sensing",
                "description": "Earth observation",
                "retrieved_at": "2026-08-05T00:00:00Z",
                "source_version": "test",
            },
        ]
    }
    samples = {
        "topic_reviews": [
            {"topic_id": "T1", "review_status": "evidence_available"},
            {"topic_id": "T2", "review_status": "evidence_available"},
        ],
        "samples": [
            {"candidate_topic_id": "T1", "work_id": "W1"},
            {"candidate_topic_id": "T2", "work_id": "W2"},
        ],
    }
    decisions = TopicDecisionRegistry.model_validate(
        {
            "decision_version": "test-v1",
            "review_status": "provisional",
            "review_note": "test",
            "decisions": [
                {
                    "topic_id": "T1",
                    "corpus_membership": "strict",
                    "method_family": "core_gis",
                    "decision_reason": "core method",
                },
                {
                    "topic_id": "T2",
                    "corpus_membership": "broad_only",
                    "method_family": "remote_sensing",
                    "decision_reason": "broad method",
                },
            ],
        }
    )
    registry = freeze_topic_registry(candidates, samples, decisions)
    assert registry["strict_topic_ids"] == ["T1"]
    assert registry["broad_topic_ids"] == ["T1", "T2"]
    assert all(topic["decision_reason"] for topic in registry["topics"])


def test_live_decision_file_covers_every_discovered_candidate() -> None:
    import json

    from gisnet.corpus.topics import load_topic_decisions

    candidates = json.loads(Path("data/reference/topic_candidates.json").read_text())
    decisions = load_topic_decisions()
    assert {candidate["topic_id"] for candidate in candidates["candidates"]} == {
        decision.topic_id for decision in decisions.decisions
    }
