"""Cross-layer acceptance validation for the school-decision system."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pandas as pd  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256
from gisnet.release import scan_public_release_files, verify_release_manifest
from gisnet.validation.reproducibility import CORE_DATASETS, verify_reproducibility
from gisnet.visualization.geographic_flows import (
    FLOW_LINE_WIDTH_DEFINITIONS,
    FlowDisplayPolicy,
    GeographicFlowSelection,
    build_flow_map_figure,
    build_flow_matrix_figure,
    build_flow_view,
    calibrated_line_width,
    filter_readable_flows,
    flow_source_options,
)
from gisnet.visualization.school_compare import align_school_profiles
from gisnet.visualization.school_profile import (
    query_school_profile,
    query_school_profiles,
    query_school_topics,
    query_school_topics_for_schools,
)

_VALIDATION_VERSION = "school-decision-system-validation-2026-08-31-v1"
_TOLERANCE = 1e-6


@dataclass(frozen=True)
class SchoolDecisionValidationPaths:
    """Repository-relative sources required by the acceptance matrix."""

    school_index: Path = Path("dashboard/data/school_index.parquet")
    school_partner_index: Path = Path("data/processed/school_partner_index.parquet")
    school_ego_partners: Path = Path("dashboard/data/school_ego_partners.parquet")
    prior_edge_core: Path = Path("data/processed/network_view_edges_year.parquet")
    subannual_reconciliation: Path = Path("data/processed/subannual_reconciliation.parquet")
    rolling_reconciliation: Path = Path("data/processed/rolling_reconciliation.parquet")
    rolling_coverage: Path = Path("data/processed/rolling_window_coverage.parquet")
    publication_dates: Path = Path("data/processed/work_publication_dates.parquet")
    region_reconciliation: Path = Path("data/processed/region_flow_reconciliation.parquet")
    region_flows: Path = Path("dashboard/data/matrix.parquet")
    geography_outputs: Path = Path("dashboard/data/geography_outputs.parquet")
    geography_anchors: Path = Path("dashboard/data/geography_anchors.parquet")
    school_profiles: Path = Path("dashboard/data/school_profiles.parquet")
    school_topics: Path = Path("dashboard/data/school_topic_profiles.parquet")
    work_corpus: Path = Path("data/processed/work_corpus.parquet")
    annual_edges: Path = Path("data/processed/edges_year.parquet")
    annual_outputs: Path = Path("data/processed/institution_outputs_year.parquet")
    annual_flows: Path = Path("data/processed/region_flows_year.parquet")
    dashboard_trends: Path = Path("dashboard/data/trends.parquet")
    dashboard_metadata: Path = Path("dashboard/data/metadata.json")
    manifest_directory: Path = Path(".agent/manifests")
    processed_directory: Path = Path("data/processed")
    release_manifest: Path = Path("release/manifest.json")
    release_checksum: Path = Path("release/manifest.json.sha256")

    def resolved(self, root: Path) -> SchoolDecisionValidationPaths:
        return SchoolDecisionValidationPaths(
            **{field: root / value for field, value in self.__dict__.items()}
        )


def validate_school_decision_system(
    *,
    root: str | Path = ".",
    paths: SchoolDecisionValidationPaths | None = None,
    expected_start_year: int = 2010,
    expected_end_year: int = 2025,
    reproducibility_datasets: dict[str, str] | None = None,
    public_roots: tuple[str, ...] | None = None,
    excluded_public_paths: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Validate all thirteen GISNET-138 acceptance requirements against stored evidence."""
    project = Path(root).resolve()
    sources = (paths or SchoolDecisionValidationPaths()).resolved(project)
    _require_sources(sources)
    checks: list[dict[str, Any]] = []
    connection = duckdb.connect()
    try:
        connection.execute("SET memory_limit = '2GB'")
        connection.execute("SET threads = 1")
        connection.execute("SET preserve_insertion_order = false")
        checks.extend(_identity_and_partner_checks(connection, sources))
        checks.extend(_temporal_checks(connection, sources))
        checks.append(_region_reconciliation_check(connection, sources))
        checks.append(_strict_subset_check(connection, sources))
        checks.append(
            _annual_regression_check(
                connection,
                sources,
                expected_start_year=expected_start_year,
                expected_end_year=expected_end_year,
            )
        )
    finally:
        connection.close()

    flow_checks = _flow_checks(sources)
    checks.extend(flow_checks)
    checks.append(_profile_compare_check(sources))

    roots = public_roots or tuple(
        path.relative_to(project).as_posix() for path in _default_public_roots(project)
    )
    privacy = scan_public_release_files(
        project,
        public_roots=roots,
        excluded_paths=excluded_public_paths,
    )
    release = verify_release_manifest(
        project,
        manifest_path=sources.release_manifest.relative_to(project),
        checksum_path=sources.release_checksum.relative_to(project),
    )
    checks.append(
        _passed(
            "public_output_secret_scan",
            "No API key is present in any public output.",
            {
                **privacy,
                "release_verified_file_count": release["verified_file_count"],
                "release_verified_size_bytes": release["verified_size_bytes"],
            },
        )
    )

    datasets = reproducibility_datasets or {
        name: str(project / path) for name, path in CORE_DATASETS.items()
    }
    reproducibility = verify_reproducibility(
        datasets,
        manifest_directory=sources.manifest_directory,
        processed_directory=sources.processed_directory,
    )
    dashboard_checks = _dashboard_checksum_checks(sources.dashboard_metadata, project)
    checks.append(
        _passed(
            "deterministic_rebuild_evidence",
            "Repeated builds are deterministic.",
            {
                "processed_dataset_check_count": reproducibility["dataset_check_count"],
                "processed_checksum_mismatch_count": reproducibility["checksum_mismatch_count"],
                "temporary_output_count": reproducibility["temporary_output_count"],
                "dashboard_table_check_count": len(dashboard_checks),
                "dashboard_checksum_mismatch_count": sum(
                    not row["checksum_matches"] for row in dashboard_checks
                ),
            },
        )
    )

    ordered = _order_checks(checks)
    logical_input_hash = semantic_hash(
        {
            "validation_version": _VALIDATION_VERSION,
            "checks": ordered,
        }
    )
    return {
        "schema_version": 1,
        "validation_version": _VALIDATION_VERSION,
        "status": "passed",
        "acceptance_check_count": len(ordered),
        "passed_check_count": sum(bool(check["passed"]) for check in ordered),
        "logical_input_hash": logical_input_hash,
        "checks": ordered,
        "required_regression_commands": [
            "uv run pytest tests/unit/test_school_decision_validation.py",
            (
                "uv run pytest tests/integration/test_school_decision_acceptance.py "
                "tests/integration/test_dashboard.py"
            ),
            "scripts/quality-gate.sh",
            "uv run python -m gisnet.release verify --root .",
        ],
        "generated_at_utc": _timestamp(),
    }


