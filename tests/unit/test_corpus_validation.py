import csv
from pathlib import Path

from gisnet.corpus.validation import (
    boundary_report,
    build_boundary_sample,
    evaluate_boundary,
    write_annotation_sheet,
)


def _fixtures() -> tuple[dict[str, object], dict[str, object]]:
    registry: dict[str, object] = {
        "registry_hash": "test",
        "strict_topic_ids": ["T1"],
        "broad_topic_ids": ["T1", "T2"],
        "topics": [
            {"topic_id": "T1", "display_name": "GIS", "corpus_membership": "strict"},
            {
                "topic_id": "T2",
                "display_name": "Remote sensing",
                "corpus_membership": "broad_only",
            },
            {"topic_id": "T3", "display_name": "Other", "corpus_membership": "excluded"},
        ],
    }
    samples: dict[str, object] = {
        "samples": [
            {
                "candidate_topic_id": topic,
                "work_id": f"W{index}",
                "title": topic,
                "publication_year": 2020,
                "topics": [{"topic_id": topic}],
            }
            for index, topic in enumerate(("T1", "T2", "T3"), start=1)
        ]
    }
    return registry, samples


def test_boundary_sample_is_deterministic_and_preserves_labels(tmp_path: Path) -> None:
    registry, samples = _fixtures()
    first = build_boundary_sample(registry, samples, seed=7, per_group=1)
    annotation_path = tmp_path / "annotations.csv"
    write_annotation_sheet(first, annotation_path)
    with annotation_path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["label"] = "relevant"
    rows[0]["annotator"] = "tester"
    with annotation_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    second = build_boundary_sample(
        registry, samples, seed=7, per_group=1, existing_annotation_path=annotation_path
    )
    assert [row["sample_id"] for row in first] == [row["sample_id"] for row in second]
    assert second[0]["label"] == "relevant"
    assert second[0]["annotator"] == "tester"


def test_precision_is_withheld_without_labels_and_supported_recall_is_measured() -> None:
    registry, samples = _fixtures()
    records = build_boundary_sample(registry, samples, seed=7, per_group=1)
    positives = [
        {
            "work_id": "W1",
            "expected_corpus": "strict",
            "title": "GIS",
            "reason": "test",
            "provenance": "fixture",
        },
        *[
            {
                "work_id": "W2",
                "expected_corpus": "broad",
                "title": "Remote sensing",
                "reason": "test",
                "provenance": "fixture",
            }
            for _ in range(4)
        ],
    ]
    metrics = evaluate_boundary(records, positives, registry, samples)
    assert metrics["precision"]["status"] == "not_estimated"
    assert metrics["known_positive_recall"]["status"] == "measured"
    assert metrics["known_positive_recall"]["value"] == 1.0
    assert metrics["human_review_complete"] is False
    assert metrics["scientific_status"] == "blocked_pending_human_corpus_review"
    assert "no human review is implied" in metrics["known_positive_recall"]["reason"]
    report = boundary_report(metrics, records)
    assert "blocked pending human judgement" in report
    assert "manually reviewed" not in report
