from __future__ import annotations

import math
from collections import defaultdict
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import gisnet.network.rolling as rolling_module
from gisnet.dataset import file_sha256
from gisnet.network.rolling import (
    build_rolling_facts,
    query_rolling_edges,
    write_rolling_artifacts,
)

_VIEW = ("strict", "organization")
_INSTITUTIONS = {
    "I_BOUNDARY": ("Boundary", "DE", "Germany", "Europe", "Western Europe"),
    "I_SINGLE": ("Singleton", "DE", "Germany", "Europe", "Western Europe"),
    "I_OLD_PARTNER": ("Old Partner", "NL", "Netherlands", "Europe", "Western Europe"),
    "I_PARTIAL": ("Partial", "DE", "Germany", "Europe", "Western Europe"),
    "I2_A": ("Alpha", "DE", "Germany", "Europe", "Western Europe"),
    "I1_B": ("Beta", "US", "United States", "Americas", "Northern America"),
    "I3_C": ("Gamma", "FR", "France", "Europe", "Western Europe"),
    "I4_D": ("Delta", "JP", "Japan", "Asia", "Eastern Asia"),
}


def _quarter(month: str) -> str:
    return f"{month[:4]}-Q{((int(month[5:]) - 1) // 3) + 1}"


def _date_fact(work_id: str, publication_year: int, month: str | None) -> dict[str, object]:
    publication_date = date.fromisoformat(f"{month}-15") if month else None
    return {
        "work_id": work_id,
        "publication_year": publication_year,
        "publication_date_raw": publication_date.isoformat() if publication_date else None,
        "publication_date": publication_date,
        "publication_month": month,
        "publication_quarter": _quarter(month) if month else None,
        "has_exact_publication_date": publication_date is not None,
        "subannual_date_eligible": publication_date is not None,
        "date_quality_status": "exact_valid" if publication_date else "missing",
    }


def _membership(work_id: str, publication_year: int, institution_id: str) -> dict[str, object]:
    name, code, country, region, subregion = _INSTITUTIONS[institution_id]
    return {
        "publication_year": publication_year,
        "work_id": work_id,
        "hierarchy_view": _VIEW[1],
        "institution_id": institution_id,
        "display_name": name,
        "ror_id": None,
        "country_code": code,
        "country_name": country,
        "macro_region": region,
        "subregion": subregion,
        "normalized_category": "higher_education",
        "analytical_scope": "primary",
        "is_primary_research_scope": True,
        "is_primary_network_scope": True,
        "latitude": None,
        "longitude": None,
        "method_families": ["GIScience"],
        "strict_primary": True,
        "broad_primary": True,
    }


