from __future__ import annotations

from datetime import date
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import gisnet.network.subannual as subannual_module
from gisnet.dataset import file_sha256
from gisnet.network.subannual import build_subannual_facts


def _date_fact(
    work_id: str,
    publication_year: int,
    publication_date: date | None,
    *,
    status: str = "exact_valid",
) -> dict[str, object]:
    eligible = publication_date is not None and status == "exact_valid"
    month = publication_date.strftime("%Y-%m") if eligible else None
    quarter = (
        f"{publication_date.year}-Q{((publication_date.month - 1) // 3) + 1}" if eligible else None
    )
    return {
        "work_id": work_id,
        "publication_year": publication_year,
        "publication_date_raw": publication_date.isoformat() if publication_date else None,
        "publication_date": publication_date if eligible else None,
        "publication_month": month,
        "publication_quarter": quarter,
        "has_exact_publication_date": publication_date is not None,
        "subannual_date_eligible": eligible,
        "date_quality_status": status,
    }


def _membership(
    work_id: str,
    publication_year: int,
    institution_id: str,
    *,
    hierarchy_view: str = "organization",
    display_name: str | None = None,
    country_code: str = "DE",
    country_name: str = "Germany",
    macro_region: str = "Europe",
    subregion: str = "Western Europe",
    is_primary_network_scope: bool = True,
) -> dict[str, object]:
    return {
        "publication_year": publication_year,
        "work_id": work_id,
        "hierarchy_view": hierarchy_view,
        "institution_id": institution_id,
        "display_name": display_name or institution_id,
        "ror_id": None,
        "country_code": country_code,
        "country_name": country_name,
        "macro_region": macro_region,
        "subregion": subregion,
        "normalized_category": "higher_education",
        "analytical_scope": "primary",
        "is_primary_research_scope": True,
        "is_primary_network_scope": is_primary_network_scope,
        "latitude": None,
        "longitude": None,
        "method_families": ["GIScience"],
        "strict_primary": True,
        "broad_primary": True,
    }


def _write_inputs(tmp_path: Path) -> tuple[Path, Path]:
    dates_path = tmp_path / "work_publication_dates.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                _date_fact("W_SINGLE", 2024, date(2024, 1, 31)),
                _date_fact("W_TWO", 2024, date(2024, 2, 29)),
                _date_fact("W_COLLAPSE", 2024, date(2024, 4, 30)),
                _date_fact("W_THREE", 2024, date(2024, 12, 31)),
                _date_fact("W_AFRICA", 2025, date(2025, 1, 1)),
                _date_fact("W_MISSING", 2024, None, status="missing"),
                _date_fact("W_MALFORMED", 2024, None, status="malformed"),
            ]
        ),
        dates_path,
    )

    memberships = [
        _membership("W_SINGLE", 2024, "I1"),
        _membership("W_TWO", 2024, "I1"),
        _membership(
            "W_TWO",
            2024,
            "I2",
            country_code="US",
            country_name="United States",
            macro_region="Americas",
            subregion="Northern America",
        ),
        _membership("W_THREE", 2024, "I1"),
        _membership(
            "W_THREE",
            2024,
            "I2",
            country_code="US",
            country_name="United States",
            macro_region="Americas",
            subregion="Northern America",
        ),
        _membership(
            "W_THREE",
            2024,
            "I3",
            country_code="JP",
            country_name="Japan",
            macro_region="Asia",
            subregion="Eastern Asia",
        ),
        # Defensive duplicate: stable-ID arithmetic must still use three institutions.
        _membership("W_THREE", 2024, "I1"),
        _membership("W_COLLAPSE", 2024, "I5"),
        _membership("W_COLLAPSE", 2024, "I6"),
        # Both organization identities collapse to one umbrella identity. No self-pair is valid.
        _membership("W_COLLAPSE", 2024, "I_PARENT", hierarchy_view="umbrella"),
        _membership("W_COLLAPSE", 2024, "I_PARENT", hierarchy_view="umbrella"),
        # School-decision scope includes primary research entities in Africa even though the
        # released annual network's legacy target-region flag is false.
        _membership(
            "W_AFRICA",
            2025,
            "I_AFRICA",
            country_code="ZA",
            country_name="South Africa",
            macro_region="Africa",
            subregion="Sub-Saharan Africa",
            is_primary_network_scope=False,
        ),
        _membership(
            "W_MISSING",
            2024,
            "I_MISSING",
            country_code="AU",
            country_name="Australia",
            macro_region="Oceania",
            subregion="Australia and New Zealand",
            is_primary_network_scope=False,
        ),
        _membership("W_MALFORMED", 2024, "I_MALFORMED"),
    ]
    institutions_path = tmp_path / "work_institutions.parquet"
    pq.write_table(pa.Table.from_pylist(memberships), institutions_path)
    return dates_path, institutions_path