def write_school_decision_validation_artifact(
    payload: dict[str, Any],
    *,
    path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    school_decision_path: str | Path,
    command: str,
) -> None:
    """Atomically write the acceptance matrix and its provenance manifest."""
    source_manifests = [
        ".agent/manifests/school_index.json",
        ".agent/manifests/school_partner_index.json",
        ".agent/manifests/subannual_reconciliation.json",
        ".agent/manifests/rolling_reconciliation.json",
        ".agent/manifests/region_flow_reconciliation.json",
        ".agent/manifests/work_corpus.json",
        ".agent/manifests/dashboard_bundle_summary.json",
        ".agent/manifests/reproducibility_validation.json",
        "release/manifest.json",
    ]
    write_json_artifact(
        path=path,
        dataset_name="school_decision_validation",
        payload=payload,
        records=list(payload["checks"]),
        primary_key=["check_id"],
        run_id=run_id,
        config_hashes={
            "project": config_file_hash(project_config_path),
            "school_decision": config_file_hash(school_decision_path),
        },
        source_versions={"school_decision_validation_policy": _VALIDATION_VERSION},
        source_manifests=source_manifests,
        command=command,
    )


def _identity_and_partner_checks(
    connection: duckdb.DuckDBPyConnection,
    paths: SchoolDecisionValidationPaths,
) -> list[dict[str, Any]]:
    school_row = _one(
        connection,
        """
        SELECT
            count(*) AS school_count,
            count(DISTINCT school_id) AS distinct_school_count,
            count(*) FILTER (WHERE NOT in_prior_visualization_core) AS outside_core_count,
            count(*) FILTER (
                WHERE NOT in_prior_visualization_core
                  AND length(trim(display_name)) > 0
                  AND length(trim(school_id)) > 0
            ) AS searchable_outside_core_count
        FROM read_parquet(?)
        """,
        [str(paths.school_index)],
    )
    if not (
        int(school_row[0]) == int(school_row[1])
        and int(school_row[2]) > 0
        and int(school_row[2]) == int(school_row[3])
    ):
        raise ValueError("complete school index does not retain searchable outside-core schools")

    partner_row = _one(
        connection,
        """
        WITH old_core AS (
            SELECT source_id AS school_id FROM read_parquet(?)
            UNION
            SELECT target_id AS school_id FROM read_parquet(?)
        ), indexed AS (
            SELECT DISTINCT school_id FROM read_parquet(?)
        ), outside AS (
            SELECT school_id FROM indexed
            WHERE school_id NOT IN (SELECT school_id FROM old_core)
        ), public AS (
            SELECT DISTINCT school_id FROM read_parquet(?)
        )
        SELECT
            (SELECT count(*) FROM indexed),
            (SELECT count(*) FROM outside),
            (SELECT count(*) FROM outside JOIN public USING (school_id)),
            (SELECT min(school_id) FROM outside)
        """,
        [
            str(paths.prior_edge_core),
            str(paths.prior_edge_core),
            str(paths.school_partner_index),
            str(paths.school_ego_partners),
        ],
    )
    if int(partner_row[1]) <= 0 or int(partner_row[1]) != int(partner_row[2]):
        raise ValueError("outside-edge-core schools are missing retained public ego partners")
    return [
        _passed(
            "outside_prior_core_school_searchable",
            "A school outside the previous top-500 core is searchable.",
            {
                "school_count": int(school_row[0]),
                "outside_prior_core_count": int(school_row[2]),
                "searchable_outside_prior_core_count": int(school_row[3]),
            },
        ),
        _passed(
            "outside_global_edge_core_school_has_ego_partners",
            "A school outside the previous top-1,000-edge core still shows ego partners.",
            {
                "partner_index_school_count": int(partner_row[0]),
                "outside_prior_edge_core_count": int(partner_row[1]),
                "outside_prior_edge_core_with_public_ego_count": int(partner_row[2]),
                "example_school_id": str(partner_row[3]),
            },
        ),
    ]