def _write_fixture(tmp_path: Path) -> dict[str, Path]:
    works: list[tuple[str, str | None, int, tuple[str, ...]]] = []

    # Binary sentinels make an off-by-one error at each 12/24/36-month boundary visible.
    boundary_months = {
        "2022-01": 1,
        "2022-02": 2,
        "2023-01": 4,
        "2023-02": 8,
        "2024-01": 16,
        "2024-02": 32,
        "2025-01": 64,
    }
    for month, count in boundary_months.items():
        for index in range(count):
            works.append(
                (f"W_BOUNDARY_{month}_{index:03d}", month, int(month[:4]), ("I_BOUNDARY",))
            )

    # An institution and edge with one old positive month must age out of sparse rolling output.
    # The early window also proves persistence retains its nominal 12-month denominator.
    works.append(("W_SINGLE", "2022-01", 2022, ("I_SINGLE", "I_OLD_PARTNER")))

    # Recent Alpha facts intentionally have unequal monthly denominators. Rolling shares must use
    # summed Work numerators rather than averaging the monthly shares.
    works.extend(
        [
            ("W_AB_PRESTART", "2024-01", 2024, ("I2_A", "I1_B")),
            ("W_AB_1", "2024-02", 2024, ("I2_A", "I1_B")),
            ("W_AB_2", "2024-02", 2024, ("I2_A", "I1_B")),
            ("W_AC_FEB", "2024-02", 2024, ("I2_A", "I3_C")),
            ("W_AC_DEC", "2024-12", 2024, ("I2_A", "I3_C")),
            ("W_AD", "2025-01", 2025, ("I2_A", "I4_D")),
            ("W_A_SINGLE_JAN", "2025-01", 2025, ("I2_A",)),
        ]
    )
    for index in range(8):
        works.append((f"W_A_SINGLE_DEC_{index}", "2024-12", 2024, ("I2_A",)))

    # A full-year window can recover 1 / 2 exact-date coverage. Once that year is only partially
    # overlapped, the missing Work's membership in the rolling window is unknowable.
    works.extend(
        [
            ("W_PARTIAL_JAN", "2024-01", 2024, ("I_PARTIAL",)),
            ("W_PARTIAL_FEB", "2024-02", 2024, ("I_PARTIAL",)),
            ("W_PARTIAL_EXACT", "2024-12", 2024, ("I_PARTIAL",)),
            ("W_PARTIAL_MISSING", None, 2024, ("I_PARTIAL",)),
        ]
    )

    date_rows = [_date_fact(work_id, year, month) for work_id, month, year, _ in works]
    membership_rows = [
        _membership(work_id, year, institution_id)
        for work_id, _, year, institution_ids in works
        for institution_id in institution_ids
    ]

    institution_aggregates: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "work_count": 0,
            "fractional_work_count": 0.0,
            "collaborative_work_count": 0,
            "single_institution_work_count": 0,
            "international_work_count": 0,
            "cross_region_work_count": 0,
        }
    )
    edge_aggregates: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "full_count": 0,
            "fractional_count": 0.0,
            "distinct_work_count": 0,
            "work_ids": [],
        }
    )
    for work_id, month, _, institution_ids in works:
        if month is None:
            continue
        distinct_ids = tuple(sorted(set(institution_ids)))
        institution_count = len(distinct_ids)
        countries = {_INSTITUTIONS[value][1] for value in distinct_ids}
        regions = {_INSTITUTIONS[value][3] for value in distinct_ids}
        for institution_id in distinct_ids:
            row = institution_aggregates[(month, institution_id)]
            row["work_count"] += 1
            row["fractional_work_count"] += 1.0 / institution_count
            row["collaborative_work_count"] += institution_count >= 2
            row["single_institution_work_count"] += institution_count == 1
            row["international_work_count"] += len(countries) >= 2
            row["cross_region_work_count"] += len(regions) >= 2
        if institution_count >= 2:
            fractional_weight = 2.0 / (institution_count * (institution_count - 1))
            for source_id, target_id in combinations(distinct_ids, 2):
                edge = edge_aggregates[(month, source_id, target_id)]
                edge["full_count"] += 1
                edge["fractional_count"] += fractional_weight
                edge["distinct_work_count"] += 1
                edge["work_ids"].append(work_id)

    institution_rows: list[dict[str, object]] = []
    for (month, institution_id), counts in sorted(institution_aggregates.items()):
        name, code, country, region, subregion = _INSTITUTIONS[institution_id]
        work_count = int(counts["work_count"])
        institution_rows.append(
            {
                "publication_month": month,
                "publication_year": int(month[:4]),
                "corpus_view": _VIEW[0],
                "hierarchy_view": _VIEW[1],
                "scope": "primary_research",
                "institution_id": institution_id,
                "display_name": name,
                "ror_id": None,
                "country_code": code,
                "country_name": country,
                "macro_region": region,
                "subregion": subregion,
                "institution_category": "higher_education",
                "analytical_scope": "primary",
                "latitude": None,
                "longitude": None,
                **counts,
                "international_collaboration_share": (
                    int(counts["international_work_count"]) / work_count
                ),
                "cross_region_collaboration_share": (
                    int(counts["cross_region_work_count"]) / work_count
                ),
            }
        )

    edge_rows: list[dict[str, object]] = []
    for (month, source_id, target_id), counts in sorted(edge_aggregates.items()):
        source = _INSTITUTIONS[source_id]
        target = _INSTITUTIONS[target_id]
        edge_rows.append(
            {
                "publication_month": month,
                "publication_year": int(month[:4]),
                "corpus_view": _VIEW[0],
                "hierarchy_view": _VIEW[1],
                "scope": "primary_research",
                "source_id": source_id,
                "target_id": target_id,
                "source_name": source[0],
                "target_name": target[0],
                "source_region": source[3],
                "target_region": target[3],
                "source_subregion": source[4],
                "target_subregion": target[4],
                "source_country": source[1],
                "target_country": target[1],
                "source_category": "higher_education",
                "target_category": "higher_education",
                **{key: value for key, value in counts.items() if key != "work_ids"},
                "large_consortium_work_count": 0,
                "excluded_threshold_work_count": 0,
                "maximum_consortium_size": 2,
                "topic_families": ["GIScience"],
                "work_ids_sample": sorted(counts["work_ids"])[:10],
                "distinct_topic_family_count": 1,
            }
        )

    paths = {
        "institution_month": tmp_path / "institution_outputs_month.parquet",
        "edge_month": tmp_path / "collaboration_edges_month.parquet",
        "work_dates": tmp_path / "work_publication_dates.parquet",
        "work_institutions": tmp_path / "work_institutions.parquet",
    }
    pq.write_table(pa.Table.from_pylist(institution_rows), paths["institution_month"])
    pq.write_table(pa.Table.from_pylist(edge_rows), paths["edge_month"])
    pq.write_table(pa.Table.from_pylist(date_rows), paths["work_dates"])
    pq.write_table(pa.Table.from_pylist(membership_rows), paths["work_institutions"])
    return paths