def _output_paths(tmp_path: Path) -> dict[str, Path]:
    return {
        "institution_month_path": tmp_path / "institution_outputs_month.parquet",
        "institution_quarter_path": tmp_path / "institution_outputs_quarter.parquet",
        "edge_month_path": tmp_path / "collaboration_edges_month.parquet",
        "edge_quarter_path": tmp_path / "collaboration_edges_quarter.parquet",
        "reconciliation_path": tmp_path / "subannual_reconciliation.parquet",
        "sparsity_path": tmp_path / "subannual_sparsity.parquet",
    }


def _build(tmp_path: Path) -> tuple[dict[str, object], Path, Path, dict[str, Path]]:
    dates_path, institutions_path = _write_inputs(tmp_path)
    outputs = _output_paths(tmp_path)
    summary = build_subannual_facts(
        dates_path,
        institutions_path,
        corpus_views=["strict", "broad"],
        hierarchy_views=["organization", "umbrella"],
        **outputs,
    )
    return summary, dates_path, institutions_path, outputs


def test_subannual_facts_exclude_ineligible_dates_and_preserve_scope_and_arithmetic(
    tmp_path: Path,
) -> None:
    _, _, _, outputs = _build(tmp_path)
    connection = duckdb.connect()
    try:
        institution_rows = connection.execute(
            """
            SELECT publication_month, institution_id, work_count, fractional_work_count,
                   collaborative_work_count, single_institution_work_count,
                   international_collaboration_share, scope
            FROM read_parquet(?)
            WHERE corpus_view = 'strict' AND hierarchy_view = 'organization'
            ORDER BY publication_month, institution_id
            """,
            [str(outputs["institution_month_path"])],
        ).fetchall()
        edge_rows = connection.execute(
            """
            SELECT publication_month, source_id, target_id, full_count, fractional_count, scope
            FROM read_parquet(?)
            WHERE corpus_view = 'strict' AND hierarchy_view = 'organization'
            ORDER BY publication_month, source_id, target_id
            """,
            [str(outputs["edge_month_path"])],
        ).fetchall()
        excluded_institutions = connection.execute(
            """
            SELECT count(*) FROM read_parquet(?)
            WHERE institution_id IN ('I_MISSING', 'I_MALFORMED')
            """,
            [str(outputs["institution_month_path"])],
        ).fetchone()
        umbrella_nodes = connection.execute(
            """
            SELECT publication_month, institution_id, work_count, fractional_work_count
            FROM read_parquet(?)
            WHERE corpus_view = 'strict' AND hierarchy_view = 'umbrella'
            """,
            [str(outputs["institution_month_path"])],
        ).fetchall()
        umbrella_edges = connection.execute(
            """
            SELECT count(*) FROM read_parquet(?)
            WHERE corpus_view = 'strict' AND hierarchy_view = 'umbrella'
            """,
            [str(outputs["edge_month_path"])],
        ).fetchone()
    finally:
        connection.close()

    by_institution_month = {(row[0], row[1]): row[2:] for row in institution_rows}
    assert by_institution_month[("2024-01", "I1")] == (1, 1.0, 0, 1, 0.0, "primary_research")
    assert by_institution_month[("2024-02", "I1")] == (1, 0.5, 1, 0, 1.0, "primary_research")
    assert by_institution_month[("2024-02", "I2")] == (1, 0.5, 1, 0, 1.0, "primary_research")
    assert by_institution_month[("2024-12", "I1")][0] == 1
    assert by_institution_month[("2024-12", "I1")][1] == pytest.approx(1 / 3)
    assert by_institution_month[("2025-01", "I_AFRICA")] == (
        1,
        1.0,
        0,
        1,
        0.0,
        "primary_research",
    )
    assert excluded_institutions == (0,)

    february_edges = [row for row in edge_rows if row[0] == "2024-02"]
    assert february_edges == [("2024-02", "I1", "I2", 1, 1.0, "primary_research")]
    december_edges = [row for row in edge_rows if row[0] == "2024-12"]
    assert [(row[1], row[2]) for row in december_edges] == [
        ("I1", "I2"),
        ("I1", "I3"),
        ("I2", "I3"),
    ]
    assert sum(row[3] for row in december_edges) == 3
    assert sum(row[4] for row in december_edges) == pytest.approx(1.0)
    assert all(row[1] < row[2] for row in edge_rows)

    assert umbrella_nodes == [("2024-04", "I_PARENT", 1, 1.0)]
    assert umbrella_edges == (0,)


