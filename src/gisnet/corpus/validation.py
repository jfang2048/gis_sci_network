"""Deterministic corpus-boundary sampling, annotations, and supported metrics."""

from __future__ import annotations

import csv
import hashlib
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal

from gisnet.artifacts import write_json_artifact
from gisnet.atomic import atomic_write_text
from gisnet.config import config_file_hash, semantic_hash

Annotation = Literal["relevant", "irrelevant", "uncertain", ""]

ANNOTATION_COLUMNS = [
    "sample_id",
    "work_id",
    "title",
    "publication_year",
    "candidate_topic_id",
    "topic_name",
    "registry_membership",
    "sample_group",
    "label",
    "annotator",
    "notes",
    "selection_reason",
]


def _selection_hash(seed: int, candidate_topic_id: str, work_id: str) -> str:
    return hashlib.sha256(f"{seed}|{candidate_topic_id}|{work_id}".encode()).hexdigest()


def _existing_annotations(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    annotations: dict[str, dict[str, str]] = {}
    for row in rows:
        label = row.get("label", "")
        if label not in {"", "relevant", "irrelevant", "uncertain"}:
            raise ValueError(f"invalid corpus annotation label: {label}")
        if row.get("sample_id"):
            annotations[str(row["sample_id"])] = row
    return annotations


def build_boundary_sample(
    topic_registry: dict[str, Any],
    sample_payload: dict[str, Any],
    *,
    seed: int,
    per_group: int = 12,
    existing_annotation_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    if per_group < 1:
        raise ValueError("per_group must be positive")
    topics = {
        str(topic["topic_id"]): topic
        for topic in topic_registry.get("topics", [])
        if isinstance(topic, dict) and topic.get("topic_id")
    }
    group_for_membership = {
        "strict": "strict",
        "broad_only": "broad_only",
        "excluded": "excluded_or_uncertain_control",
        "uncertain": "excluded_or_uncertain_control",
    }
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for sample in sample_payload.get("samples", []):
        if not isinstance(sample, dict):
            continue
        topic = topics.get(str(sample.get("candidate_topic_id")))
        if not topic:
            continue
        membership = str(topic["corpus_membership"])
        group = group_for_membership[membership]
        grouped[group].append(
            {
                "sample_id": hashlib.sha256(
                    f"{sample.get('candidate_topic_id')}|{sample.get('work_id')}".encode()
                ).hexdigest()[:16],
                "work_id": sample.get("work_id"),
                "title": sample.get("title"),
                "publication_year": sample.get("publication_year"),
                "candidate_topic_id": sample.get("candidate_topic_id"),
                "topic_name": topic.get("display_name"),
                "registry_membership": membership,
                "sample_group": group,
                "label": "",
                "annotator": "",
                "notes": "",
                "selection_reason": "deterministic_hash_within_registry_membership",
            }
        )
    existing = (
        _existing_annotations(Path(existing_annotation_path)) if existing_annotation_path else {}
    )
    selected: list[dict[str, Any]] = []
    for group in ("strict", "broad_only", "excluded_or_uncertain_control"):
        ordered = sorted(
            grouped.get(group, []),
            key=lambda row: _selection_hash(
                seed, str(row["candidate_topic_id"]), str(row["work_id"])
            ),
        )
        for row in ordered[:per_group]:
            previous = existing.get(str(row["sample_id"]))
            if previous:
                row["label"] = previous.get("label", "")
                row["annotator"] = previous.get("annotator", "")
                row["notes"] = previous.get("notes", "")
            selected.append(row)
    return selected


def write_annotation_sheet(records: list[dict[str, Any]], path: str | Path) -> None:
    lines: list[str] = []
    from io import StringIO

    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=ANNOTATION_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow({column: record.get(column, "") for column in ANNOTATION_COLUMNS})
    lines.append(buffer.getvalue())
    atomic_write_text(path, "".join(lines))


def load_known_positives(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(encoding="utf-8", newline="") as handle:
        records = list(csv.DictReader(handle))
    required = {"work_id", "expected_corpus", "title", "reason", "provenance"}
    if set(records[0] if records else required) != required:
        raise ValueError("known-positive columns are invalid")
    for record in records:
        if record["expected_corpus"] not in {"strict", "broad"}:
            raise ValueError("known positives must specify strict or broad")
        if not all(record.get(field, "").strip() for field in required):
            raise ValueError("known-positive records require all fields")
    return records


def evaluate_boundary(
    records: list[dict[str, Any]],
    known_positives: list[dict[str, str]],
    topic_registry: dict[str, Any],
    sample_payload: dict[str, Any],
    *,
    minimum_precision_labels: int = 10,
) -> dict[str, Any]:
    labels = [record for record in records if record.get("label") in {"relevant", "irrelevant"}]
    if len(labels) >= minimum_precision_labels:
        predicted_included = [
            record for record in labels if record["registry_membership"] in {"strict", "broad_only"}
        ]
        relevant = sum(record["label"] == "relevant" for record in predicted_included)
        precision: dict[str, Any] = {
            "status": "measured",
            "label_count": len(labels),
            "included_label_count": len(predicted_included),
            "value": relevant / len(predicted_included) if predicted_included else None,
        }
    else:
        precision = {
            "status": "not_estimated",
            "label_count": len(labels),
            "minimum_required": minimum_precision_labels,
            "reason": "insufficient human labels",
            "value": None,
        }

    work_topic_ids: dict[str, set[str]] = defaultdict(set)
    for sample in sample_payload.get("samples", []):
        if not isinstance(sample, dict) or not sample.get("work_id"):
            continue
        for topic in sample.get("topics") or []:
            if isinstance(topic, dict) and topic.get("topic_id"):
                work_topic_ids[str(sample["work_id"])].add(str(topic["topic_id"]))
    strict_ids = set(topic_registry.get("strict_topic_ids", []))
    broad_ids = set(topic_registry.get("broad_topic_ids", []))
    coverage_records = []
    for known in known_positives:
        expected_ids = strict_ids if known["expected_corpus"] == "strict" else broad_ids
        available_topics = work_topic_ids.get(known["work_id"], set())
        coverage_records.append(
            {
                **known,
                "observed_topic_ids": sorted(available_topics),
                "is_recovered": bool(expected_ids & available_topics),
            }
        )
    recall_supported = len(coverage_records) >= 5 and all(
        record["observed_topic_ids"] for record in coverage_records
    )
    recovered_count = sum(bool(record["is_recovered"]) for record in coverage_records)
    recall = {
        "status": "measured" if recall_supported else "not_estimated",
        "reference_count": len(coverage_records),
        "recovered_count": recovered_count,
        "value": (recovered_count / len(coverage_records) if recall_supported else None),
        "reason": "manually reviewed known-positive reference set"
        if recall_supported
        else "reference set lacks sufficient observed Topic evidence",
    }
    membership_counts = Counter(
        str(topic["corpus_membership"])
        for topic in topic_registry.get("topics", [])
        if isinstance(topic, dict)
    )
    return {
        "schema_version": 1,
        "validation_version": "corpus-boundary-2026-08-05-v1",
        "registry_hash": topic_registry.get("registry_hash"),
        "annotation_sample_hash": semantic_hash(records),
        "annotation_counts": dict(
            Counter(str(record.get("label") or "unlabelled") for record in records)
        ),
        "precision": precision,
        "known_positive_recall": recall,
        "known_positive_results": coverage_records,
        "strict_topic_count": membership_counts["strict"],
        "broad_only_topic_count": membership_counts["broad_only"],
        "broad_topic_count": membership_counts["strict"] + membership_counts["broad_only"],
        "uncertain_topic_count": membership_counts["uncertain"],
        "excluded_topic_count": membership_counts["excluded"],
    }


def boundary_report(metrics: dict[str, Any], records: list[dict[str, Any]]) -> str:
    precision = metrics["precision"]
    recall = metrics["known_positive_recall"]
    group_counts = Counter(str(record["sample_group"]) for record in records)
    precision_text = (
        f"{precision['value']:.3f} from {precision['included_label_count']} included labels"
        if precision["status"] == "measured" and precision["value"] is not None
        else f"not estimated ({precision['reason']}; labels={precision['label_count']})"
    )
    recall_text = (
        f"{recall['value']:.3f} ({recall['recovered_count']}/{recall['reference_count']})"
        if recall["status"] == "measured" and recall["value"] is not None
        else f"not estimated ({recall['reason']})"
    )
    return f"""# Corpus Boundary Validation

> The Topic registry is provisional and has not received human review.

## Deterministic annotation sample

- Strict candidates: {group_counts["strict"]}
- Broad-only candidates: {group_counts["broad_only"]}
- Excluded/uncertain controls: {group_counts["excluded_or_uncertain_control"]}
- Human-label precision: {precision_text}

The annotation sheet accepts only `relevant`, `irrelevant`, or `uncertain`. Precision is not
reported until at least {precision.get("minimum_required", 10)} relevant/irrelevant labels exist.

## Known-positive check

- Recall: {recall_text}
- Reference basis: manually reviewed real OpenAlex works already present in Topic samples.

This recall applies only to the small reference set and is not a population-wide recall estimate.

## Strict versus Broad

- Strict Topic count: {metrics["strict_topic_count"]}
- Broad-only Topic count: {metrics["broad_only_topic_count"]}
- Broad total (including Strict): {metrics["broad_topic_count"]}
- Uncertain sensitivity Topics: {metrics["uncertain_topic_count"]}
- Excluded Topics: {metrics["excluded_topic_count"]}

Broad adds remote sensing, photogrammetry, positioning, geospatial computer vision,
digital-twin/3D city modelling, and applied environmental, urban, and transport spatial methods.
"""


def write_boundary_artifacts(
    metrics: dict[str, Any],
    records: list[dict[str, Any]],
    *,
    metrics_path: str | Path,
    report_path: str | Path,
    run_id: str,
    registry_path: str | Path,
    known_positive_path: str | Path,
    command: str,
) -> None:
    write_json_artifact(
        path=metrics_path,
        dataset_name="corpus_boundary_validation",
        payload=metrics,
        records=metrics["known_positive_results"],
        primary_key=["work_id"],
        run_id=run_id,
        config_hashes={
            "topic_registry": config_file_hash(registry_path),
            "known_positive_works": hashlib.sha256(
                Path(known_positive_path).read_bytes()
            ).hexdigest(),
        },
        source_versions={"openalex_topic_samples": "retrieved-2026-08-05"},
        source_manifests=[
            ".agent/manifests/topic_registry.json",
            ".agent/manifests/topic_work_samples.json",
        ],
        command=command,
    )
    atomic_write_text(report_path, boundary_report(metrics, records))