def _output_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "institution_rolling_path": tmp_path / "institution_outputs_rolling.parquet",
        "edge_intervals_path": tmp_path / "collaboration_edge_intervals.parquet",
        "coverage_path": tmp_path / "rolling_window_coverage.parquet",
        "reconciliation_path": tmp_path / "rolling_reconciliation.parquet",
    }


def _build(tmp_path: Path) -> tuple[dict[str, object], dict[str, Path], dict[str, Path]]:
    inputs = _write_fixture(tmp_path)
    outputs = _output_paths(tmp_path)
    summary = build_rolling_facts(
        inputs["institution_month"],
        inputs["edge_month"],
        inputs["work_dates"],
        inputs["work_institutions"],
        observation_start_month="2022-01",
        observation_end_month="2026-07",
        **outputs,
    )
    return summary, inputs, outputs


def _query_rows(
    outputs: dict[str, Path], *, institution_id: str | None = None, limit: int | None = None
) -> list[dict[str, object]]:
    return query_rolling_edges(
        outputs["edge_intervals_path"],
        outputs["coverage_path"],
        window_end="2025-01",
        window_months=12,
        corpus_view=_VIEW[0],
        hierarchy_view=_VIEW[1],
        institution_id=institution_id,
        limit=limit,
    )


def test_exact_calendar_boundaries_sparse_expiry_and_explicit_completeness(
    tmp_path: Path,
) -> None:
    _, _, outputs = _build(tmp_path)
    connection = duckdb.connect()
    try:
        boundary_rows = connection.execute(
            """
            SELECT window_start, window_end, window_months, work_count,
                   observed_month_count, eligible_month_count, coverage_ratio,
                   is_complete_window
            FROM read_parquet(?)
            WHERE institution_id = 'I_BOUNDARY' AND window_end = '2025-01'
            ORDER BY window_months
            """,
            [str(outputs["institution_rolling_path"])],
        ).fetchall()
        early_coverage = connection.execute(
            """
            SELECT window_end, window_months, observed_month_count, eligible_month_count,
                   coverage_ratio, is_complete_window
            FROM read_parquet(?)
            WHERE window_end IN ('2022-01', '2022-12')
            ORDER BY window_end, window_months
            """,
            [str(outputs["coverage_path"])],
        ).fetchall()
        expired = connection.execute(
            """
            SELECT count(*) FROM read_parquet(?)
            WHERE institution_id = 'I_SINGLE' AND window_end = '2023-01'
              AND window_months = 12
            """,
            [str(outputs["institution_rolling_path"])],
        ).fetchone()
        zero_work_rows = connection.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE work_count <= 0",
            [str(outputs["institution_rolling_path"])],
        ).fetchone()
        calendar_examples = connection.execute(
            """
            SELECT window_start, window_end
            FROM read_parquet(?)
            WHERE window_months = 12 AND window_end IN ('2025-12', '2026-07')
            ORDER BY window_end
            """,
            [str(outputs["coverage_path"])],
        ).fetchall()
    finally:
        connection.close()

    assert boundary_rows == [
        ("2024-02", "2025-01", 12, 96, 12, 12, 1.0, True),
        ("2023-02", "2025-01", 24, 120, 24, 24, 1.0, True),
        ("2022-02", "2025-01", 36, 126, 36, 36, 1.0, True),
    ]
    assert early_coverage == [
        ("2022-01", 12, 1, 1, pytest.approx(1 / 12), False),
        ("2022-01", 24, 1, 1, pytest.approx(1 / 24), False),
        ("2022-01", 36, 1, 1, pytest.approx(1 / 36), False),
        ("2022-12", 12, 12, 12, 1.0, True),
        ("2022-12", 24, 12, 12, 0.5, False),
        ("2022-12", 36, 12, 12, pytest.approx(1 / 3), False),
    ]
    assert expired == (0,)
    assert zero_work_rows == (0,)
    assert calendar_examples == [("2025-01", "2025-12"), ("2025-08", "2026-07")]

    early_edge = query_rolling_edges(
        outputs["edge_intervals_path"],
        outputs["coverage_path"],
        window_end="2022-01",
        window_months=12,
        corpus_view=_VIEW[0],
        hierarchy_view=_VIEW[1],
        institution_id="I_SINGLE",
    )
    assert len(early_edge) == 1
    assert early_edge[0]["active_month_count"] == 1
    assert early_edge[0]["edge_persistence"] == pytest.approx(1 / 12)
    assert early_edge[0]["observed_month_count"] == 1
    assert early_edge[0]["eligible_month_count"] == 1
    assert early_edge[0]["coverage_ratio"] == pytest.approx(1 / 12)
    assert early_edge[0]["is_complete_window"] is False