def test_month_and_quarter_facts_reconcile_to_the_same_exact_date_universe(
    tmp_path: Path,
) -> None:
    _, _, _, outputs = _build(tmp_path)
    connection = duckdb.connect()
    try:
        institution_differences = connection.execute(
            """
            WITH monthly AS (
                SELECT publication_year, corpus_view, hierarchy_view,
                       sum(work_count) AS full_count,
                       sum(fractional_work_count) AS fractional_count
                FROM read_parquet(?) GROUP BY ALL
            ), quarterly AS (
                SELECT publication_year, corpus_view, hierarchy_view,
                       sum(work_count) AS full_count,
                       sum(fractional_work_count) AS fractional_count
                FROM read_parquet(?) GROUP BY ALL
            )
            SELECT max(abs(monthly.full_count - quarterly.full_count)),
                   max(abs(monthly.fractional_count - quarterly.fractional_count))
            FROM monthly INNER JOIN quarterly USING (
                publication_year, corpus_view, hierarchy_view
            )
            """,
            [
                str(outputs["institution_month_path"]),
                str(outputs["institution_quarter_path"]),
            ],
        ).fetchone()
        edge_differences = connection.execute(
            """
            WITH monthly AS (
                SELECT publication_year, corpus_view, hierarchy_view,
                       sum(full_count) AS full_count,
                       sum(fractional_count) AS fractional_count
                FROM read_parquet(?) GROUP BY ALL
            ), quarterly AS (
                SELECT publication_year, corpus_view, hierarchy_view,
                       sum(full_count) AS full_count,
                       sum(fractional_count) AS fractional_count
                FROM read_parquet(?) GROUP BY ALL
            )
            SELECT max(abs(monthly.full_count - quarterly.full_count)),
                   max(abs(monthly.fractional_count - quarterly.fractional_count))
            FROM monthly INNER JOIN quarterly USING (
                publication_year, corpus_view, hierarchy_view
            )
            """,
            [str(outputs["edge_month_path"]), str(outputs["edge_quarter_path"])],
        ).fetchone()
        reconciliation = connection.execute(
            """
            SELECT dimension, temporal_grain, publication_year, corpus_view, hierarchy_view,
                   full_count_difference, fractional_count_difference, reconciliation_passed
            FROM read_parquet(?) ORDER BY ALL
            """,
            [str(outputs["reconciliation_path"])],
        ).fetchall()
    finally:
        connection.close()

    assert institution_differences == pytest.approx((0, 0))
    assert edge_differences == pytest.approx((0, 0))
    assert reconciliation
    assert {row[0] for row in reconciliation} == {"institution", "edge"}
    assert {row[1] for row in reconciliation} == {"month", "quarter"}
    assert all(row[5] == 0 for row in reconciliation)
    assert all(abs(row[6]) < 1e-10 for row in reconciliation)
    assert all(row[7] is True for row in reconciliation)


def test_sparsity_denominators_and_strata_are_recoverable(tmp_path: Path) -> None:
    _, _, _, outputs = _build(tmp_path)
    connection = duckdb.connect()
    try:
        rows = connection.execute(
            """
            SELECT dimension, temporal_grain, corpus_view, hierarchy_view, macro_region,
                   activity_tier, annual_entity_count, date_eligible_entity_count,
                   eligible_period_count, possible_period_count, active_period_count,
                   zero_period_count, zero_rate
            FROM read_parquet(?) ORDER BY ALL
            """,
            [str(outputs["sparsity_path"])],
        ).fetchall()
    finally:
        connection.close()

    assert rows
    assert {row[0] for row in rows} == {"institution", "edge"}
    assert {row[1] for row in rows} == {"month", "quarter"}
    assert {row[2] for row in rows} == {"strict", "broad"}
    assert any(row[4] == "Africa" for row in rows if row[0] == "institution")
    assert any(row[5] is not None for row in rows if row[0] == "institution")
    assert any(row[6] > row[7] for row in rows if row[0] == "institution")
    assert any(row[11] > 0 for row in rows if row[1] == "month")
    annual_only_oceania = next(
        row
        for row in rows
        if row[0:6] == ("institution", "month", "strict", "organization", "Oceania", "1_to_4_works")
    )
    assert annual_only_oceania[6:12] == (1, 0, 13, 13, 0, 13)
    for row in rows:
        annual_entities = row[6]
        date_eligible_entities = row[7]
        eligible_periods = row[8]
        possible_periods = row[9]
        active_periods = row[10]
        zero_periods = row[11]
        zero_rate = row[12]
        assert 0 <= date_eligible_entities <= annual_entities
        # The denominator is the annual institution/edge universe. Annual-only entities remain
        # visible as all-zero subannual cells rather than disappearing from coverage diagnostics.
        assert possible_periods == annual_entities * eligible_periods
        assert possible_periods == active_periods + zero_periods
        assert zero_rate == pytest.approx(zero_periods / possible_periods)


