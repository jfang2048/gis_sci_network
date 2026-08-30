"""Predicate-pushed profile queries and explicit quality views for one school."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd  # type: ignore[import-untyped]

_CORPUS_VIEWS = frozenset({"strict", "broad"})
_ROLLING_WINDOWS = (12, 24, 36)
_PROFILE_KEY_COLUMNS = {
    "school_id",
    "corpus_view",
    "hierarchy_view",
    "window_start",
    "window_end",
    "window_months",
}
_TOPIC_KEY_COLUMNS = _PROFILE_KEY_COLUMNS | {"topic_family"}


def query_school_profile(
    profile_path: str | Path,
    *,
    school_id: str,
    corpus_view: str,
    window_months: int,
) -> pd.DataFrame:
    """Read exactly one supported rolling profile using Parquet predicate pushdown."""
    return query_school_profiles(
        profile_path,
        school_ids=[school_id],
        corpus_view=corpus_view,
        window_months=window_months,
    )


def query_school_profiles(
    profile_path: str | Path,
    *,
    school_ids: Sequence[str],
    corpus_view: str,
    window_months: int,
) -> pd.DataFrame:
    """Read exact rolling profiles for stable IDs using one predicate-pushed query."""
    source = _validated_source(profile_path, "school profile")
    selected_ids = _validate_selections(school_ids, corpus_view, window_months)
    _require_parquet_columns(source, _PROFILE_KEY_COLUMNS, "school profile")
    connection = duckdb.connect()
    try:
        result = connection.execute(
            """
            SELECT *
            FROM read_parquet(?)
            WHERE school_id = ANY(?)
              AND corpus_view = ?
              AND hierarchy_view = 'school'
              AND window_months = ?
            ORDER BY school_id, window_end DESC, window_start DESC
            """,
            [str(source), selected_ids, corpus_view, window_months],
        ).fetchdf()
    finally:
        connection.close()
    duplicate_ids = sorted(
        str(value)
        for value in result.loc[result.duplicated("school_id", keep=False), "school_id"].unique()
    )
    if duplicate_ids:
        raise ValueError(
            "school profile query returned multiple rows for stable ID, corpus, and window: "
            f"{duplicate_ids}"
        )
    return result


def query_school_topics(
    topic_profile_path: str | Path,
    *,
    school_id: str,
    corpus_view: str,
    window_months: int,
) -> pd.DataFrame:
    """Read one school's Topic-family profile using Parquet predicate pushdown."""
    return query_school_topics_for_schools(
        topic_profile_path,
        school_ids=[school_id],
        corpus_view=corpus_view,
        window_months=window_months,
    )


def query_school_topics_for_schools(
    topic_profile_path: str | Path,
    *,
    school_ids: Sequence[str],
    corpus_view: str,
    window_months: int,
) -> pd.DataFrame:
    """Read Topic-family profiles for stable IDs using one predicate-pushed query."""
    source = _validated_source(topic_profile_path, "school Topic profile")
    selected_ids = _validate_selections(school_ids, corpus_view, window_months)
    _require_parquet_columns(source, _TOPIC_KEY_COLUMNS, "school Topic profile")
    connection = duckdb.connect()
    try:
        return connection.execute(
            """
            SELECT *
            FROM read_parquet(?)
            WHERE school_id = ANY(?)
              AND corpus_view = ?
              AND hierarchy_view = 'school'
              AND window_months = ?
            ORDER BY school_id, topic_rank NULLS LAST, topic_family
            """,
            [str(source), selected_ids, corpus_view, window_months],
        ).fetchdf()
    finally:
        connection.close()


def activity_horizon_view(profile: Mapping[str, Any]) -> pd.DataFrame:
    """Return source-stored cumulative rolling counts without deriving or imputing values."""
    rows = []
    for window_months in _ROLLING_WINDOWS:
        value = _optional_number(profile.get(f"recent_{window_months}m_work_count"))
        rows.append(
            {
                "window_months": window_months,
                "work_count": value,
                "window_label": f"Rolling {window_months} months",
            }
        )
    return pd.DataFrame(rows, columns=["window_months", "work_count", "window_label"])