def test_activity_partner_metrics_and_edge_query_use_exact_rolling_window(
    tmp_path: Path,
) -> None:
    _, _, outputs = _build(tmp_path)
    connection = duckdb.connect()
    try:
        alpha = connection.execute(
            """
            SELECT window_start, window_end, window_months, work_count,
                   fractional_work_count, collaborative_work_count,
                   single_institution_work_count, international_work_count,
                   cross_region_work_count, international_collaboration_share,
                   cross_region_collaboration_share, partner_institution_count,
                   partner_country_count, fractional_collaboration_strength,
                   repeat_partner_count, repeat_partner_ratio, effective_partner_count
            FROM read_parquet(?)
            WHERE institution_id = 'I2_A' AND window_end = '2025-01'
              AND window_months = 12
            """,
            [str(outputs["institution_rolling_path"])],
        ).fetchone()
        singleton = connection.execute(
            """
            SELECT partner_institution_count, partner_country_count,
                   fractional_collaboration_strength, repeat_partner_count,
                   repeat_partner_ratio, effective_partner_count
            FROM read_parquet(?)
            WHERE institution_id = 'I_BOUNDARY' AND window_end = '2025-01'
              AND window_months = 12
            """,
            [str(outputs["institution_rolling_path"])],
        ).fetchone()
    finally:
        connection.close()

    assert alpha is not None
    assert alpha[:9] == ("2024-02", "2025-01", 12, 14, 11.5, 5, 9, 5, 3)
    assert alpha[9] == pytest.approx(5 / 14)
    assert alpha[10] == pytest.approx(3 / 14)
    assert alpha[11:16] == (3, 3, 5.0, 1, pytest.approx(1 / 3))
    expected_effective_count = math.exp(
        -(0.4 * math.log(0.4) + 0.4 * math.log(0.4) + 0.2 * math.log(0.2))
    )
    assert alpha[16] == pytest.approx(expected_effective_count)
    assert singleton == (0, 0, 0.0, 0, None, 0.0)

    incident = _query_rows(outputs, institution_id="I2_A")
    assert [(row["source_id"], row["target_id"]) for row in incident] == [
        ("I1_B", "I2_A"),
        ("I2_A", "I3_C"),
        ("I2_A", "I4_D"),
    ]
    assert [row["fractional_count"] for row in incident] == pytest.approx([2.0, 2.0, 1.0])
    assert [row["active_month_count"] for row in incident] == [1, 2, 1]
    assert [row["edge_persistence"] for row in incident] == pytest.approx([1 / 12, 2 / 12, 1 / 12])
    for row in incident:
        assert row["window_start"] == "2024-02"
        assert row["window_end"] == "2025-01"
        assert row["window_months"] == 12
        assert row["observed_month_count"] == 12
        assert row["eligible_month_count"] == 12
        assert row["coverage_ratio"] == 1.0
        assert row["is_complete_window"] is True
        assert row["source_id"] < row["target_id"]
    assert _query_rows(outputs, institution_id="I2_A", limit=2) == incident[:2]


