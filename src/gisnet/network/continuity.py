"""Deterministic adjacent-year community matching and continuity events."""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "jaccard-community-continuity-2026-08-05-v1"
_ALGORITHM = (
    "deterministic greedy one-to-one assignment by descending Jaccard, intersection, "
    "then annual community IDs"
)


@dataclass(frozen=True)
class Overlap:
    previous_id: str
    current_id: str
    intersection_count: int
    previous_size: int
    current_size: int

    @property
    def union_count(self) -> int:
        return self.previous_size + self.current_size - self.intersection_count

    @property
    def jaccard(self) -> float:
        return self.intersection_count / self.union_count if self.union_count else 0.0


@dataclass(frozen=True)
class TransitionMatch:
    assignments: dict[str, str]
    candidates: tuple[Overlap, ...]
    previous_overlap_degree: dict[str, int]
    current_overlap_degree: dict[str, int]
    unmatched_previous: tuple[str, ...]
    unmatched_current: tuple[str, ...]


def match_community_sets(
    previous: dict[str, set[str]],
    current: dict[str, set[str]],
    *,
    event_overlap_threshold: float = 0.10,
) -> TransitionMatch:
    """Match synthetic or loaded communities using the production assignment rule."""
    overlaps: list[Overlap] = []
    for previous_id, previous_members in sorted(previous.items()):
        for current_id, current_members in sorted(current.items()):
            intersection = len(previous_members.intersection(current_members))
            if intersection:
                overlaps.append(
                    Overlap(
                        previous_id,
                        current_id,
                        intersection,
                        len(previous_members),
                        len(current_members),
                    )
                )
    return _match_overlaps(
        overlaps,
        previous_ids=set(previous),
        current_ids=set(current),
        event_overlap_threshold=event_overlap_threshold,
    )