def _temporal_checks(
    connection: duckdb.DuckDBPyConnection,
    paths: SchoolDecisionValidationPaths,
) -> list[dict[str, Any]]:
    subannual = _one(
        connection,
        """
        SELECT
            count(*),
            count(*) FILTER (WHERE NOT reconciliation_passed),
            max(abs(full_count_difference)),
            max(abs(fractional_count_difference)),
            count(DISTINCT temporal_grain)
        FROM read_parquet(?)
        """,
        [str(paths.subannual_reconciliation)],
    )
    if int(subannual[1]) or float(subannual[2]) > _TOLERANCE or float(subannual[3]) > _TOLERANCE:
        raise ValueError("subannual facts do not reconcile with annual eligible records")

    rolling = _one(
        connection,
        """
        SELECT
            count(*),
            count(*) FILTER (WHERE NOT reconciliation_passed),
            max(abs(full_count_difference)),
            max(abs(fractional_count_difference))
        FROM read_parquet(?)
        """,
        [str(paths.rolling_reconciliation)],
    )
    coverage = _one(
        connection,
        """
        SELECT
            count(*),
            count(*) FILTER (
                WHERE date_diff(
                    'month', strptime(window_start, '%Y-%m'), strptime(window_end, '%Y-%m')
                ) + 1 != window_months
            ),
            count(*) FILTER (WHERE window_months = 12 AND NOT is_complete_window),
            count(*) FILTER (WHERE window_months = 12 AND is_complete_window),
            count(*) FILTER (
                WHERE window_months = 12
                  AND year(strptime(window_start, '%Y-%m')) < year(strptime(window_end, '%Y-%m'))
            )
        FROM read_parquet(?)
        """,
        [str(paths.rolling_coverage)],
    )
    if (
        int(rolling[1])
        or float(rolling[2]) > _TOLERANCE
        or float(rolling[3]) > _TOLERANCE
        or int(coverage[1])
        or int(coverage[2]) <= 0
        or int(coverage[3]) <= 0
        or int(coverage[4]) <= 0
    ):
        raise ValueError("rolling calendar boundaries or reconciliations are invalid")

    dates = _one(
        connection,
        """
        SELECT
            count(*),
            count(*) FILTER (
                WHERE NOT subannual_date_eligible
                  AND (publication_month IS NOT NULL OR publication_quarter IS NOT NULL)
            ),
            count(*) FILTER (
                WHERE subannual_date_eligible
                  AND (
                    publication_date IS NULL
                    OR publication_month IS NULL
                    OR publication_quarter IS NULL
                    OR publication_month != strftime(publication_date, '%Y-%m')
                    OR publication_year != year(publication_date)
                  )
            )
        FROM read_parquet(?)
        """,
        [str(paths.publication_dates)],
    )
    if int(dates[1]) or int(dates[2]):
        raise ValueError("publication months contain imputed or calendar-inconsistent values")
    return [
        _passed(
            "subannual_reconciliation",
            "Monthly and quarterly eligible records reconcile with annual eligible records.",
            {
                "reconciliation_row_count": int(subannual[0]),
                "failed_row_count": int(subannual[1]),
                "maximum_full_count_error": float(subannual[2]),
                "maximum_fractional_count_error": float(subannual[3]),
                "temporal_grain_count": int(subannual[4]),
            },
        ),
        _passed(
            "rolling_12m_calendar_boundaries",
            "Rolling 12-month boundaries are correct across year boundaries.",
            {
                "rolling_reconciliation_row_count": int(rolling[0]),
                "failed_reconciliation_row_count": int(rolling[1]),
                "boundary_mismatch_count": int(coverage[1]),
                "incomplete_12m_window_count": int(coverage[2]),
                "complete_12m_window_count": int(coverage[3]),
                "cross_year_12m_window_count": int(coverage[4]),
            },
        ),
        _passed(
            "no_imputed_publication_months",
            "Missing exact dates are never assigned fake months.",
            {
                "publication_date_row_count": int(dates[0]),
                "ineligible_row_with_subannual_label_count": int(dates[1]),
                "eligible_calendar_mismatch_count": int(dates[2]),
            },
        ),
    ]


