"""Read-only complete-index school filtering and transparent research-fit scoring.

The functions in this module never write a dataset.  In particular,
``user_defined_fit_score`` exists only in the returned comparison result so a Streamlit caller
can keep the user weights and derived values in session state.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal, TypeAlias

import duckdb
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.schools.contract import SchoolDecisionContract, load_school_decision_contract
from gisnet.schools.index import normalize_school_name

CorpusView: TypeAlias = Literal["strict", "broad"]
SortMetric: TypeAlias = Literal[
    "full_work_count",
    "specialization_lift_global",
    "international_collaboration_share",
    "effective_partner_count",
    "pagerank",
    "citation_flow_fractional_in_strength",
    "rolling_12m_activity_change",
    "user_defined_fit_score",
]

FIT_COMPONENT_IDS = (
    "topic_fit_similarity",
    "recent_24m_work_count",
    "international_collaboration_share",
    "bridge_score",
    "rolling_12m_activity_change",
)
SORT_METRICS = (
    "full_work_count",
    "specialization_lift_global",
    "international_collaboration_share",
    "effective_partner_count",
    "pagerank",
    "citation_flow_fractional_in_strength",
    "rolling_12m_activity_change",
    "user_defined_fit_score",
)
_SUPPORTED_WINDOWS = {12, 24, 36}
_INDEX_REQUIRED_COLUMNS = {
    "canonical_school_id",
    "display_name",
    "country_code",
    "country_name",
    "macro_region",
    "subregion",
    "institution_category",
    "identity_status",
    "identity_resolution_confidence",
    "identity_quality_flags",
    "eligibility_status",
    "support_status",
}
_NAME_REQUIRED_COLUMNS = {
    "normalized_name",
    "canonical_school_id",
    "display_name",
    "country_code",
    "matched_names",
    "match_types",
    "ambiguity_count",
    "is_ambiguous",
}
_PROFILE_REQUIRED_COLUMNS = {
    "canonical_school_id",
    "corpus_view",
    "window_start",
    "window_end",
    "window_months",
    "profile_support_status",
    "full_work_count",
    "recent_24m_work_count",
    "international_collaboration_share",
    "effective_partner_count",
    "pagerank",
    "bridge_score",
    "citation_flow_fractional_in_strength",
    "rolling_12m_activity_change",
    "annual_graph_year",
    "annual_graph_boundary",
    "annual_network_support_status",
    "citation_flow_year",
    "citation_flow_boundary",
    "citation_flow_support_status",
    "momentum_support_status",
    "date_coverage_ratio",
    "date_coverage_status",
    "quality_flags",
}
_TOPIC_REQUIRED_COLUMNS = {
    "canonical_school_id",
    "corpus_view",
    "window_start",
    "window_end",
    "window_months",
    "topic_family",
    "topic_family_share",
    "specialization_lift_global",
    "specialization_lift_macro_region",
    "specialization_lift_country",
    "provisional_topic_registry",
    "topic_profile_support_status",
}


@dataclass(frozen=True)
class SchoolComparisonFilters:
    """Exact complete-index filters for one corpus and rolling time window."""

    corpus_view: CorpusView = "broad"
    window_months: int = 24
    macro_region: str | None = None
    country_code: str | None = None
    subregion: str | None = None
    topic_family: str | None = None
    minimum_recent_activity: int = 0
    minimum_date_coverage: float = 0.0
    stable_school_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.corpus_view not in {"strict", "broad"}:
            raise ValueError("corpus_view must be strict or broad")
        if self.window_months not in _SUPPORTED_WINDOWS:
            raise ValueError("window_months must be 12, 24, or 36")
        if self.minimum_recent_activity < 0:
            raise ValueError("minimum_recent_activity must be non-negative")
        if not 0.0 <= self.minimum_date_coverage <= 1.0:
            raise ValueError("minimum_date_coverage must be between 0 and 1")
        for field_name in ("macro_region", "country_code", "subregion", "topic_family"):
            value = getattr(self, field_name)
            if value is not None and not value.strip():
                raise ValueError(f"{field_name} must be non-empty when supplied")
        if any(not value.strip() for value in self.stable_school_ids):
            raise ValueError("stable_school_ids must contain non-empty stable IDs")


@dataclass(frozen=True)
class SchoolComparisonResult:
    """Rows plus the exact session disclosure needed to reproduce their ordering."""

    rows: list[dict[str, Any]]
    candidate_count: int
    disclosure: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable comparison payload."""
        return {
            "rows": self.rows,
            "candidate_count": self.candidate_count,
            "disclosure": self.disclosure,
        }