def test_rolling_outputs_reconcile_with_monthly_sources_and_expose_partial_date_status(
    tmp_path: Path,
) -> None:
    _, _, outputs = _build(tmp_path)
    connection = duckdb.connect()
    try:
        reconciliation = connection.execute(
            """
            SELECT count(*) FILTER (WHERE NOT reconciliation_passed),
                   max(abs(full_count_difference)),
                   max(abs(fractional_count_difference))
            FROM read_parquet(?)
            """,
            [str(outputs["reconciliation_path"])],
        ).fetchone()
        invalid_coverage = connection.execute(
            """
            SELECT count(*) FROM read_parquet(?)
            WHERE eligible_month_count > observed_month_count
               OR observed_month_count > window_months
               OR abs(coverage_ratio - eligible_month_count::DOUBLE / window_months) > 1e-12
               OR is_complete_window <>
                  (observed_month_count = window_months
                   AND eligible_month_count = window_months)
            """,
            [str(outputs["coverage_path"])],
        ).fetchone()
        full_year_date = connection.execute(
            """
            SELECT exact_date_work_count, annual_only_work_count, date_coverage_ratio,
                   date_coverage_status
            FROM read_parquet(?)
            WHERE institution_id = 'I_PARTIAL' AND window_end = '2024-12'
              AND window_months = 12
            """,
            [str(outputs["institution_rolling_path"])],
        ).fetchone()
        split_year_date = connection.execute(
            """
            SELECT exact_date_work_count, annual_only_work_count, date_coverage_ratio,
                   date_coverage_status
            FROM read_parquet(?)
            WHERE institution_id = 'I_PARTIAL' AND window_end = '2025-01'
              AND window_months = 12
            """,
            [str(outputs["institution_rolling_path"])],
        ).fetchone()
    finally:
        connection.close()

    assert reconciliation is not None
    assert reconciliation[0] == 0
    assert reconciliation[1] == pytest.approx(0)
    assert reconciliation[2] == pytest.approx(0)
    assert invalid_coverage == (0,)
    assert full_year_date == (3, 1, 0.75, "exact")
    assert split_year_date == (2, 1, None, "indeterminate_boundary_year")