def _region_reconciliation_check(
    connection: duckdb.DuckDBPyConnection,
    paths: SchoolDecisionValidationPaths,
) -> dict[str, Any]:
    row = _one(
        connection,
        """
        SELECT
            count(*),
            max(abs(full_count_difference)),
            max(abs(fractional_count_difference)),
            max(abs(normalized_share_difference)),
            count(DISTINCT geographic_level)
        FROM read_parquet(?)
        """,
        [str(paths.region_reconciliation)],
    )
    if max(float(row[1]), float(row[2]), float(row[3])) > _TOLERANCE:
        raise ValueError("geographic flow totals do not reconcile with institution flows")
    return _passed(
        "region_flow_reconciliation",
        "Region-flow totals reconcile with institution-flow totals.",
        {
            "reconciliation_row_count": int(row[0]),
            "maximum_full_count_error": float(row[1]),
            "maximum_fractional_count_error": float(row[2]),
            "maximum_normalized_share_error": float(row[3]),
            "geographic_level_count": int(row[4]),
        },
    )


def _flow_checks(paths: SchoolDecisionValidationPaths) -> list[dict[str, Any]]:
    flows = pd.read_parquet(paths.region_flows)
    outputs = pd.read_parquet(paths.geography_outputs)
    anchors = pd.read_parquet(paths.geography_anchors)
    source_options = flow_source_options(
        flows,
        anchors,
        geographic_level="country",
        start_year=int(flows["year"].min()),
        end_year=int(flows["year"].max()),
        corpus_view="broad",
        hierarchy_view="organization",
    )
    if not source_options:
        raise ValueError("country flow validation has no sourced geography option")
    source_geography = source_options[0][0]
    map_matrix_mismatches = 0
    width_mismatches = 0
    displayed_rows = 0
    for metric in ("volume", "partner_share", "normalized_intensity"):
        selection = GeographicFlowSelection(
            geographic_level="country",
            source_geography=source_geography,
            start_year=int(flows["year"].min()),
            end_year=int(flows["year"].max()),
            corpus_view="broad",
            hierarchy_view="organization",
            counting_method="fractional",
            metric=metric,
        )
        view = filter_readable_flows(
            build_flow_view(flows, outputs, anchors, selection),
            FlowDisplayPolicy(top_n=12),
        )
        if view.empty:
            raise ValueError(f"country flow validation returned no rows for {metric}")
        displayed_rows += len(view)
        expected = dict(
            view[["target_geography", "selected_value"]].itertuples(index=False, name=None)
        )
        map_figure = build_flow_map_figure(view, selection)
        matrix_figure = build_flow_matrix_figure(view, selection)
        map_values = {
            str(row[0]): float(row[1])
            for trace in map_figure.data
            if trace.meta == "flow-partner-markers"
            for row in trace.customdata
        }
        matrix_values = {
            str(row[0][0]): float(row[0][1]) for row in matrix_figure.data[0].customdata
        }
        map_matrix_mismatches += int(map_values != expected) + int(matrix_values != expected)
        width_mismatches += sum(
            abs(
                float(row.calibrated_width_px)
                - calibrated_line_width(float(row.selected_value), metric)
            )
            > 1e-12
            for row in view.itertuples(index=False)
        )
    metadata = json.loads(paths.dashboard_metadata.read_text(encoding="utf-8"))
    definitions = metadata["geographic_flow_explorer"]["line_width_definitions"]
    definition_mismatches = sum(
        not str(definitions.get(metric, "")).startswith(definition)
        for metric, definition in FLOW_LINE_WIDTH_DEFINITIONS.items()
    )
    if map_matrix_mismatches or width_mismatches or definition_mismatches:
        raise ValueError("geographic map/matrix values or calibrated widths diverge")
    return [
        _passed(
            "country_map_matrix_reconciliation",
            "Country-flow map values reconcile with origin-destination matrix values.",
            {
                "source_geography": source_geography,
                "metric_count": 3,
                "displayed_row_count": displayed_rows,
                "map_matrix_mismatch_count": map_matrix_mismatches,
            },
        ),
        _passed(
            "calibrated_edge_width_semantics",
            "Edge width uses documented comparable semantics.",
            {
                "metric_count": 3,
                "displayed_row_count": displayed_rows,
                "width_mismatch_count": width_mismatches,
                "metadata_definition_mismatch_count": definition_mismatches,
            },
        ),
    ]


