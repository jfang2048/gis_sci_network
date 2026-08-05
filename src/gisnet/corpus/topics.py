"""GIS Topic discovery, sampled-work evidence, and provisional registry freezing."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from gisnet.artifacts import load_json_object, write_json_artifact, write_yaml_artifact
from gisnet.atomic import atomic_write_text
from gisnet.config import config_file_hash, load_yaml, semantic_hash
from gisnet.openalex.cache import RawResponseCache
from gisnet.openalex.client import OpenAlexClient, OpenAlexError

CandidateScope = Literal["strict_candidate", "broad_candidate"]
CorpusMembership = Literal["strict", "broad_only", "excluded", "uncertain"]

_TOKEN = re.compile(r"[a-z0-9]+")
_OPENALEX_TOPIC = re.compile(r"^T\d+$")
_STOPWORDS = {
    "a",
    "and",
    "for",
    "in",
    "of",
    "on",
    "the",
    "to",
    "with",
    "methods",
    "method",
    "modelling",
    "modeling",
}


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class DiscoveryTerm(BaseModel):
    model_config = ConfigDict(extra="forbid")

    term_id: str
    term: str
    candidate_scope: CandidateScope
    method_family: str
    rationale: str

    @field_validator("term_id", "method_family")
    @classmethod
    def validate_slug(cls, value: str) -> str:
        if not re.fullmatch(r"[a-z0-9_]+", value):
            raise ValueError(
                "identifiers must contain only lowercase letters, digits, and underscores"
            )
        return value


class DiscoveryTermRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    registry_version: str
    terms: list[DiscoveryTerm]

    @model_validator(mode="after")
    def validate_terms(self) -> DiscoveryTermRegistry:
        ids = [term.term_id for term in self.terms]
        names = [term.term.casefold() for term in self.terms]
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise ValueError("discovery term IDs and terms must be unique")
        if len(self.terms) < 25:
            raise ValueError("the required discovery registry contains at least 25 terms")
        scopes = {term.candidate_scope for term in self.terms}
        if scopes != {"strict_candidate", "broad_candidate"}:
            raise ValueError("registry must contain strict and broad candidate terms")
        if any(not term.rationale.strip() for term in self.terms):
            raise ValueError("every discovery term requires a rationale")
        return self


def load_discovery_terms(path: str | Path = "config/discovery_terms.yml") -> DiscoveryTermRegistry:
    return DiscoveryTermRegistry.model_validate(load_yaml(path))


def short_openalex_id(value: Any, prefix: str) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    identifier = value.rstrip("/").rsplit("/", 1)[-1]
    return identifier if identifier.startswith(prefix) else None


def topic_id(value: Any) -> str | None:
    identifier = short_openalex_id(value, "T")
    return identifier if identifier and _OPENALEX_TOPIC.fullmatch(identifier) else None


def _tokens(value: str) -> set[str]:
    return {token for token in _TOKEN.findall(value.casefold()) if token not in _STOPWORDS}


def _lexical_score(term: str, topic: dict[str, Any]) -> float:
    term_tokens = _tokens(term)
    if not term_tokens:
        return 0.0
    name_tokens = _tokens(str(topic.get("display_name") or ""))
    description_tokens = _tokens(str(topic.get("description") or ""))
    keyword_tokens = _tokens(" ".join(map(str, topic.get("keywords") or [])))
    name_overlap = len(term_tokens & name_tokens) / len(term_tokens)
    keyword_overlap = len(term_tokens & keyword_tokens) / len(term_tokens)
    description_overlap = len(term_tokens & description_tokens) / len(term_tokens)
    exact_bonus = 0.25 if term.casefold() in str(topic.get("display_name") or "").casefold() else 0
    return round(
        min(
            1.0,
            0.55 * name_overlap + 0.25 * keyword_overlap + 0.2 * description_overlap + exact_bonus,
        ),
        6,
    )


def _raw_hash(value: dict[str, Any]) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def cached_get(
    client: OpenAlexClient,
    cache: RawResponseCache,
    endpoint: str,
    parameters: dict[str, Any],
    *,
    force: bool = False,
) -> tuple[dict[str, Any], str]:
    key = cache.make_key(endpoint, parameters)
    entry = None if force else cache.get(key)
    if entry is None:
        response = client.get(endpoint, params=parameters)
        entry = cache.put(
            endpoint=endpoint,
            parameters=parameters,
            data=response.data,
            status_code=response.status_code,
            retrieved_at_utc=response.retrieved_at_utc,
            rate_limit=response.rate_limit,
        )
    return entry.data, str(entry.metadata["retrieved_at_utc"])


def discover_candidate_topics(
    terms: DiscoveryTermRegistry,
    client: OpenAlexClient,
    cache: RawResponseCache,
    *,
    max_results_per_term: int = 5,
    force: bool = False,
) -> dict[str, Any]:
    if not 1 <= max_results_per_term <= 50:
        raise ValueError("max_results_per_term must be between 1 and 50")
    candidates: dict[str, dict[str, Any]] = {}
    retrieval_times: list[str] = []
    query_summaries: list[dict[str, Any]] = []
    for term in terms.terms:
        parameters = {"search": term.term, "per-page": max_results_per_term}
        data, retrieved_at = cached_get(client, cache, "/topics", parameters, force=force)
        retrieval_times.append(retrieved_at)
        results = data.get("results", [])
        if not isinstance(results, list):
            raise ValueError(f"Topic search returned no results list for {term.term_id}")
        raw_meta = data.get("meta")
        meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
        query_summaries.append(
            {
                "term_id": term.term_id,
                "term": term.term,
                "candidate_scope": term.candidate_scope,
                "returned_count": len(results),
                "source_match_count": meta.get("count"),
                "retrieved_at": retrieved_at,
            }
        )
        for rank, raw_topic in enumerate(results, start=1):
            if not isinstance(raw_topic, dict):
                continue
            identifier = topic_id(raw_topic.get("id"))
            if identifier is None:
                continue
            lexical = _lexical_score(term.term, raw_topic)
            rank_score = round(1 / rank, 6)
            evidence = {
                "term_id": term.term_id,
                "term": term.term,
                "candidate_scope": term.candidate_scope,
                "method_family": term.method_family,
                "search_rank": rank,
                "lexical_score": lexical,
                "rank_score": rank_score,
            }
            hierarchy = {
                name: (raw_topic.get(name) or {}).get("id")
                if isinstance(raw_topic.get(name), dict)
                else None
                for name in ("domain", "field", "subfield")
            }
            if identifier not in candidates:
                candidates[identifier] = {
                    "topic_id": identifier,
                    "display_name": raw_topic.get("display_name"),
                    "description": raw_topic.get("description"),
                    "keywords": raw_topic.get("keywords") or [],
                    "domain_id": hierarchy["domain"],
                    "field_id": hierarchy["field"],
                    "subfield_id": hierarchy["subfield"],
                    "domain_name": (raw_topic.get("domain") or {}).get("display_name")
                    if isinstance(raw_topic.get("domain"), dict)
                    else None,
                    "field_name": (raw_topic.get("field") or {}).get("display_name")
                    if isinstance(raw_topic.get("field"), dict)
                    else None,
                    "subfield_name": (raw_topic.get("subfield") or {}).get("display_name")
                    if isinstance(raw_topic.get("subfield"), dict)
                    else None,
                    "works_count": raw_topic.get("works_count"),
                    "discovery_evidence": [],
                    "raw_record_hash": _raw_hash(raw_topic),
                }
            candidates[identifier]["discovery_evidence"].append(evidence)

    records: list[dict[str, Any]] = []
    for candidate in candidates.values():
        evidence = candidate["discovery_evidence"]
        candidate["best_lexical_score"] = max(item["lexical_score"] for item in evidence)
        candidate["best_search_rank"] = min(item["search_rank"] for item in evidence)
        candidate["evidence_score"] = round(
            max(item["lexical_score"] + 0.15 * item["rank_score"] for item in evidence), 6
        )
        candidate["discovery_term_ids"] = sorted({item["term_id"] for item in evidence})
        candidate["candidate_scopes"] = sorted({item["candidate_scope"] for item in evidence})
        candidate["retrieved_at"] = max(retrieval_times) if retrieval_times else utc_timestamp()
        candidate["source_version"] = "OpenAlex Topics API retrieved 2026-08-05"
        records.append(candidate)
    records.sort(key=lambda row: (-row["evidence_score"], row["topic_id"]))
    return {
        "schema_version": 1,
        "registry_status": "candidate",
        "generated_at_utc": max(retrieval_times) if retrieval_times else utc_timestamp(),
        "discovery_registry_version": terms.registry_version,
        "discovery_terms_hash": semantic_hash(terms),
        "candidate_count": len(records),
        "query_summaries": query_summaries,
        "candidates": records,
    }


_YEAR_STRATA = ((2010, 2014), (2015, 2019), (2020, 2025))
_CITATION_STRATA = (("low", "<11", "publication_date:desc"), ("high", ">10", "cited_by_count:desc"))
_WORK_SELECT = (
    "id,title,publication_year,publication_date,cited_by_count,abstract_inverted_index,"
    "primary_topic,topics,authorships,primary_location,type,is_retracted,is_paratext,doi"
)


def _sample_work_record(
    work: dict[str, Any], *, candidate_topic_id: str, year_stratum: str, citation_stratum: str
) -> dict[str, Any]:
    raw_location = work.get("primary_location")
    location: dict[str, Any] = raw_location if isinstance(raw_location, dict) else {}
    raw_source = location.get("source")
    source: dict[str, Any] = raw_source if isinstance(raw_source, dict) else {}
    raw_primary_topic = work.get("primary_topic")
    primary_topic: dict[str, Any] = raw_primary_topic if isinstance(raw_primary_topic, dict) else {}
    institutions: dict[str, str | None] = {}
    for authorship in work.get("authorships") or []:
        if not isinstance(authorship, dict):
            continue
        for institution in authorship.get("institutions") or []:
            if isinstance(institution, dict):
                identifier = short_openalex_id(institution.get("id"), "I")
                if identifier:
                    institutions[identifier] = institution.get("display_name")
    topics = []
    for item in work.get("topics") or []:
        if not isinstance(item, dict):
            continue
        identifier = topic_id(item.get("id"))
        if identifier:
            topics.append(
                {
                    "topic_id": identifier,
                    "display_name": item.get("display_name"),
                    "score": item.get("score"),
                }
            )
    return {
        "candidate_topic_id": candidate_topic_id,
        "work_id": short_openalex_id(work.get("id"), "W"),
        "title": work.get("title"),
        "publication_year": work.get("publication_year"),
        "publication_date": work.get("publication_date"),
        "cited_by_count": work.get("cited_by_count"),
        "has_abstract": bool(work.get("abstract_inverted_index")),
        "source_id": short_openalex_id(source.get("id"), "S"),
        "source_name": source.get("display_name"),
        "work_type": work.get("type"),
        "doi": work.get("doi"),
        "is_retracted": work.get("is_retracted"),
        "is_paratext": work.get("is_paratext"),
        "primary_topic_id": topic_id(primary_topic.get("id")),
        "topics": topics,
        "institutions": [
            {"institution_id": identifier, "display_name": institutions[identifier]}
            for identifier in sorted(institutions)
        ],
        "year_stratum": year_stratum,
        "citation_stratum": citation_stratum,
    }


def sample_candidate_works(
    candidate_payload: dict[str, Any],
    client: OpenAlexClient,
    cache: RawResponseCache,
    *,
    force: bool = False,
) -> dict[str, Any]:
    candidates = candidate_payload.get("candidates", [])
    if not isinstance(candidates, list):
        raise ValueError("candidate registry lacks candidates")
    samples: list[dict[str, Any]] = []
    topic_reviews: list[dict[str, Any]] = []
    retrieval_times: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict) or not topic_id(candidate.get("topic_id")):
            continue
        identifier = str(candidate["topic_id"])
        topic_samples: dict[str, dict[str, Any]] = {}
        failures: list[dict[str, str]] = []
        for start_year, end_year in _YEAR_STRATA:
            year_label = f"{start_year}-{end_year}"
            for citation_label, citation_filter, sort in _CITATION_STRATA:
                filters = (
                    f"topics.id:{identifier},from_publication_date:{start_year}-01-01,"
                    f"to_publication_date:{end_year}-12-31,cited_by_count:{citation_filter}"
                )
                parameters = {
                    "filter": filters,
                    "select": _WORK_SELECT,
                    "sort": sort,
                    "per-page": 1,
                }
                try:
                    data, retrieved_at = cached_get(
                        client, cache, "/works", parameters, force=force
                    )
                    retrieval_times.append(retrieved_at)
                    results = data.get("results", [])
                    if isinstance(results, list) and results and isinstance(results[0], dict):
                        record = _sample_work_record(
                            results[0],
                            candidate_topic_id=identifier,
                            year_stratum=year_label,
                            citation_stratum=citation_label,
                        )
                        if record["work_id"]:
                            topic_samples[str(record["work_id"])] = record
                except OpenAlexError as exc:
                    failures.append(
                        {
                            "stratum": f"{year_label}|{citation_label}",
                            "failure_type": type(exc).__name__,
                        }
                    )
        selected = sorted(
            topic_samples.values(),
            key=lambda row: (row["publication_year"] or 0, row["citation_stratum"], row["work_id"]),
        )
        samples.extend(selected)
        topic_reviews.append(
            {
                "topic_id": identifier,
                "display_name": candidate.get("display_name"),
                "sample_count": len(selected),
                "review_status": "evidence_available" if selected else "insufficient_sample_data",
                "retrieval_failures": failures,
                "sample_work_ids": [record["work_id"] for record in selected],
            }
        )
    return {
        "schema_version": 1,
        "sampling_version": "topic-samples-2026-08-05-v1",
        "generated_at_utc": max(retrieval_times) if retrieval_times else utc_timestamp(),
        "candidate_registry_hash": semantic_hash(candidate_payload),
        "sample_count": len(samples),
        "topic_review_count": len(topic_reviews),
        "topic_reviews": topic_reviews,
        "samples": samples,
    }


def topic_review_markdown(candidate_payload: dict[str, Any], sample_payload: dict[str, Any]) -> str:
    candidates = {
        candidate["topic_id"]: candidate
        for candidate in candidate_payload.get("candidates", [])
        if isinstance(candidate, dict) and candidate.get("topic_id")
    }
    samples_by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in sample_payload.get("samples", []):
        if isinstance(sample, dict):
            samples_by_topic[str(sample.get("candidate_topic_id"))].append(sample)
    lines = [
        "# OpenAlex Topic Review Evidence",
        "",
        "> Generated evidence for provisional review; no human review is implied.",
        "",
        f"Candidates: {len(candidates)}  ",
        f"Sample works: {len(sample_payload.get('samples', []))}",
        "",
    ]
    for identifier in sorted(candidates):
        candidate = candidates[identifier]
        lines.extend(
            [
                f"## {identifier} — {candidate.get('display_name')}",
                "",
                str(candidate.get("description") or "_No description supplied._"),
                "",
                f"- Discovery terms: {', '.join(candidate.get('discovery_term_ids', []))}",
                "- Hierarchy: "
                f"{candidate.get('domain_name')} / {candidate.get('field_name')} / "
                f"{candidate.get('subfield_name')}",
                f"- Evidence score: {candidate.get('evidence_score')}",
                "",
                "| Year | Citations | Work | Primary Topic | Source |",
                "|---:|---:|---|---|---|",
            ]
        )
        topic_samples = samples_by_topic.get(identifier, [])
        if not topic_samples:
            lines.append("| — | — | _Insufficient sample data_ | — | — |")
        for sample in topic_samples:
            title = str(sample.get("title") or "Untitled").replace("|", "\\|")
            source = str(sample.get("source_name") or "Unknown").replace("|", "\\|")
            lines.append(
                f"| {sample.get('publication_year')} | {sample.get('cited_by_count')} | "
                f"{title} ({sample.get('work_id')}) | {sample.get('primary_topic_id')} | {source} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_candidate_artifact(
    payload: dict[str, Any],
    *,
    path: str | Path,
    run_id: str,
    terms_path: str | Path,
    command: str,
) -> None:
    write_json_artifact(
        path=path,
        dataset_name="topic_candidates",
        payload=payload,
        records=payload["candidates"],
        primary_key=["topic_id"],
        run_id=run_id,
        config_hashes={"discovery_terms": config_file_hash(terms_path)},
        source_versions={"openalex_topics": "retrieved-2026-08-05"},
        command=command,
    )


def write_sample_artifacts(
    candidate_payload: dict[str, Any],
    sample_payload: dict[str, Any],
    *,
    path: str | Path,
    report_path: str | Path,
    run_id: str,
    candidate_manifest: str,
    command: str,
) -> None:
    write_json_artifact(
        path=path,
        dataset_name="topic_work_samples",
        payload=sample_payload,
        records=sample_payload["samples"],
        primary_key=["candidate_topic_id", "work_id"],
        run_id=run_id,
        config_hashes={"candidate_registry": semantic_hash(candidate_payload)},
        source_versions={"openalex_works": "retrieved-2026-08-05"},
        source_manifests=[candidate_manifest],
        command=command,
    )
    atomic_write_text(report_path, topic_review_markdown(candidate_payload, sample_payload))


def load_candidate_payload(path: str | Path) -> dict[str, Any]:
    return load_json_object(path)


class TopicDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: str
    corpus_membership: CorpusMembership
    method_family: str
    decision_reason: str

    @field_validator("topic_id")
    @classmethod
    def validate_topic_identifier(cls, value: str) -> str:
        if not _OPENALEX_TOPIC.fullmatch(value):
            raise ValueError("Topic decisions require real-looking OpenAlex Topic IDs")
        return value


class TopicDecisionRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    decision_version: str
    review_status: Literal["provisional", "human_reviewed"]
    review_note: str
    decisions: list[TopicDecision]

    @model_validator(mode="after")
    def validate_decisions(self) -> TopicDecisionRegistry:
        identifiers = [decision.topic_id for decision in self.decisions]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("Topic decisions must be unique by Topic ID")
        if any(not decision.decision_reason.strip() for decision in self.decisions):
            raise ValueError("every Topic decision requires a reason")
        return self


def load_topic_decisions(path: str | Path = "config/topic_decisions.yml") -> TopicDecisionRegistry:
    return TopicDecisionRegistry.model_validate(load_yaml(path))


def freeze_topic_registry(
    candidate_payload: dict[str, Any],
    sample_payload: dict[str, Any],
    decisions: TopicDecisionRegistry,
) -> dict[str, Any]:
    candidates = {
        str(candidate["topic_id"]): candidate
        for candidate in candidate_payload.get("candidates", [])
        if isinstance(candidate, dict) and topic_id(candidate.get("topic_id"))
    }
    decision_map = {decision.topic_id: decision for decision in decisions.decisions}
    if set(candidates) != set(decision_map):
        missing = sorted(set(candidates) - set(decision_map))
        extra = sorted(set(decision_map) - set(candidates))
        raise ValueError(f"Topic decision coverage mismatch: missing={missing}, extra={extra}")
    review_map = {
        str(review["topic_id"]): review
        for review in sample_payload.get("topic_reviews", [])
        if isinstance(review, dict) and review.get("topic_id")
    }
    sample_ids: dict[str, list[str]] = defaultdict(list)
    for sample in sample_payload.get("samples", []):
        if isinstance(sample, dict) and sample.get("candidate_topic_id") and sample.get("work_id"):
            sample_ids[str(sample["candidate_topic_id"])].append(str(sample["work_id"]))

    records: list[dict[str, Any]] = []
    for identifier in sorted(candidates):
        candidate = candidates[identifier]
        decision = decision_map[identifier]
        record = {
            "topic_id": identifier,
            "display_name": candidate.get("display_name"),
            "description": candidate.get("description"),
            "keywords": candidate.get("keywords") or [],
            "domain_id": candidate.get("domain_id"),
            "field_id": candidate.get("field_id"),
            "subfield_id": candidate.get("subfield_id"),
            "corpus_membership": decision.corpus_membership,
            "method_family": decision.method_family,
            "decision": decision.corpus_membership,
            "decision_reason": decision.decision_reason,
            "review_status": decisions.review_status,
            "sample_review_status": review_map.get(identifier, {}).get(
                "review_status", "insufficient_sample_data"
            ),
            "sample_work_ids": sorted(set(sample_ids.get(identifier, []))),
            "discovery_term_ids": candidate.get("discovery_term_ids") or [],
            "candidate_evidence_score": candidate.get("evidence_score"),
            "retrieved_at": candidate.get("retrieved_at"),
            "source_version": candidate.get("source_version"),
        }
        records.append(record)
    strict_ids = [
        record["topic_id"] for record in records if record["corpus_membership"] == "strict"
    ]
    broad_only_ids = [
        record["topic_id"] for record in records if record["corpus_membership"] == "broad_only"
    ]
    uncertain_ids = [
        record["topic_id"] for record in records if record["corpus_membership"] == "uncertain"
    ]
    body = {
        "schema_version": 1,
        "registry_version": decisions.decision_version,
        "review_status": decisions.review_status,
        "review_note": decisions.review_note,
        "candidate_registry_hash": semantic_hash(candidate_payload),
        "sample_registry_hash": semantic_hash(sample_payload),
        "strict_topic_ids": strict_ids,
        "broad_topic_ids": sorted(strict_ids + broad_only_ids),
        "uncertain_topic_ids": uncertain_ids,
        "topics": records,
    }
    body["registry_hash"] = semantic_hash(body)
    return body


def write_frozen_topic_registry(
    payload: dict[str, Any],
    *,
    path: str | Path,
    run_id: str,
    decisions_path: str | Path,
    command: str,
) -> None:
    write_yaml_artifact(
        path=path,
        dataset_name="topic_registry",
        payload=payload,
        records=payload["topics"],
        primary_key=["topic_id"],
        run_id=run_id,
        config_hashes={
            "topic_decisions": config_file_hash(decisions_path),
            "candidate_registry": payload["candidate_registry_hash"],
            "sample_registry": payload["sample_registry_hash"],
        },
        source_versions={"openalex_topics_and_works": "retrieved-2026-08-05"},
        source_manifests=[
            ".agent/manifests/topic_candidates.json",
            ".agent/manifests/topic_work_samples.json",
        ],
        command=command,
    )


def load_frozen_topic_registry(path: str | Path = "config/topic_registry.yml") -> dict[str, Any]:
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("topics"), list):
        raise ValueError(f"invalid frozen Topic registry: {path}")
    strict = set(value.get("strict_topic_ids", []))
    broad = set(value.get("broad_topic_ids", []))
    if not strict.issubset(broad):
        raise ValueError("Strict Topic IDs must be a subset of Broad Topic IDs")
    if any(not topic.get("decision_reason") for topic in value["topics"]):
        raise ValueError("every Topic requires a decision reason")
    return value
