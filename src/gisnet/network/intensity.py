"""Normalized collaboration intensity and trailing edge persistence."""

from __future__ import annotations

import os
from collections import defaultdict
from datetime import UTC, datetime
from math import log1p, sqrt
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "edge-intensity-persistence-2026-08-05-v1"
_VISUALIZATION_METHOD = "non_primary_log_fractional_persistence_blend_v1"


def build_edge_intensity(
    edges_path: str | Path,
    institution_outputs_path: str | Path,
    *,
    output_path: str | Path,
    analysis_start_year: int,
    memory_limit: str = "4GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Join denominators and stream fixed-denominator 3/5-year persistence."""
    edges = Path(edges_path)
    nodes = Path(institution_outputs_path)
    for path in (edges, nodes):
        if not path.is_file():
            raise ValueError(f"edge-intensity input does not exist: {path}")
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".parquet.tmp")
    temporary.unlink(missing_ok=True)

    node_table = pq.read_table(
        nodes,
        columns=[
            "year",
            "corpus_view",
            "hierarchy_view",
            "institution_id",
            "work_count",
        ],
    )
    node_columns = [node_table.column(name).to_pylist() for name in node_table.column_names]
    denominators = {
        (int(year), str(corpus), str(hierarchy), str(institution)): int(work_count)
        for year, corpus, hierarchy, institution, work_count in zip(*node_columns, strict=True)
    }
    del node_columns, node_table

    histories: dict[tuple[str, str], dict[int, set[tuple[str, str]]]] = defaultdict(dict)
    last_sort_key: tuple[int, str, str, str, str] | None = None
    last_year: int | None = None
    writer: pq.ParquetWriter | None = None
    try:
        for batch in pq.ParquetFile(edges).iter_batches(batch_size=100_000):
            table = pa.Table.from_batches([batch])
            years = [int(value) for value in table.column("year").to_pylist()]
            corpora = [str(value) for value in table.column("corpus_view").to_pylist()]
            hierarchies = [str(value) for value in table.column("hierarchy_view").to_pylist()]
            sources = [str(value) for value in table.column("source_id").to_pylist()]
            targets = [str(value) for value in table.column("target_id").to_pylist()]
            fractions = [float(value) for value in table.column("fractional_count").to_pylist()]
            active_3: list[int] = []
            active_5: list[int] = []
            source_counts: list[int] = []
            target_counts: list[int] = []
            intensities: list[float] = []
            scores: list[float] = []
            for year, corpus, hierarchy, source, target, fraction in zip(
                years,
                corpora,
                hierarchies,
                sources,
                targets,
                fractions,
                strict=True,
            ):
                sort_key = (year, corpus, hierarchy, source, target)
                if last_sort_key is not None and sort_key < last_sort_key:
                    raise ValueError(
                        "edges must be sorted by year, corpus, hierarchy, source, and target"
                    )
                last_sort_key = sort_key
                if last_year != year:
                    for history in histories.values():
                        for expired_year in [item for item in history if item < year - 4]:
                            del history[expired_year]
                    last_year = year
                view = (corpus, hierarchy)
                pair = (source, target)
                history = histories[view]
                count_3 = 1 + sum(pair in history.get(year - offset, ()) for offset in (1, 2))
                count_5 = count_3 + sum(pair in history.get(year - offset, ()) for offset in (3, 4))
                history.setdefault(year, set()).add(pair)
                source_count = denominators.get((year, corpus, hierarchy, source), 0)
                target_count = denominators.get((year, corpus, hierarchy, target), 0)
                if source_count <= 0 or target_count <= 0:
                    raise ValueError(
                        f"missing positive output denominator for {year} {view} {pair}"
                    )
                intensity = fraction / sqrt(source_count * target_count)
                active_3.append(count_3)
                active_5.append(count_5)
                source_counts.append(source_count)
                target_counts.append(target_count)
                intensities.append(intensity)
                scores.append(log1p(fraction) * (0.5 + 0.5 * count_5 / 5.0))
            table = table.append_column("active_years_3y", pa.array(active_3, pa.int8()))
            table = table.append_column("active_years_5y", pa.array(active_5, pa.int8()))
            table = table.append_column("source_work_count", pa.array(source_counts, pa.int64()))
            table = table.append_column("target_work_count", pa.array(target_counts, pa.int64()))
            table = table.append_column("normalized_intensity", pa.array(intensities, pa.float64()))
            table = table.append_column(
                "persistence_3y", pa.array((value / 3.0 for value in active_3), pa.float64())
            )
            table = table.append_column(
                "persistence_5y", pa.array((value / 5.0 for value in active_5), pa.float64())
            )
            table = table.append_column(
                "persistence_3y_incomplete_window",
                pa.array((year - analysis_start_year + 1 < 3 for year in years), pa.bool_()),
            )
            table = table.append_column(
                "persistence_5y_incomplete_window",
                pa.array((year - analysis_start_year + 1 < 5 for year in years), pa.bool_()),
            )
            table = table.append_column("visualization_score", pa.array(scores, pa.float64()))
            table = table.append_column(
                "visualization_score_is_primary", pa.array([False] * len(years), pa.bool_())
            )
            table = table.append_column(
                "visualization_score_method",
                pa.array([_VISUALIZATION_METHOD] * len(years), pa.string()),
            )
            if writer is None:
                writer = pq.ParquetWriter(temporary, table.schema, compression="zstd")
            writer.write_table(table, row_group_size=100_000)
        if writer is None:
            raise ValueError("edge-intensity input contains no annual edges")
    except BaseException:
        if writer is not None:
            writer.close()
            writer = None
        temporary.unlink(missing_ok=True)
        raise
    finally:
        if writer is not None:
            writer.close()
    del denominators, histories

    metrics = parquet_metrics(
        temporary,
        primary_key=["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
        required_columns={
            "year",
            "source_id",
            "target_id",
            "normalized_intensity",
            "persistence_3y",
            "persistence_5y",
            "visualization_score_is_primary",
        },
        year_column="year",
    )
    validation = duckdb.connect()
    try:
        validation.execute("SET memory_limit = ?", [memory_limit])
        validation.execute("SET threads = ?", [threads])
        values = validation.execute(
            """
            SELECT
                count(*) FILTER (
                    WHERE normalized_intensity < 0 OR NOT isfinite(normalized_intensity)
                ),
                count(*) FILTER (WHERE persistence_3y < 0 OR persistence_3y > 1),
                count(*) FILTER (WHERE persistence_5y < 0 OR persistence_5y > 1),
                count(*) FILTER (WHERE visualization_score_is_primary),
                count(*) FILTER (WHERE persistence_3y_incomplete_window),
                count(*) FILTER (WHERE persistence_5y_incomplete_window),
                min(normalized_intensity),
                max(normalized_intensity)
            FROM read_parquet(?)
            """,
            [str(temporary)],
        ).fetchone()
        validation_source_count = validation.execute(
            "SELECT count(*) FROM read_parquet(?)", [str(edges)]
        ).fetchone()
    finally:
        validation.close()
    if values is None or validation_source_count is None:
        raise ValueError("edge-intensity validation query failed")
    if any(int(values[index]) for index in range(4)):
        raise ValueError("edge intensity, persistence, or score-label invariant failed")
    if int(metrics["row_count"]) != int(validation_source_count[0]):
        raise ValueError("edge denominators did not join to every annual edge")
    os.replace(temporary, output)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "edges_sha256": file_sha256(edges),
                "institution_outputs_sha256": file_sha256(nodes),
                "analysis_start_year": analysis_start_year,
            }
        ),
        "edge_year_count": int(metrics["row_count"]),
        "invalid_intensity_count": int(values[0]),
        "invalid_persistence_3y_count": int(values[1]),
        "invalid_persistence_5y_count": int(values[2]),
        "persistence_3y_incomplete_window_count": int(values[4]),
        "persistence_5y_incomplete_window_count": int(values[5]),
        "minimum_normalized_intensity": float(values[6]),
        "maximum_normalized_intensity": float(values[7]),
        "visualization_score_method": _VISUALIZATION_METHOD,
        "visualization_score_is_primary": False,
        "outputs": {"edges_metrics_year": str(output)},
        "generated_at_utc": _timestamp(),
    }


def write_intensity_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [
        ".agent/manifests/edges_year.json",
        ".agent/manifests/institution_outputs_year.json",
    ]
    source_versions = {"intensity_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="edge_intensity_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions=source_versions,
        source_manifests=source_manifests,
        command=command,
    )
    write_parquet_manifest(
        path=summary["outputs"]["edges_metrics_year"],
        dataset_name="edges_metrics_year",
        primary_key=["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
        required_columns={
            "year",
            "normalized_intensity",
            "persistence_3y",
            "persistence_5y",
            "visualization_score_is_primary",
        },
        year_column="year",
        run_id=run_id,
        config_hashes=config_hashes,
        source_manifests=source_manifests,
        source_versions=source_versions,
        command=command,
    )


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