def _profile_compare_check(paths: SchoolDecisionValidationPaths) -> dict[str, Any]:
    school_index = pd.read_parquet(paths.school_index)
    selected_ids = [
        str(value)
        for value in school_index.sort_values(
            ["recent_24m_work_count", "school_id"],
            ascending=[False, True],
            kind="stable",
        )["school_id"].head(4)
    ]
    if len(selected_ids) < 2:
        raise ValueError("profile/compare validation requires at least two indexed schools")
    batch = (
        query_school_profiles(
            paths.school_profiles,
            school_ids=selected_ids,
            corpus_view="broad",
            window_months=24,
        )
        .sort_values("school_id", kind="stable")
        .reset_index(drop=True)
    )
    singles = (
        pd.concat(
            [
                query_school_profile(
                    paths.school_profiles,
                    school_id=school_id,
                    corpus_view="broad",
                    window_months=24,
                )
                for school_id in selected_ids
            ],
            ignore_index=True,
        )
        .sort_values("school_id", kind="stable")
        .reset_index(drop=True)
    )
    pd.testing.assert_frame_equal(batch, singles, check_like=False)
    topic_batch = (
        query_school_topics_for_schools(
            paths.school_topics,
            school_ids=selected_ids,
            corpus_view="broad",
            window_months=24,
        )
        .sort_values(["school_id", "topic_family"], kind="stable")
        .reset_index(drop=True)
    )
    topic_singles = (
        pd.concat(
            [
                query_school_topics(
                    paths.school_topics,
                    school_id=school_id,
                    corpus_view="broad",
                    window_months=24,
                )
                for school_id in selected_ids
            ],
            ignore_index=True,
        )
        .sort_values(["school_id", "topic_family"], kind="stable")
        .reset_index(drop=True)
    )
    pd.testing.assert_frame_equal(topic_batch, topic_singles, check_like=False)
    aligned = align_school_profiles(batch, school_index, school_ids=selected_ids)
    if list(aligned["school_id"].astype(str)) != selected_ids:
        raise ValueError("comparison alignment changed the stable-ID selection order")
    return _passed(
        "profile_compare_source_equality",
        "School Profile and Compare Schools use the same source metrics.",
        {
            "selected_school_count": len(selected_ids),
            "selected_school_ids": selected_ids,
            "profile_row_count": len(batch),
            "topic_row_count": len(topic_batch),
            "profile_mismatch_count": 0,
            "topic_mismatch_count": 0,
        },
    )


def _strict_subset_check(
    connection: duckdb.DuckDBPyConnection,
    paths: SchoolDecisionValidationPaths,
) -> dict[str, Any]:
    row = _one(
        connection,
        """
        SELECT
            count(*) FILTER (WHERE strict_primary),
            count(*) FILTER (WHERE broad_primary),
            count(*) FILTER (WHERE strict_primary AND NOT broad_primary)
        FROM read_parquet(?)
        """,
        [str(paths.work_corpus)],
    )
    if int(row[2]) or int(row[0]) > int(row[1]):
        raise ValueError("Strict primary Works are not a subset of Broad primary Works")
    return _passed(
        "strict_subset_broad",
        "Strict remains a subset of Broad.",
        {
            "strict_primary_work_count": int(row[0]),
            "broad_primary_work_count": int(row[1]),
            "strict_not_broad_count": int(row[2]),
        },
    )