def test_explicit_observation_start_clamps_facts_but_keeps_nominal_window(
    tmp_path: Path,
) -> None:
    inputs = _write_fixture(tmp_path)
    limited_root = tmp_path / "limited"
    limited_root.mkdir()
    outputs = _output_paths(limited_root)
    build_rolling_facts(
        inputs["institution_month"],
        inputs["edge_month"],
        inputs["work_dates"],
        inputs["work_institutions"],
        institution_rolling_path=outputs["institution_rolling_path"],
        edge_intervals_path=outputs["edge_intervals_path"],
        coverage_path=outputs["coverage_path"],
        reconciliation_path=outputs["reconciliation_path"],
        window_months=(12,),
        observation_start_month="2024-02",
        observation_end_month="2024-02",
        corpus_views=[_VIEW[0]],
        hierarchy_views=[_VIEW[1]],
    )
    connection = duckdb.connect()
    try:
        coverage = connection.execute(
            """
            SELECT window_start, window_end, observed_month_count, eligible_month_count,
                   coverage_ratio, is_complete_window
            FROM read_parquet(?)
            """,
            [str(outputs["coverage_path"])],
        ).fetchone()
        activity = connection.execute(
            """
            SELECT work_count, fractional_work_count
            FROM read_parquet(?)
            WHERE institution_id = 'I2_A'
            """,
            [str(outputs["institution_rolling_path"])],
        ).fetchone()
        boundary = connection.execute(
            """
            SELECT work_count FROM read_parquet(?)
            WHERE institution_id = 'I_BOUNDARY'
            """,
            [str(outputs["institution_rolling_path"])],
        ).fetchone()
        date_qa = connection.execute(
            """
            SELECT exact_date_work_count, annual_only_work_count, date_coverage_ratio,
                   date_coverage_status
            FROM read_parquet(?)
            WHERE institution_id = 'I_PARTIAL'
            """,
            [str(outputs["institution_rolling_path"])],
        ).fetchone()
        reconciliation = connection.execute(
            """
            SELECT dimension, full_count_difference, fractional_count_difference,
                   reconciliation_passed
            FROM read_parquet(?) ORDER BY dimension
            """,
            [str(outputs["reconciliation_path"])],
        ).fetchall()
    finally:
        connection.close()

    # The nominal 12-month boundary remains visible, but only February is observed and eligible.
    assert coverage == ("2023-03", "2024-02", 1, 1, pytest.approx(1 / 12), False)
    # January activity exists in the source but lies outside declared coverage.
    assert activity == (3, 1.5)
    assert boundary == (32,)
    # The missing 2024 Work is an unallocatable boundary-year candidate, not an imputed month.
    assert date_qa == (1, 1, None, "indeterminate_boundary_year")
    assert [(row[0], row[3]) for row in reconciliation] == [("edge", True), ("institution", True)]
    assert all(row[1] == pytest.approx(0) and row[2] == pytest.approx(0) for row in reconciliation)

    incident = query_rolling_edges(
        outputs["edge_intervals_path"],
        outputs["coverage_path"],
        window_end="2024-02",
        window_months=12,
        corpus_view=_VIEW[0],
        hierarchy_view=_VIEW[1],
        institution_id="I2_A",
    )
    by_partner = {
        row["source_id"] if row["target_id"] == "I2_A" else row["target_id"]: row
        for row in incident
    }
    # The January Alpha-Beta edge is excluded from both count and active-month numerator.
    assert by_partner["I1_B"]["full_count"] == 2
    assert by_partner["I1_B"]["fractional_count"] == pytest.approx(2.0)
    assert by_partner["I1_B"]["active_month_count"] == 1
    assert by_partner["I1_B"]["edge_persistence"] == pytest.approx(1 / 12)
    assert by_partner["I1_B"]["window_start"] == "2023-03"
    assert by_partner["I1_B"]["observed_month_count"] == 1


def test_rolling_build_is_deterministic_atomic_and_preserves_inputs(tmp_path: Path) -> None:
    _, inputs, outputs = _build(tmp_path)
    annual_paths = [
        tmp_path / "institution_outputs_year.parquet",
        tmp_path / "edges_year.parquet",
        tmp_path / "region_flows_year.parquet",
    ]
    for path in annual_paths:
        pq.write_table(pa.Table.from_pylist([{"dataset": path.stem, "sentinel": 1}]), path)
    protected = [*inputs.values(), *annual_paths]
    protected_hashes = {path: file_sha256(path) for path in protected}
    first_hashes = {name: file_sha256(path) for name, path in outputs.items()}

    build_rolling_facts(
        inputs["institution_month"],
        inputs["edge_month"],
        inputs["work_dates"],
        inputs["work_institutions"],
        observation_start_month="2022-01",
        observation_end_month="2026-07",
        **outputs,
    )

    assert {name: file_sha256(path) for name, path in outputs.items()} == first_hashes
    assert {path: file_sha256(path) for path in protected} == protected_hashes


def test_edge_query_rejects_monthly_source_changed_after_index_build(tmp_path: Path) -> None:
    _, inputs, outputs = _build(tmp_path)
    edge_rows = pq.read_table(inputs["edge_month"]).to_pylist()
    edge_rows[0]["full_count"] += 1
    edge_rows[0]["distinct_work_count"] += 1
    pq.write_table(pa.Table.from_pylist(edge_rows), inputs["edge_month"])

    with pytest.raises(ValueError, match="checksum does not match the rolling index"):
        query_rolling_edges(
            outputs["edge_intervals_path"],
            outputs["coverage_path"],
            window_end="2025-01",
            window_months=12,
            corpus_view=_VIEW[0],
            hierarchy_view=_VIEW[1],
            institution_id="I2_A",
        )