def test_partial_bounds_exclude_exact_out_of_window_entities_but_retain_annual_only(
    tmp_path: Path,
) -> None:
    dates_path, institutions_path = _write_inputs(tmp_path)
    partial_root = tmp_path / "partial"
    partial_root.mkdir()
    outputs = _output_paths(partial_root)
    build_subannual_facts(
        dates_path,
        institutions_path,
        corpus_views=["strict"],
        hierarchy_views=["organization"],
        observation_start_month="2024-02",
        observation_end_month="2024-12",
        **outputs,
    )
    connection = duckdb.connect()
    try:
        africa_count = connection.execute(
            "SELECT count(*) FROM read_parquet(?) WHERE macro_region = 'Africa'",
            [str(outputs["sparsity_path"])],
        ).fetchone()
        oceania = connection.execute(
            """
            SELECT annual_entity_count, date_eligible_entity_count, eligible_period_count,
                   possible_period_count, active_period_count, zero_period_count, zero_rate
            FROM read_parquet(?)
            WHERE dimension = 'institution' AND temporal_grain = 'month'
              AND corpus_view = 'strict' AND hierarchy_view = 'organization'
              AND macro_region = 'Oceania' AND activity_tier = '1_to_4_works'
            """,
            [str(outputs["sparsity_path"])],
        ).fetchone()
    finally:
        connection.close()
    assert africa_count == (0,)
    assert oceania == (1, 0, 11, 11, 0, 11, 1.0)


def test_subannual_outputs_are_deterministic_and_do_not_modify_annual_inputs(
    tmp_path: Path,
) -> None:
    _, dates_path, institutions_path, outputs = _build(tmp_path)
    annual_outputs = {
        "institution_outputs_year": tmp_path / "institution_outputs_year.parquet",
        "edges_year": tmp_path / "edges_year.parquet",
    }
    for name, path in annual_outputs.items():
        pq.write_table(pa.Table.from_pylist([{"dataset": name, "sentinel": 1}]), path)
    protected = {dates_path, institutions_path, *annual_outputs.values()}
    protected_hashes = {path: file_sha256(path) for path in protected}
    first_hashes = {name: file_sha256(path) for name, path in outputs.items()}

    build_subannual_facts(
        dates_path,
        institutions_path,
        corpus_views=["strict", "broad"],
        hierarchy_views=["organization", "umbrella"],
        **outputs,
    )

    assert {name: file_sha256(path) for name, path in outputs.items()} == first_hashes
    assert {path: file_sha256(path) for path in protected} == protected_hashes


def test_failed_subannual_group_promotion_restores_every_prior_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, dates_path, institutions_path, outputs = _build(tmp_path)
    first_hashes = {name: file_sha256(path) for name, path in outputs.items()}
    interrupted_destination = outputs["institution_month_path"]
    interrupted_backup = interrupted_destination.with_name(
        f".{interrupted_destination.name}.rollback.tmp"
    )
    subannual_module.os.replace(interrupted_destination, interrupted_backup)
    interrupted_destination.write_bytes(b"interrupted new generation")
    date_rows = pq.read_table(dates_path).to_pylist()
    missing = next(row for row in date_rows if row["work_id"] == "W_MISSING")
    missing.update(
        {
            "publication_date_raw": "2024-03-15",
            "publication_date": date(2024, 3, 15),
            "publication_month": "2024-03",
            "publication_quarter": "2024-Q1",
            "has_exact_publication_date": True,
            "subannual_date_eligible": True,
            "date_quality_status": "exact_valid",
        }
    )
    pq.write_table(pa.Table.from_pylist(date_rows), dates_path)

    failing_destination = outputs["institution_quarter_path"]
    failing_source = failing_destination.with_suffix(".parquet.tmp")
    real_replace = subannual_module.os.replace

    def fail_second_promotion(source: str | Path, destination: str | Path) -> None:
        if Path(destination) == failing_destination and Path(source) == failing_source:
            raise OSError("simulated subannual generation promotion failure")
        real_replace(source, destination)

    monkeypatch.setattr(subannual_module.os, "replace", fail_second_promotion)
    with pytest.raises(OSError, match="simulated subannual"):
        build_subannual_facts(
            dates_path,
            institutions_path,
            corpus_views=["strict", "broad"],
            hierarchy_views=["organization", "umbrella"],
            **outputs,
        )

    assert {name: file_sha256(path) for name, path in outputs.items()} == first_hashes
    assert not list(tmp_path.glob("*.tmp"))
    assert not [path for path in tmp_path.iterdir() if "rollback" in path.name]