def research_neighbor_view(profile: Mapping[str, Any], school_index: pd.DataFrame) -> pd.DataFrame:
    """Resolve source-stored research-neighbour IDs while retaining every unmatched ID."""
    required = {"school_id", "display_name", "country_name", "macro_region"}
    missing = sorted(required.difference(school_index.columns))
    if missing:
        raise ValueError(f"complete school index lacks required columns: {missing}")
    ids = _string_sequence(profile.get("topic_similarity_top_neighbor_ids"))
    lookup = school_index.drop_duplicates("school_id", keep="first").copy()
    lookup["school_id"] = lookup["school_id"].astype(str)
    lookup = lookup.set_index("school_id")
    rows: list[dict[str, Any]] = []
    for rank, school_id in enumerate(ids, start=1):
        if school_id in lookup.index:
            match = lookup.loc[school_id]
            rows.append(
                {
                    "proximity_rank": rank,
                    "school_id": school_id,
                    "display_name": _optional_text(match["display_name"]),
                    "country_name": _optional_text(match["country_name"]),
                    "macro_region": _optional_text(match["macro_region"]),
                    "index_match_status": "matched_complete_school_index",
                }
            )
        else:
            rows.append(
                {
                    "proximity_rank": rank,
                    "school_id": school_id,
                    "display_name": None,
                    "country_name": None,
                    "macro_region": None,
                    "index_match_status": "stable_id_not_in_complete_school_index",
                }
            )
    result = pd.DataFrame(
        rows,
        columns=[
            "proximity_rank",
            "school_id",
            "display_name",
            "country_name",
            "macro_region",
            "index_match_status",
        ],
    )
    for column in ("display_name", "country_name", "macro_region"):
        result[column] = result[column].astype(object).where(result[column].notna(), None)
    return result


def profile_quality_messages(
    profile: Mapping[str, Any], *, low_date_coverage_threshold: float = 0.8
) -> list[str]:
    """Translate source statuses into explicit diagnostic messages; never fill missing data."""
    if not 0 <= low_date_coverage_threshold <= 1:
        raise ValueError("low_date_coverage_threshold must be between zero and one")
    messages: list[str] = []
    support_status = _optional_text(profile.get("profile_support_status"))
    if support_status and support_status != "supported":
        messages.append(
            f"Profile support status is `{support_status}`; no recent activity or other "
            "unsupported evidence remains explicit rather than being imputed."
        )
    date_coverage = _optional_number(profile.get("date_coverage_ratio"))
    if date_coverage is None:
        messages.append("Exact publication-date coverage is unavailable; no value is imputed.")
    elif date_coverage < low_date_coverage_threshold:
        messages.append(
            f"Exact publication-date coverage is low at {date_coverage:.1%} under the "
            f"dashboard diagnostic threshold of {low_date_coverage_threshold:.0%}."
        )
    window_coverage = _optional_number(profile.get("coverage_ratio"))
    complete = profile.get("is_complete_window")
    if complete is False or (window_coverage is not None and window_coverage < 1):
        coverage = "unavailable" if window_coverage is None else f"{window_coverage:.1%}"
        messages.append(
            f"The requested rolling window is incomplete ({coverage} coverage); missing "
            "months are not treated as zero."
        )
    flags = _string_sequence(profile.get("quality_flags"))
    if flags:
        messages.append(f"Source quality flags: {', '.join(flags)}.")
    return messages


def _validated_source(path: str | Path, label: str) -> Path:
    source = Path(path)
    if not source.is_file():
        raise ValueError(f"{label} dataset does not exist: {source}")
    return source


def _validate_selection(school_id: str, corpus_view: str, window_months: int) -> None:
    _validate_selections([school_id], corpus_view, window_months)


def _validate_selections(
    school_ids: Sequence[str], corpus_view: str, window_months: int
) -> list[str]:
    if isinstance(school_ids, str):
        raise ValueError("school_ids must be a sequence of stable IDs, not one string")
    selected_ids = [str(school_id) for school_id in school_ids]
    if not selected_ids or any(not school_id for school_id in selected_ids):
        raise ValueError("school_ids cannot be empty")
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("school_ids cannot contain duplicates")
    if corpus_view not in _CORPUS_VIEWS:
        raise ValueError("corpus_view must be strict or broad")
    if window_months not in _ROLLING_WINDOWS:
        raise ValueError("window_months must be one of 12, 24, or 36")
    return selected_ids


def _require_parquet_columns(source: Path, required: set[str], label: str) -> None:
    connection = duckdb.connect()
    try:
        columns = {
            str(row[0])
            for row in connection.execute(
                "SELECT column_name FROM (DESCRIBE SELECT * FROM read_parquet(?))", [str(source)]
            ).fetchall()
        }
    finally:
        connection.close()
    missing = sorted(required.difference(columns))
    if missing:
        raise ValueError(f"{label} lacks required columns: {missing}")


def _optional_number(value: Any) -> int | float | None:
    if value is None or (not isinstance(value, Sequence) and bool(pd.isna(value))):
        return None
    return value if isinstance(value, int | float) else float(value)


def _optional_text(value: Any) -> str | None:
    if value is None or bool(pd.isna(value)):
        return None
    return str(value)


def _string_sequence(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Sequence) or hasattr(value, "tolist"):
        values = value.tolist() if hasattr(value, "tolist") else value
        return [str(item) for item in values if item is not None and not bool(pd.isna(item))]
    return []