def search_schools(
    school_index_path: str | Path,
    school_name_index_path: str | Path,
    *,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search every eligible school alias and return explicit stable-ID matches.

    Exact stable IDs are resolved directly.  Names are search aids only: ambiguous aliases return
    every retained candidate rather than silently choosing an institution.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    normalized_query = normalize_school_name(query)
    if not normalized_query:
        raise ValueError("query must contain searchable text")
    index = _validated_path(school_index_path, _INDEX_REQUIRED_COLUMNS, "school index")
    names = _validated_path(school_name_index_path, _NAME_REQUIRED_COLUMNS, "school name index")
    connection = duckdb.connect()
    try:
        direct = _fetch_dicts(
            connection.execute(
                """
                SELECT canonical_school_id, display_name, country_code, macro_region, subregion
                FROM read_parquet(?)
                WHERE canonical_school_id = ?
                """,
                [str(index), query.strip()],
            )
        )
        if direct:
            row = direct[0]
            row.update(
                {
                    "normalized_name": None,
                    "matched_names": [],
                    "match_types": ["stable_id"],
                    "ambiguity_count": 1,
                    "is_ambiguous": False,
                    "match_basis": "stable_id",
                }
            )
            return [row]
        cursor = connection.execute(
            """
            WITH matches AS (
                SELECT
                    s.canonical_school_id,
                    s.display_name,
                    s.country_code,
                    s.macro_region,
                    s.subregion,
                    n.normalized_name,
                    n.matched_names,
                    n.match_types,
                    n.ambiguity_count,
                    n.is_ambiguous,
                    CASE
                        WHEN n.normalized_name = ? THEN 0
                        WHEN starts_with(n.normalized_name, ?) THEN 1
                        ELSE 2
                    END AS match_rank
                FROM read_parquet(?) n
                JOIN read_parquet(?) s USING (canonical_school_id)
                WHERE contains(n.normalized_name, ?)
            ), best_per_school AS (
                SELECT *, row_number() OVER (
                    PARTITION BY canonical_school_id
                    ORDER BY match_rank, normalized_name
                ) AS school_match_rank
                FROM matches
            )
            SELECT
                canonical_school_id,
                display_name,
                country_code,
                macro_region,
                subregion,
                normalized_name,
                matched_names,
                match_types,
                ambiguity_count,
                is_ambiguous,
                CASE match_rank WHEN 0 THEN 'exact_name'
                    WHEN 1 THEN 'name_prefix' ELSE 'name_contains' END AS match_basis
            FROM best_per_school
            WHERE school_match_rank = 1
            ORDER BY match_rank, lower(display_name), canonical_school_id
            LIMIT ?
            """,
            [
                normalized_query,
                normalized_query,
                str(names),
                str(index),
                normalized_query,
                limit,
            ],
        )
        return _fetch_dicts(cursor)
    finally:
        connection.close()


def compare_schools(
    school_index_path: str | Path,
    school_profiles_path: str | Path,
    school_topic_profiles_path: str | Path,
    *,
    filters: SchoolComparisonFilters,
    sort_metric: SortMetric = "full_work_count",
    descending: bool = True,
    fit_weights: Mapping[str, float] | None = None,
    topic_preferences: Mapping[str, float] | None = None,
    limit: int = 100,
    contract_path: str | Path = "config/school_decision.yml",
) -> SchoolComparisonResult:
    """Filter, independently sort, and optionally score the complete school index.

    Percentile transformations use all candidates after the supplied filters and before ``limit``.
    The returned fit score is never written or registered as a scientific artifact.
    """
    if limit <= 0:
        raise ValueError("limit must be positive")
    if sort_metric not in SORT_METRICS:
        raise ValueError(f"unsupported sort metric: {sort_metric}")
    if sort_metric == "specialization_lift_global" and filters.topic_family is None:
        raise ValueError("Topic specialization sorting requires a topic_family filter")
    if sort_metric == "user_defined_fit_score" and fit_weights is None:
        raise ValueError("user_defined_fit_score sorting requires explicit session weights")

    index = _validated_path(school_index_path, _INDEX_REQUIRED_COLUMNS, "school index")
    profiles = _validated_path(school_profiles_path, _PROFILE_REQUIRED_COLUMNS, "school profiles")
    topics = _validated_path(
        school_topic_profiles_path, _TOPIC_REQUIRED_COLUMNS, "school Topic profiles"
    )
    contract = load_school_decision_contract(contract_path)
    _validate_fit_contract(contract)
    weights = _validated_vector(fit_weights, FIT_COMPONENT_IDS, "fit weights")

    connection = duckdb.connect()
    try:
        available_topics = {
            str(row[0])
            for row in connection.execute(
                "SELECT DISTINCT topic_family FROM read_parquet(?) ORDER BY topic_family",
                [str(topics)],
            ).fetchall()
        }
        requested_topics = set(topic_preferences or ())
        if filters.topic_family is not None:
            requested_topics.add(filters.topic_family)
        unknown_topics = requested_topics.difference(available_topics)
        if unknown_topics:
            raise ValueError(f"unknown Topic families: {sorted(unknown_topics)}")
        preferences = _validated_vector(
            topic_preferences, tuple(sorted(available_topics)), "Topic preferences"
        )
        stable_ids = tuple(dict.fromkeys(filters.stable_school_ids))
        _validate_stable_ids(connection, index, stable_ids)
        rows = _query_candidates(
            connection,
            index=index,
            profiles=profiles,
            topics=topics,
            filters=filters,
            stable_ids=stable_ids,
        )
    finally:
        connection.close()

    _attach_fit_components(rows, contract, weights, preferences, fit_weights is not None)
    for row in rows:
        row["selected_topic_family"] = filters.topic_family
    rows.sort(key=lambda row: _sort_key(row, sort_metric, descending))
    candidate_count = len(rows)
    returned_rows = rows[:limit]
    candidate_id_hash = sha256(
        "\n".join(sorted(str(row["canonical_school_id"]) for row in rows)).encode()
    ).hexdigest()
    boundaries = sorted(
        {
            (str(row["window_start"]), str(row["window_end"]))
            for row in rows
            if row.get("window_start") is not None and row.get("window_end") is not None
        }
    )
    fit_policy = contract.fit_score_policy
    component_reference_counts = {
        component: sum(_finite_number(row["fit_components_raw"][component]) for row in rows)
        for component in FIT_COMPONENT_IDS
    }
    disclosure = {
        "filters": asdict(filters),
        "candidate_count": candidate_count,
        "returned_count": len(returned_rows),
        "candidate_set": {
            "stable_id_sha256": candidate_id_hash,
            "evaluated_before_result_limit": True,
            "source": "complete school_index joined to the selected school profile window",
        },
        "corpus_view": filters.corpus_view,
        "window_months": filters.window_months,
        "window_boundaries": [
            {"window_start": start, "window_end": end} for start, end in boundaries
        ],
        "sort_metric": sort_metric,
        "sort_direction": "descending" if descending else "ascending",
        "sort_metric_definition": contract.metrics[sort_metric].model_dump(mode="json"),
        "comparison_policy": (
            "Metrics remain independent evidence dimensions. This result is not a university "
            "quality ranking."
        ),
        "fit_session": {
            "enabled": fit_weights is not None,
            "score_name": fit_policy.allowed_combined_score_name,
            "weights": weights,
            "topic_preferences": preferences,
            "component_transforms": {
                component: transform.model_dump(mode="json")
                for component, transform in fit_policy.component_transforms.items()
            },
            "component_reference_counts": component_reference_counts,
            "candidate_count": candidate_count,
            "candidate_stable_id_sha256": candidate_id_hash,
            "corpus_view": filters.corpus_view,
            "window_months": filters.window_months,
            "persisted_to_scientific_datasets": False,
            "weight_storage": fit_policy.weight_storage,
            "missing_component_policy": fit_policy.missing_component_policy,
            "zero_weight_policy": fit_policy.zero_weight_policy,
            "required_disclosure": fit_policy.required_disclosure,
        },
        "topic_warning": contract.topic_quality_policy.required_warning,
    }
    return SchoolComparisonResult(
        rows=returned_rows,
        candidate_count=candidate_count,
        disclosure=disclosure,
    )


def _query_candidates(
    connection: duckdb.DuckDBPyConnection,
    *,
    index: Path,
    profiles: Path,
    topics: Path,
    filters: SchoolComparisonFilters,
    stable_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    conditions = [
        "p.corpus_view = ?",
        "p.window_months = ?",
        "coalesce(p.full_work_count, 0) >= ?",
        "coalesce(p.date_coverage_ratio, 0.0) >= ?",
    ]
    condition_values: list[Any] = [
        filters.corpus_view,
        filters.window_months,
        filters.minimum_recent_activity,
        filters.minimum_date_coverage,
    ]
    for column, value in (
        ("macro_region", filters.macro_region),
        ("country_code", filters.country_code),
        ("subregion", filters.subregion),
    ):
        if value is not None:
            conditions.append(f"s.{column} = ?")
            condition_values.append(value)
    if filters.topic_family is not None:
        conditions.append("t.topic_family_share IS NOT NULL")
    if stable_ids:
        placeholders = ", ".join("?" for _ in stable_ids)
        conditions.append(f"s.canonical_school_id IN ({placeholders})")
        condition_values.extend(stable_ids)

    selected_topic = filters.topic_family or ""
    cursor = connection.execute(
        f"""
        WITH topic_vectors AS (
            SELECT
                canonical_school_id,
                list(topic_family ORDER BY topic_family) AS topic_vector_families,
                list(topic_family_share ORDER BY topic_family) AS topic_vector_shares,
                max(topic_family_share) FILTER (WHERE topic_family = ?)
                    AS topic_family_share,
                max(specialization_lift_global) FILTER (WHERE topic_family = ?)
                    AS specialization_lift_global,
                max(specialization_lift_macro_region) FILTER (WHERE topic_family = ?)
                    AS specialization_lift_macro_region,
                max(specialization_lift_country) FILTER (WHERE topic_family = ?)
                    AS specialization_lift_country,
                bool_or(provisional_topic_registry) FILTER (WHERE topic_family = ?)
                    AS provisional_topic_registry,
                max(topic_profile_support_status) FILTER (WHERE topic_family = ?)
                    AS selected_topic_support_status
            FROM read_parquet(?)
            WHERE corpus_view = ? AND window_months = ?
            GROUP BY canonical_school_id
        )
        SELECT
            s.canonical_school_id,
            s.display_name,
            s.country_code,
            s.country_name,
            s.macro_region,
            s.subregion,
            s.institution_category,
            s.identity_status,
            s.identity_resolution_confidence,
            s.identity_quality_flags,
            s.eligibility_status,
            s.support_status AS index_support_status,
            p.corpus_view,
            p.window_start,
            p.window_end,
            p.window_months,
            p.profile_support_status,
            p.full_work_count,
            p.recent_24m_work_count,
            p.international_collaboration_share,
            p.effective_partner_count,
            p.pagerank,
            p.bridge_score,
            p.citation_flow_fractional_in_strength,
            p.rolling_12m_activity_change,
            p.annual_graph_year,
            p.annual_graph_boundary,
            p.annual_network_support_status,
            p.citation_flow_year,
            p.citation_flow_boundary,
            p.citation_flow_support_status,
            p.momentum_support_status,
            p.date_coverage_ratio,
            p.date_coverage_status,
            p.quality_flags,
            t.topic_vector_families,
            t.topic_vector_shares,
            t.topic_family_share,
            t.specialization_lift_global,
            t.specialization_lift_macro_region,
            t.specialization_lift_country,
            t.provisional_topic_registry,
            t.selected_topic_support_status
        FROM read_parquet(?) s
        JOIN read_parquet(?) p USING (canonical_school_id)
        LEFT JOIN topic_vectors t USING (canonical_school_id)
        WHERE {" AND ".join(conditions)}
        ORDER BY s.canonical_school_id
        """,
        [
            selected_topic,
            selected_topic,
            selected_topic,
            selected_topic,
            selected_topic,
            selected_topic,
            str(topics),
            filters.corpus_view,
            filters.window_months,
            str(index),
            str(profiles),
            *condition_values,
        ],
    )
    return _fetch_dicts(cursor)


def _attach_fit_components(
    rows: list[dict[str, Any]],
    contract: SchoolDecisionContract,
    weights: dict[str, float],
    preferences: dict[str, float],
    enabled: bool,
) -> None:
    for row in rows:
        row["topic_fit_similarity"] = _topic_fit_similarity(row, preferences)
        row["fit_components_raw"] = {
            component: row.get(component) for component in FIT_COMPONENT_IDS
        }

    transformed_by_component: dict[str, list[float | None]] = {}
    for component in FIT_COMPONENT_IDS:
        transform = contract.fit_score_policy.component_transforms[component]
        raw = [row["fit_components_raw"][component] for row in rows]
        if transform.method == "identity_0_1":
            transformed_by_component[component] = [_identity_0_1(value, component) for value in raw]
        else:
            transformed_by_component[component] = _average_percentile_rank(raw)

    positive_weight = any(value > 0 for value in weights.values())
    weight_total = sum(weights.values())
    for row_index, row in enumerate(rows):
        transformed = {
            component: transformed_by_component[component][row_index]
            for component in FIT_COMPONENT_IDS
        }
        row["fit_components_transformed"] = transformed
        if not enabled or not positive_weight:
            row["user_defined_fit_score"] = None
            continue
        positive_components = [
            component for component in FIT_COMPONENT_IDS if weights[component] > 0
        ]
        if any(transformed[component] is None for component in positive_components):
            row["user_defined_fit_score"] = None
            continue
        weighted_total = 0.0
        for component in positive_components:
            component_value = transformed[component]
            if component_value is None:  # Guarded above; retained for static type safety.
                raise AssertionError("positive-weight fit component unexpectedly missing")
            weighted_total += weights[component] * component_value
        row["user_defined_fit_score"] = weighted_total / weight_total


def _topic_fit_similarity(row: dict[str, Any], preferences: dict[str, float]) -> float | None:
    if not preferences or not any(value > 0 for value in preferences.values()):
        return None
    families = row.get("topic_vector_families")
    shares = row.get("topic_vector_shares")
    if not isinstance(families, list) or not isinstance(shares, list) or not shares:
        return None
    school_vector: dict[str, float] = {}
    for family, share in zip(families, shares, strict=True):
        numeric_share = _as_finite_float(share)
        if numeric_share is None or numeric_share < 0:
            return None
        school_vector[str(family)] = numeric_share
    dot_product = sum(
        school_vector.get(family, 0.0) * value for family, value in preferences.items()
    )
    school_norm = math.sqrt(sum(value * value for value in school_vector.values()))
    preference_norm = math.sqrt(sum(value * value for value in preferences.values()))
    if school_norm == 0.0 or preference_norm == 0.0:
        return None
    return dot_product / (school_norm * preference_norm)


def _average_percentile_rank(values: list[Any]) -> list[float | None]:
    numeric = [float(value) for value in values if _finite_number(value)]
    if not numeric:
        return [None for _ in values]
    ordered = sorted(numeric)
    rank_sums: dict[float, float] = {}
    rank_counts: dict[float, int] = {}
    for rank, value in enumerate(ordered, start=1):
        rank_sums[value] = rank_sums.get(value, 0.0) + rank
        rank_counts[value] = rank_counts.get(value, 0) + 1
    candidate_count = len(numeric)
    if candidate_count == 1 or len(rank_counts) == 1:
        transformed = {value: 0.5 for value in rank_counts}
    else:
        transformed = {
            value: (rank_sums[value] / rank_counts[value] - 1.0) / (candidate_count - 1.0)
            for value in rank_counts
        }
    return [transformed[float(value)] if _finite_number(value) else None for value in values]


def _identity_0_1(value: Any, component: str) -> float | None:
    if value is None:
        return None
    if not _finite_number(value):
        return None
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError(f"{component} must be between 0 and 1 for identity transformation")
    return result


def _sort_key(row: dict[str, Any], metric: str, descending: bool) -> tuple[Any, ...]:
    numeric_value = _as_finite_float(row.get(metric))
    missing = numeric_value is None
    numeric = 0.0 if numeric_value is None else numeric_value
    ordered = -numeric if descending else numeric
    return (
        missing,
        ordered,
        str(row["display_name"]).casefold(),
        str(row["canonical_school_id"]),
    )


def _validated_vector(
    values: Mapping[str, float] | None,
    allowed: tuple[str, ...],
    label: str,
) -> dict[str, float]:
    result = dict.fromkeys(allowed, 0.0)
    if values is None:
        return result
    unknown = set(values).difference(allowed)
    if unknown:
        raise ValueError(f"{label} contain unsupported components: {sorted(unknown)}")
    for key, raw_value in values.items():
        value = float(raw_value)
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"{label} must contain finite non-negative values")
        result[key] = value
    return result


def _validate_fit_contract(contract: SchoolDecisionContract) -> None:
    policy_components = tuple(contract.fit_score_policy.allowed_component_metric_ids)
    if policy_components != FIT_COMPONENT_IDS:
        raise ValueError("school comparison fit components do not match the analytical contract")
    if contract.fit_score_policy.persisted_to_scientific_datasets:
        raise ValueError("the analytical contract may not persist user-defined fit scores")


def _validate_stable_ids(
    connection: duckdb.DuckDBPyConnection,
    index: Path,
    stable_ids: tuple[str, ...],
) -> None:
    if not stable_ids:
        return
    placeholders = ", ".join("?" for _ in stable_ids)
    present = {
        str(row[0])
        for row in connection.execute(
            f"""
            SELECT canonical_school_id
            FROM read_parquet(?)
            WHERE canonical_school_id IN ({placeholders})
            """,
            [str(index), *stable_ids],
        ).fetchall()
    }
    unknown = set(stable_ids).difference(present)
    if unknown:
        raise ValueError(f"unknown stable school IDs: {sorted(unknown)}")


def _validated_path(path: str | Path, required: set[str], label: str) -> Path:
    candidate = Path(path)
    if not candidate.is_file():
        raise ValueError(f"{label} does not exist: {candidate}")
    missing = required.difference(pq.read_schema(candidate).names)
    if missing:
        raise ValueError(f"{label} is missing required columns: {sorted(missing)}")
    return candidate


def _fetch_dicts(cursor: duckdb.DuckDBPyConnection) -> list[dict[str, Any]]:
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def _finite_number(value: Any) -> bool:
    return _as_finite_float(value) is not None


def _as_finite_float(value: Any) -> float | None:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    converted = float(value)
    return converted if math.isfinite(converted) else None