def test_artifact_policy_versions_match_pipeline_resume_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_versions: list[dict[str, str]] = []

    def capture_artifact(**kwargs: object) -> None:
        source_versions = kwargs["source_versions"]
        assert isinstance(source_versions, dict)
        captured_versions.append(source_versions)

    monkeypatch.setattr(rolling_module, "write_json_artifact", capture_artifact)
    monkeypatch.setattr(rolling_module, "write_parquet_manifest", capture_artifact)
    project = tmp_path / "project.yml"
    contract = tmp_path / "school_decision.yml"
    project.write_text("project: rolling-test\n", encoding="utf-8")
    contract.write_text("contract: rolling-test\n", encoding="utf-8")
    outputs = {
        name: str(tmp_path / f"{name}.parquet")
        for name in (
            "institution_outputs_rolling",
            "collaboration_edge_window_intervals",
            "rolling_window_coverage",
            "rolling_reconciliation",
        )
    }
    write_rolling_artifacts(
        {
            "logical_input_hash": "test-hash",
            "corpus_views": ["strict", "broad"],
            "hierarchy_views": ["organization", "umbrella"],
            "observation_start_month": "2010-01",
            "observation_end_month": "2025-12",
            "outputs": outputs,
        },
        summary_path=tmp_path / "summary.json",
        run_id="test-run",
        project_config_path=project,
        school_decision_path=contract,
        command="test rolling artifacts",
    )

    assert len(captured_versions) == 5
    assert all(
        versions
        == {
            "rolling_fact_policy": "rolling-school-facts-2026-08-17-v1",
            "entity_scope": "primary_research",
            "rolling_corpus_views": "strict,broad",
            "rolling_hierarchy_views": "organization,umbrella",
            "rolling_observation_bounds": "2010-01:2025-12",
        }
        for versions in captured_versions
    )


def test_failed_group_promotion_recovers_interrupted_backup_and_prior_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, inputs, outputs = _build(tmp_path)
    first_hashes = {name: file_sha256(path) for name, path in outputs.items()}

    interrupted_destination = outputs["institution_rolling_path"]
    interrupted_backup = interrupted_destination.with_name(
        f".{interrupted_destination.name}.rollback.tmp"
    )
    rolling_module.os.replace(interrupted_destination, interrupted_backup)
    interrupted_destination.write_bytes(b"interrupted new rolling generation")

    # Make the candidate generation observably different from the accepted one. A broken group
    # rollback must not pass merely because a partially promoted file happens to have the old hash.
    institution_rows = pq.read_table(inputs["institution_month"]).to_pylist()
    boundary = next(
        row
        for row in institution_rows
        if row["publication_month"] == "2025-01" and row["institution_id"] == "I_BOUNDARY"
    )
    boundary["work_count"] += 1
    boundary["fractional_work_count"] += 1.0
    boundary["single_institution_work_count"] += 1
    pq.write_table(pa.Table.from_pylist(institution_rows), inputs["institution_month"])

    date_rows = pq.read_table(inputs["work_dates"]).to_pylist()
    date_rows.append(_date_fact("W_NEW_GENERATION", 2025, "2025-01"))
    pq.write_table(pa.Table.from_pylist(date_rows), inputs["work_dates"])
    membership_rows = pq.read_table(inputs["work_institutions"]).to_pylist()
    membership_rows.append(_membership("W_NEW_GENERATION", 2025, "I_BOUNDARY"))
    pq.write_table(pa.Table.from_pylist(membership_rows), inputs["work_institutions"])

    failing_destination = outputs["coverage_path"]
    failing_source = failing_destination.with_suffix(".parquet.tmp")
    real_replace = rolling_module.os.replace

    def fail_promotion(source: str | Path, destination: str | Path) -> None:
        if Path(source) == failing_source and Path(destination) == failing_destination:
            raise OSError("simulated rolling promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(rolling_module.os, "replace", fail_promotion)
    with pytest.raises(OSError, match="simulated rolling"):
        build_rolling_facts(
            inputs["institution_month"],
            inputs["edge_month"],
            inputs["work_dates"],
            inputs["work_institutions"],
            observation_start_month="2022-01",
            observation_end_month="2026-07",
            **outputs,
        )

    assert {name: file_sha256(path) for name, path in outputs.items()} == first_hashes
    assert not list(tmp_path.glob("*.tmp"))
    assert not [path for path in tmp_path.iterdir() if "rollback" in path.name]