def _annual_regression_check(
    connection: duckdb.DuckDBPyConnection,
    paths: SchoolDecisionValidationPaths,
    *,
    expected_start_year: int,
    expected_end_year: int,
) -> dict[str, Any]:
    datasets = {
        "annual_edges": paths.annual_edges,
        "annual_outputs": paths.annual_outputs,
        "annual_flows": paths.annual_flows,
        "dashboard_trends": paths.dashboard_trends,
    }
    observed: dict[str, dict[str, int]] = {}
    for name, path in datasets.items():
        row = _one(
            connection,
            "SELECT min(year), max(year), count(*) FROM read_parquet(?)",
            [str(path)],
        )
        observed[name] = {
            "min_year": int(row[0]),
            "max_year": int(row[1]),
            "row_count": int(row[2]),
        }
    mismatches = [
        name
        for name, values in observed.items()
        if values["min_year"] != expected_start_year or values["max_year"] != expected_end_year
    ]
    if mismatches:
        raise ValueError(f"annual regression range mismatch: {mismatches}")
    return _passed(
        "annual_pipeline_regression_evidence",
        "Existing annual pipeline regression evidence remains valid.",
        {
            "expected_start_year": expected_start_year,
            "expected_end_year": expected_end_year,
            "dataset_count": len(observed),
            "range_mismatch_count": 0,
            "datasets": observed,
        },
    )


def _dashboard_checksum_checks(metadata_path: Path, project: Path) -> list[dict[str, Any]]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    checks: list[dict[str, Any]] = []
    for name, table in sorted(metadata["tables"].items()):
        path = project / str(table["path"])
        actual = file_sha256(path)
        expected = str(table["sha256"])
        if actual != expected:
            raise ValueError(f"dashboard table checksum mismatch: {name}")
        checks.append(
            {
                "dataset_name": name,
                "path": str(table["path"]),
                "expected_sha256": expected,
                "actual_sha256": actual,
                "checksum_matches": True,
            }
        )
    return checks


def _require_sources(paths: SchoolDecisionValidationPaths) -> None:
    missing = sorted(
        str(path)
        for name, path in paths.__dict__.items()
        if name not in {"manifest_directory", "processed_directory"} and not path.is_file()
    )
    if not paths.manifest_directory.is_dir():
        missing.append(str(paths.manifest_directory))
    if not paths.processed_directory.is_dir():
        missing.append(str(paths.processed_directory))
    if missing:
        raise ValueError(f"school-decision validation sources are missing: {missing}")


def _default_public_roots(project: Path) -> list[Path]:
    return [
        project / value
        for value in (
            ".agent/manifests",
            "config",
            "dashboard/data",
            "data/reference",
            "figures",
            "outputs/reports",
        )
    ]


def _one(
    connection: duckdb.DuckDBPyConnection,
    query: str,
    parameters: list[str],
) -> tuple[Any, ...]:
    row = connection.execute(query, parameters).fetchone()
    if row is None:
        raise ValueError("validation query returned no row")
    return row


def _passed(check_id: str, requirement: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "requirement": requirement,
        "passed": True,
        "evidence": evidence,
    }


def _order_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = [
        "outside_prior_core_school_searchable",
        "outside_global_edge_core_school_has_ego_partners",
        "subannual_reconciliation",
        "rolling_12m_calendar_boundaries",
        "no_imputed_publication_months",
        "region_flow_reconciliation",
        "country_map_matrix_reconciliation",
        "calibrated_edge_width_semantics",
        "profile_compare_source_equality",
        "strict_subset_broad",
        "public_output_secret_scan",
        "deterministic_rebuild_evidence",
        "annual_pipeline_regression_evidence",
    ]
    by_id = {str(check["check_id"]): check for check in checks}
    if set(by_id) != set(order):
        missing = sorted(set(order).difference(by_id))
        unexpected = sorted(set(by_id).difference(order))
        raise ValueError(f"acceptance matrix mismatch; missing={missing}, unexpected={unexpected}")
    return [by_id[check_id] for check_id in order]


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