def build_community_continuity(
    communities_path: str | Path,
    *,
    continuity_output: str | Path,
    transitions_output: str | Path,
    confident_match_threshold: float = 0.25,
    event_overlap_threshold: float = 0.10,
    memory_limit: str = "2GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Build community-level continuity IDs and machine-readable transition events."""
    source = Path(communities_path)
    if not source.is_file():
        raise ValueError(f"community dataset does not exist: {source}")
    if not 0.0 < event_overlap_threshold <= confident_match_threshold <= 1.0:
        raise ValueError("community match thresholds must satisfy 0 < event <= confident <= 1")
    continuity_path = Path(continuity_output)
    transitions_path = Path(transitions_output)
    continuity_path.parent.mkdir(parents=True, exist_ok=True)
    transitions_path.parent.mkdir(parents=True, exist_ok=True)
    continuity_temporary = continuity_path.with_suffix(".parquet.tmp")
    transitions_temporary = transitions_path.with_suffix(".parquet.tmp")
    continuity_temporary.unlink(missing_ok=True)
    transitions_temporary.unlink(missing_ok=True)

    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = ?", [memory_limit])
        connection.execute("SET threads = ?", [threads])
        size_rows = connection.execute(
            """
            SELECT year, corpus_view, hierarchy_view, community_id, count(*)::BIGINT
            FROM read_parquet(?)
            WHERE community_id IS NOT NULL
            GROUP BY ALL
            ORDER BY corpus_view, hierarchy_view, year, community_id
            """,
            [str(source)],
        ).fetchall()
        overlap_rows = connection.execute(
            """
            SELECT
                previous.year + 1 AS transition_year,
                previous.corpus_view,
                previous.hierarchy_view,
                previous.community_id AS previous_id,
                current.community_id AS current_id,
                count(*)::BIGINT AS intersection_count
            FROM read_parquet(?) AS previous
            JOIN read_parquet(?) AS current
              ON current.year = previous.year + 1
             AND current.corpus_view = previous.corpus_view
             AND current.hierarchy_view = previous.hierarchy_view
             AND current.institution_id = previous.institution_id
            WHERE previous.community_id IS NOT NULL
              AND current.community_id IS NOT NULL
            GROUP BY ALL
            ORDER BY corpus_view, hierarchy_view, transition_year, previous_id, current_id
            """,
            [str(source), str(source)],
        ).fetchall()
    finally:
        connection.close()

    sizes: dict[tuple[str, str, int, str], int] = {
        (str(corpus), str(hierarchy), int(year), str(community)): int(size)
        for year, corpus, hierarchy, community, size in size_rows
    }
    communities_by_view_year: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    for corpus, hierarchy, year, community in sizes:
        communities_by_view_year[(corpus, hierarchy, year)].add(community)
    overlaps_by_transition: dict[tuple[str, str, int], list[Overlap]] = defaultdict(list)
    for transition_year, corpus, hierarchy, previous_id, current_id, intersection in overlap_rows:
        key = (str(corpus), str(hierarchy), int(transition_year))
        overlaps_by_transition[key].append(
            Overlap(
                str(previous_id),
                str(current_id),
                int(intersection),
                sizes[(str(corpus), str(hierarchy), int(transition_year) - 1, str(previous_id))],
                sizes[(str(corpus), str(hierarchy), int(transition_year), str(current_id))],
            )
        )

    continuity_rows: list[dict[str, Any]] = []
    transition_rows: list[dict[str, Any]] = []
    event_counts: dict[str, int] = defaultdict(int)
    selected_match_count = 0
    uncertain_match_count = 0
    views = sorted({(corpus, hierarchy) for corpus, hierarchy, _ in communities_by_view_year})
    for corpus, hierarchy in views:
        years = sorted(
            year
            for view_corpus, view_hierarchy, year in communities_by_view_year
            if (view_corpus, view_hierarchy) == (corpus, hierarchy)
        )
        continuity_by_annual: dict[tuple[int, str], str] = {}
        counter = 0

        first_year = years[0]
        for community_id in sorted(communities_by_view_year[(corpus, hierarchy, first_year)]):
            counter += 1
            continuity_id = _new_continuity_id(corpus, hierarchy, counter)
            continuity_by_annual[(first_year, community_id)] = continuity_id
            continuity_rows.append(
                _continuity_row(
                    first_year,
                    corpus,
                    hierarchy,
                    community_id,
                    continuity_id,
                    sizes[(corpus, hierarchy, first_year, community_id)],
                    match_status="first_year",
                )
            )
        for current_year in years[1:]:
            previous_year = current_year - 1
            previous_ids = communities_by_view_year[(corpus, hierarchy, previous_year)]
            current_ids = communities_by_view_year[(corpus, hierarchy, current_year)]
            match = _match_overlaps(
                overlaps_by_transition[(corpus, hierarchy, current_year)],
                previous_ids=previous_ids,
                current_ids=current_ids,
                event_overlap_threshold=event_overlap_threshold,
            )
            candidate_lookup = {
                (candidate.previous_id, candidate.current_id): candidate
                for candidate in match.candidates
            }
            for current_id in sorted(current_ids):
                previous_id = match.assignments.get(current_id)
                if previous_id is None:
                    counter += 1
                    continuity_id = _new_continuity_id(corpus, hierarchy, counter)
                    status = "birth"
                    overlap = None
                else:
                    continuity_id = continuity_by_annual[(previous_year, previous_id)]
                    overlap = candidate_lookup[(previous_id, current_id)]
                    status = (
                        "uncertain_match"
                        if overlap.jaccard < confident_match_threshold
                        else "continued"
                    )
                    selected_match_count += 1
                    uncertain_match_count += int(status == "uncertain_match")
                continuity_by_annual[(current_year, current_id)] = continuity_id
                continuity_rows.append(
                    _continuity_row(
                        current_year,
                        corpus,
                        hierarchy,
                        current_id,
                        continuity_id,
                        sizes[(corpus, hierarchy, current_year, current_id)],
                        match_status=status,
                        previous_community_id=previous_id,
                        overlap=overlap,
                    )
                )
            accepted_pairs = {
                (previous, current) for current, previous in match.assignments.items()
            }
            for candidate in match.candidates:
                meaningful = candidate.jaccard >= event_overlap_threshold
                event_type = _candidate_event_type(candidate, match, meaningful=meaningful)
                event_counts[event_type] += 1
                transition_rows.append(
                    _transition_row(
                        current_year,
                        corpus,
                        hierarchy,
                        candidate.previous_id,
                        candidate.current_id,
                        continuity_by_annual[(previous_year, candidate.previous_id)],
                        continuity_by_annual[(current_year, candidate.current_id)],
                        candidate,
                        assignment_selected=(candidate.previous_id, candidate.current_id)
                        in accepted_pairs,
                        event_type=event_type,
                        previous_overlap_degree=match.previous_overlap_degree.get(
                            candidate.previous_id, 0
                        ),
                        current_overlap_degree=match.current_overlap_degree.get(
                            candidate.current_id, 0
                        ),
                        confident_match_threshold=confident_match_threshold,
                        event_overlap_threshold=event_overlap_threshold,
                    )
                )
            for current_id in match.unmatched_current:
                significant_sources = match.current_overlap_degree.get(current_id, 0)
                event_type = "split_birth" if significant_sources else "birth"
                event_counts[event_type] += 1
                transition_rows.append(
                    _transition_row(
                        current_year,
                        corpus,
                        hierarchy,
                        None,
                        current_id,
                        None,
                        continuity_by_annual[(current_year, current_id)],
                        None,
                        assignment_selected=False,
                        event_type=event_type,
                        previous_overlap_degree=0,
                        current_overlap_degree=significant_sources,
                        confident_match_threshold=confident_match_threshold,
                        event_overlap_threshold=event_overlap_threshold,
                    )
                )
            matched_previous = set(match.assignments.values())
            for previous_id in match.unmatched_previous:
                significant_targets = match.previous_overlap_degree.get(previous_id, 0)
                event_type = "merge_disappearance" if significant_targets else "disappearance"
                event_counts[event_type] += 1
                transition_rows.append(
                    _transition_row(
                        current_year,
                        corpus,
                        hierarchy,
                        previous_id,
                        None,
                        continuity_by_annual[(previous_year, previous_id)],
                        None,
                        None,
                        assignment_selected=False,
                        event_type=event_type,
                        previous_overlap_degree=significant_targets,
                        current_overlap_degree=0,
                        confident_match_threshold=confident_match_threshold,
                        event_overlap_threshold=event_overlap_threshold,
                    )
                )
            if matched_previous.intersection(match.unmatched_previous):
                raise ValueError("community assignment produced contradictory previous matches")

    continuity_rows.sort(
        key=lambda row: (
            row["year"],
            row["corpus_view"],
            row["hierarchy_view"],
            row["annual_community_id"],
        )
    )
    transition_rows.sort(
        key=lambda row: (
            row["transition_year"],
            row["corpus_view"],
            row["hierarchy_view"],
            row["previous_community_key"],
            row["current_community_key"],
        )
    )
    try:
        pq.write_table(
            pa.Table.from_pylist(continuity_rows), continuity_temporary, compression="zstd"
        )
        pq.write_table(
            pa.Table.from_pylist(transition_rows), transitions_temporary, compression="zstd"
        )
        continuity_metrics = parquet_metrics(
            continuity_temporary,
            primary_key=["year", "corpus_view", "hierarchy_view", "annual_community_id"],
            required_columns={
                "year",
                "annual_community_id",
                "continuity_id",
                "match_status",
                "low_overlap_uncertain",
            },
            year_column="year",
        )
        transition_metrics = parquet_metrics(
            transitions_temporary,
            primary_key=[
                "transition_year",
                "corpus_view",
                "hierarchy_view",
                "previous_community_key",
                "current_community_key",
            ],
            required_columns={
                "transition_year",
                "event_type",
                "assignment_selected",
                "jaccard_overlap",
            },
            year_column="transition_year",
        )
    except BaseException:
        continuity_temporary.unlink(missing_ok=True)
        transitions_temporary.unlink(missing_ok=True)
        raise
    os.replace(continuity_temporary, continuity_path)
    os.replace(transitions_temporary, transitions_path)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "source_sha256": file_sha256(source),
                "confident_match_threshold": confident_match_threshold,
                "event_overlap_threshold": event_overlap_threshold,
            }
        ),
        "assignment_algorithm": _ALGORITHM,
        "confident_match_threshold": confident_match_threshold,
        "event_overlap_threshold": event_overlap_threshold,
        "continuity_row_count": int(continuity_metrics["row_count"]),
        "transition_row_count": int(transition_metrics["row_count"]),
        "selected_match_count": selected_match_count,
        "uncertain_match_count": uncertain_match_count,
        "event_counts": dict(sorted(event_counts.items())),
        "continuity_sha256": str(continuity_metrics["checksum_sha256"]),
        "transitions_sha256": str(transition_metrics["checksum_sha256"]),
        "outputs": {
            "community_continuity_year": str(continuity_path),
            "community_transitions_year": str(transitions_path),
        },
        "generated_at_utc": _timestamp(),
    }


