from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

from gisnet.dataset import file_sha256
from gisnet.schools.partners import build_school_partner_index, query_school_partners


def test_school_partner_index_uses_exact_rolling_denominators_and_stable_rank(
    tmp_path: Path,
) -> None:
    edge_month = tmp_path / "edges_month.parquet"
    intervals = tmp_path / "intervals.parquet"
    coverage = tmp_path / "coverage.parquet"
    outputs = tmp_path / "outputs.parquet"
    identities = tmp_path / "identities.parquet"
    schools = tmp_path / "schools.parquet"
    partner_index = tmp_path / "partners.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "publication_month": "2025-01",
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "source_id": "I1",
                    "target_id": "I2",
                    "full_count": 2,
                    "fractional_count": 0.5,
                    "distinct_work_count": 2,
                },
                {
                    "publication_month": "2025-02",
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "source_id": "I1",
                    "target_id": "I2",
                    "full_count": 1,
                    "fractional_count": 0.25,
                    "distinct_work_count": 1,
                },
            ]
        ),
        edge_month,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "window_months": 12,
                    "source_id": "I1",
                    "target_id": "I2",
                    "valid_from_window_end": "2025-02",
                    "valid_through_window_end": "2025-02",
                }
            ]
        ),
        intervals,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "window_start": "2024-03",
                    "window_end": "2025-02",
                    "window_months": 12,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "observed_month_count": 12,
                    "eligible_month_count": 12,
                    "coverage_ratio": 1.0,
                    "is_complete_window": True,
                    "observation_start_month": "2024-03",
                    "observation_end_month": "2025-02",
                    "edge_month_source_path": edge_month.name,
                    "edge_month_source_sha256": file_sha256(edge_month),
                }
            ]
        ),
        coverage,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "window_end": "2025-02",
                    "window_months": 12,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "institution_id": "I1",
                    "work_count": 4,
                },
                {
                    "window_end": "2025-02",
                    "window_months": 12,
                    "corpus_view": "broad",
                    "hierarchy_view": "organization",
                    "institution_id": "I2",
                    "work_count": 9,
                },
            ]
        ),
        outputs,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {"institution_id": "I1", "canonical_school_id": "I1", "is_collapsed": False},
                {"institution_id": "I2", "canonical_school_id": "I2", "is_collapsed": False},
            ]
        ),
        identities,
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "canonical_school_id": "I1",
                    "display_name": "Alpha",
                    "country_code": "NL",
                    "macro_region": "Europe",
                    "subregion": "Western Europe",
                },
                {
                    "canonical_school_id": "I2",
                    "display_name": "Beta",
                    "country_code": "US",
                    "macro_region": "Americas",
                    "subregion": "Northern America",
                },
            ]
        ),
        schools,
    )

    summary = build_school_partner_index(
        intervals,
        coverage,
        outputs,
        identities,
        schools,
        output_path=partner_index,
        corpus_views=("broad",),
        window_months=(12,),
        top_k=10,
    )
    rows = query_school_partners(
        partner_index,
        school_id="I1",
        corpus_view="broad",
        window_months=12,
    )

    assert len(rows) == 1
    assert rows[0]["partner_id"] == "I2"
    assert rows[0]["full_count"] == 3
    assert rows[0]["fractional_count"] == 0.75
    assert rows[0]["normalized_intensity"] == pytest.approx(0.75 / 6.0)
    assert rows[0]["active_month_count"] == 2
    assert rows[0]["edge_persistence"] == pytest.approx(2 / 12)
    assert rows[0]["partner_rank"] == 1
    assert summary["directed_partner_row_count"] == 2


def test_school_partner_index_refuses_stale_organization_rollups_after_collapse(
    tmp_path: Path,
) -> None:
    identities = tmp_path / "identities.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [{"institution_id": "I2", "canonical_school_id": "I1", "is_collapsed": True}]
        ),
        identities,
    )
    with pytest.raises(ValueError, match="rebuild from Work memberships"):
        build_school_partner_index(
            tmp_path / "missing-intervals.parquet",
            tmp_path / "missing-coverage.parquet",
            tmp_path / "missing-outputs.parquet",
            identities,
            tmp_path / "missing-schools.parquet",
            output_path=tmp_path / "partners.parquet",
        )