def write_continuity_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    continuity_path: str | Path,
    transitions_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    config_hashes = {"project": config_file_hash(project_config_path)}
    sources = [".agent/manifests/communities_year.json"]
    versions = {"community_continuity": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="community_continuity_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions=versions,
        source_manifests=sources,
        command=command,
    )
    write_parquet_manifest(
        path=continuity_path,
        dataset_name="community_continuity_year",
        primary_key=["year", "corpus_view", "hierarchy_view", "annual_community_id"],
        required_columns={"year", "annual_community_id", "continuity_id", "match_status"},
        year_column="year",
        run_id=run_id,
        config_hashes=config_hashes,
        source_manifests=sources,
        source_versions=versions,
        command=command,
    )
    write_parquet_manifest(
        path=transitions_path,
        dataset_name="community_transitions_year",
        primary_key=[
            "transition_year",
            "corpus_view",
            "hierarchy_view",
            "previous_community_key",
            "current_community_key",
        ],
        required_columns={"transition_year", "event_type", "assignment_selected"},
        year_column="transition_year",
        run_id=run_id,
        config_hashes=config_hashes,
        source_manifests=sources,
        source_versions=versions,
        command=command,
    )


def _match_overlaps(
    overlaps: list[Overlap],
    *,
    previous_ids: set[str],
    current_ids: set[str],
    event_overlap_threshold: float,
) -> TransitionMatch:
    ordered = tuple(
        sorted(
            overlaps,
            key=lambda row: (
                -row.jaccard,
                -row.intersection_count,
                row.previous_id,
                row.current_id,
            ),
        )
    )
    assignments: dict[str, str] = {}
    used_previous: set[str] = set()
    for candidate in ordered:
        if candidate.previous_id in used_previous or candidate.current_id in assignments:
            continue
        assignments[candidate.current_id] = candidate.previous_id
        used_previous.add(candidate.previous_id)
    previous_degree: dict[str, int] = defaultdict(int)
    current_degree: dict[str, int] = defaultdict(int)
    for candidate in ordered:
        if candidate.jaccard >= event_overlap_threshold:
            previous_degree[candidate.previous_id] += 1
            current_degree[candidate.current_id] += 1
    return TransitionMatch(
        assignments=assignments,
        candidates=ordered,
        previous_overlap_degree=dict(previous_degree),
        current_overlap_degree=dict(current_degree),
        unmatched_previous=tuple(sorted(previous_ids.difference(used_previous))),
        unmatched_current=tuple(sorted(current_ids.difference(assignments))),
    )


def _candidate_event_type(
    candidate: Overlap,
    match: TransitionMatch,
    *,
    meaningful: bool,
) -> str:
    if not meaningful:
        return "minor_overlap"
    previous_degree = match.previous_overlap_degree.get(candidate.previous_id, 0)
    current_degree = match.current_overlap_degree.get(candidate.current_id, 0)
    if previous_degree > 1 and current_degree > 1:
        return "split_merge"
    if previous_degree > 1:
        return "split"
    if current_degree > 1:
        return "merge"
    return "continuation"


def _new_continuity_id(corpus: str, hierarchy: str, counter: int) -> str:
    return f"CT-{corpus[0].upper()}{hierarchy[0].upper()}-{counter:05d}"


def _continuity_row(
    year: int,
    corpus: str,
    hierarchy: str,
    community_id: str,
    continuity_id: str,
    community_size: int,
    *,
    match_status: str,
    previous_community_id: str | None = None,
    overlap: Overlap | None = None,
) -> dict[str, Any]:
    return {
        "year": year,
        "corpus_view": corpus,
        "hierarchy_view": hierarchy,
        "annual_community_id": community_id,
        "continuity_id": continuity_id,
        "community_size": community_size,
        "previous_community_id": previous_community_id,
        "overlap_intersection_count": overlap.intersection_count if overlap else None,
        "overlap_union_count": overlap.union_count if overlap else None,
        "jaccard_overlap": overlap.jaccard if overlap else None,
        "match_status": match_status,
        "low_overlap_uncertain": match_status == "uncertain_match",
        "assignment_algorithm": _ALGORITHM,
    }


def _transition_row(
    transition_year: int,
    corpus: str,
    hierarchy: str,
    previous_id: str | None,
    current_id: str | None,
    previous_continuity_id: str | None,
    current_continuity_id: str | None,
    overlap: Overlap | None,
    *,
    assignment_selected: bool,
    event_type: str,
    previous_overlap_degree: int,
    current_overlap_degree: int,
    confident_match_threshold: float,
    event_overlap_threshold: float,
) -> dict[str, Any]:
    jaccard = overlap.jaccard if overlap else None
    return {
        "transition_year": transition_year,
        "previous_year": transition_year - 1,
        "corpus_view": corpus,
        "hierarchy_view": hierarchy,
        "previous_community_id": previous_id,
        "current_community_id": current_id,
        "previous_community_key": previous_id or "__BIRTH__",
        "current_community_key": current_id or "__DISAPPEARANCE__",
        "previous_continuity_id": previous_continuity_id,
        "current_continuity_id": current_continuity_id,
        "intersection_count": overlap.intersection_count if overlap else 0,
        "union_count": overlap.union_count if overlap else None,
        "jaccard_overlap": jaccard,
        "assignment_selected": assignment_selected,
        "low_overlap_uncertain": bool(
            assignment_selected and jaccard is not None and jaccard < confident_match_threshold
        ),
        "previous_overlap_degree": previous_overlap_degree,
        "current_overlap_degree": current_overlap_degree,
        "event_type": event_type,
        "assignment_algorithm": _ALGORITHM,
        "confident_match_threshold": confident_match_threshold,
        "event_overlap_threshold": event_overlap_threshold,
    }


def _timestamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
