"""Build directed, corpus-internal institution citation-flow edges."""

from __future__ import annotations

import os
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from gisnet.artifacts import write_json_artifact
from gisnet.config import config_file_hash, semantic_hash
from gisnet.dataset import file_sha256, parquet_metrics, write_parquet_manifest

_STAGE_VERSION = "directed-citation-flow-2026-08-17-v1"


def build_citation_flows(
    works_path: str | Path,
    work_corpus_path: str | Path,
    work_institutions_path: str | Path,
    *,
    edges_year_path: str | Path,
    coverage_year_path: str | Path,
    corpus_views: list[str] | None = None,
    hierarchy_views: list[str] | None = None,
    memory_limit: str = "8GB",
    threads: int = 1,
) -> dict[str, Any]:
    """Aggregate citing-institution to cited-institution links by citing year.

    Both Works must belong to the selected corpus. Each Work-to-Work citation contributes one
    fractional unit divided across the Cartesian product of the citing and cited institutions.
    Institution self-flows are retained because they are meaningful in a citation layer.
    """
    works = Path(works_path)
    corpus = Path(work_corpus_path)
    institutions = Path(work_institutions_path)
    for source in (works, corpus, institutions):
        if not source.is_file():
            raise ValueError(f"citation-flow input does not exist: {source}")
    corpora = corpus_views or ["strict", "broad"]
    hierarchies = hierarchy_views or ["organization", "umbrella"]
    if not corpora or not set(corpora).issubset({"strict", "broad"}):
        raise ValueError("corpus views must contain only strict and broad")
    if not hierarchies or not set(hierarchies).issubset({"organization", "umbrella"}):
        raise ValueError("hierarchy views must contain only organization and umbrella")

    edges = Path(edges_year_path)
    coverage = Path(coverage_year_path)
    outputs = (edges, coverage)
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
    temporary = {output: output.with_suffix(".parquet.tmp") for output in outputs}
    edge_shards = {
        (view, hierarchy): edges.with_name(f".{edges.stem}.{view}.{hierarchy}.parquet.tmp")
        for view in corpora
        for hierarchy in hierarchies
    }
    coverage_shards = {
        (view, hierarchy): coverage.with_name(f".{coverage.stem}.{view}.{hierarchy}.parquet.tmp")
        for view in corpora
        for hierarchy in hierarchies
    }
    scratch = [*temporary.values(), *edge_shards.values(), *coverage_shards.values()]
    for path in scratch:
        path.unlink(missing_ok=True)

    connection = duckdb.connect()
    try:
        _configure(connection, memory_limit, threads)
        for corpus_view in corpora:
            corpus_flag = f"{corpus_view}_primary"
            for hierarchy_view in hierarchies:
                _write_edge_shard(
                    connection,
                    works=works,
                    corpus=corpus,
                    institutions=institutions,
                    destination=edge_shards[(corpus_view, hierarchy_view)],
                    corpus_view=corpus_view,
                    corpus_flag=corpus_flag,
                    hierarchy_view=hierarchy_view,
                )
                _write_coverage_shard(
                    connection,
                    works=works,
                    corpus=corpus,
                    institutions=institutions,
                    destination=coverage_shards[(corpus_view, hierarchy_view)],
                    corpus_view=corpus_view,
                    corpus_flag=corpus_flag,
                    hierarchy_view=hierarchy_view,
                )
        _combine_shards(connection, edge_shards.values(), temporary[edges], order="1, 2, 3, 4, 5")
        _combine_shards(
            connection,
            coverage_shards.values(),
            temporary[coverage],
            order="1, 2, 3",
        )
    except BaseException:
        for path in scratch:
            path.unlink(missing_ok=True)
        raise
    finally:
        connection.close()

    edge_metrics = parquet_metrics(
        temporary[edges],
        primary_key=["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
        required_columns={
            "year",
            "corpus_view",
            "hierarchy_view",
            "source_id",
            "target_id",
            "full_count",
            "fractional_count",
            "citation_direction",
        },
        year_column="year",
        memory_limit=memory_limit,
    )
    coverage_metrics = parquet_metrics(
        temporary[coverage],
        primary_key=["year", "corpus_view", "hierarchy_view"],
        required_columns={
            "year",
            "corpus_view",
            "hierarchy_view",
            "reference_count",
            "internal_corpus_reference_count",
            "institution_resolved_reference_count",
            "institution_pair_contribution_count",
            "institution_resolved_share",
        },
        year_column="year",
        memory_limit=memory_limit,
    )
    validation = duckdb.connect()
    try:
        reconciliation = validation.execute(
            """
            WITH edge_totals AS (
                SELECT year, corpus_view, hierarchy_view,
                       sum(full_count) AS full_count,
                       sum(fractional_count) AS fractional_count
                FROM read_parquet(?)
                GROUP BY year, corpus_view, hierarchy_view
            )
            SELECT
                count(*) FILTER (
                    WHERE coalesce(edge_totals.full_count, 0)
                          != coverage.institution_pair_contribution_count
                ) AS full_count_failure_count,
                count(*) FILTER (
                    WHERE abs(
                        coalesce(edge_totals.fractional_count, 0)
                        - coverage.institution_resolved_reference_count
                    ) > 1e-9 * greatest(coverage.institution_resolved_reference_count, 1)
                ) AS fractional_count_failure_count,
                max(abs(
                    coalesce(edge_totals.fractional_count, 0)
                    - coverage.institution_resolved_reference_count
                )),
                sum(coverage.reference_count),
                sum(coverage.internal_corpus_reference_count),
                sum(coverage.institution_resolved_reference_count),
                sum(coverage.negative_lag_reference_count),
                sum(coverage.institution_pair_contribution_count),
                (SELECT sum(full_count) FROM read_parquet(?)),
                (SELECT sum(fractional_count) FROM read_parquet(?))
            FROM read_parquet(?) coverage
            LEFT JOIN edge_totals USING (year, corpus_view, hierarchy_view)
            """,
            [
                str(temporary[edges]),
                str(temporary[edges]),
                str(temporary[edges]),
                str(temporary[coverage]),
            ],
        ).fetchone()
    finally:
        validation.close()
    if reconciliation is None:
        for path in scratch:
            path.unlink(missing_ok=True)
        raise ValueError("citation-flow reconciliation query returned no result")
    if int(reconciliation[0]) or int(reconciliation[1]):
        for path in scratch:
            path.unlink(missing_ok=True)
        raise ValueError(
            "citation-flow weights failed coverage reconciliation: "
            f"full failures={int(reconciliation[0])}, "
            f"fractional failures={int(reconciliation[1])}, "
            f"maximum fractional error={float(reconciliation[2] or 0.0):.6g}"
        )

    for output, path in temporary.items():
        os.replace(path, output)
    for path in [*edge_shards.values(), *coverage_shards.values()]:
        path.unlink(missing_ok=True)
    return {
        "schema_version": 1,
        "stage_version": _STAGE_VERSION,
        "logical_input_hash": semantic_hash(
            {
                "stage_version": _STAGE_VERSION,
                "works_sha256": file_sha256(works),
                "work_corpus_sha256": file_sha256(corpus),
                "work_institutions_sha256": file_sha256(institutions),
                "corpus_views": corpora,
                "hierarchy_views": hierarchies,
            }
        ),
        "layer_semantics": "directed corpus-internal citation flow, not collaboration",
        "citation_direction": "citing institution to cited institution",
        "fractional_weight_policy": (
            "one unit per Work-to-Work citation divided by citing institutions times cited "
            "institutions"
        ),
        "coverage_denominator": (
            "references made by selected-corpus Works with at least one in-scope citing institution"
        ),
        "self_flows_preserved": True,
        "annual_edge_count": int(edge_metrics["row_count"]),
        "coverage_row_count": int(coverage_metrics["row_count"]),
        "view_reference_count": int(reconciliation[3] or 0),
        "view_internal_corpus_reference_count": int(reconciliation[4] or 0),
        "view_institution_resolved_reference_count": int(reconciliation[5] or 0),
        "view_negative_lag_reference_count": int(reconciliation[6] or 0),
        "institution_pair_contribution_count": int(reconciliation[7] or 0),
        "annual_full_count": int(reconciliation[8] or 0),
        "annual_fractional_count": float(reconciliation[9] or 0.0),
        "maximum_fractional_reconciliation_error": float(reconciliation[2] or 0.0),
        "corpus_views": corpora,
        "hierarchy_views": hierarchies,
        "outputs": {
            "citation_edges_year": str(edges),
            "citation_flow_coverage_year": str(coverage),
        },
        "generated_at_utc": _timestamp(),
    }


def write_citation_artifacts(
    summary: dict[str, Any],
    *,
    summary_path: str | Path,
    run_id: str,
    project_config_path: str | Path,
    command: str,
) -> None:
    """Write the citation summary and manifests for both Parquet outputs."""
    config_hashes = {"project": config_file_hash(project_config_path)}
    source_manifests = [
        ".agent/manifests/works.json",
        ".agent/manifests/work_corpus.json",
        ".agent/manifests/work_institutions.json",
    ]
    source_versions = {"citation_flow_policy": _STAGE_VERSION}
    write_json_artifact(
        path=summary_path,
        dataset_name="citation_flow_summary",
        payload=summary,
        records=[summary],
        primary_key=["logical_input_hash"],
        run_id=run_id,
        config_hashes=config_hashes,
        source_versions=source_versions,
        source_manifests=source_manifests,
        command=command,
    )
    definitions = {
        "citation_edges_year": (
            ["year", "corpus_view", "hierarchy_view", "source_id", "target_id"],
            {"year", "source_id", "target_id", "full_count", "fractional_count"},
        ),
        "citation_flow_coverage_year": (
            ["year", "corpus_view", "hierarchy_view"],
            {"year", "reference_count", "institution_resolved_reference_count"},
        ),
    }
    for dataset_name, path in summary["outputs"].items():
        primary_key, required = definitions[dataset_name]
        write_parquet_manifest(
            path=path,
            dataset_name=dataset_name,
            primary_key=primary_key,
            required_columns=required,
            year_column="year",
            run_id=run_id,
            config_hashes=config_hashes,
            source_manifests=source_manifests,
            source_versions=source_versions,
            command=command,
        )


def _write_edge_shard(
    connection: duckdb.DuckDBPyConnection,
    *,
    works: Path,
    corpus: Path,
    institutions: Path,
    destination: Path,
    corpus_view: str,
    corpus_flag: str,
    hierarchy_view: str,
) -> None:
    connection.execute(
        f"""
        COPY (
            WITH corpus_works AS (
                SELECT work_id, publication_year
                FROM read_parquet(?)
                WHERE {corpus_flag}
            ), nodes AS (
                SELECT DISTINCT
                    wi.work_id,
                    wi.institution_id,
                    wi.display_name,
                    wi.macro_region,
                    wi.subregion,
                    wi.country_code,
                    wi.normalized_category
                FROM read_parquet(?) wi
                INNER JOIN corpus_works USING (work_id)
                WHERE wi.hierarchy_view = '{hierarchy_view}'
                  AND wi.is_primary_network_scope
            ), work_counts AS (
                SELECT work_id, count(*)::BIGINT AS institution_count
                FROM nodes GROUP BY work_id
            ), resolved_references AS (
                SELECT
                    source_work.work_id AS citing_work_id,
                    cited_work.work_id AS cited_work_id,
                    source_work.publication_year AS citing_year,
                    cited_work.publication_year AS cited_year,
                    source_count.institution_count AS citing_institution_count,
                    target_count.institution_count AS cited_institution_count
                FROM read_parquet(?) works
                INNER JOIN corpus_works source_work USING (work_id)
                INNER JOIN work_counts source_count USING (work_id)
                CROSS JOIN UNNEST(works.referenced_work_ids) referenced(cited_work_id)
                INNER JOIN corpus_works cited_work
                    ON cited_work.work_id = referenced.cited_work_id
                INNER JOIN work_counts target_count
                    ON target_count.work_id = referenced.cited_work_id
            ), expanded AS (
                SELECT
                    reference.citing_year AS year,
                    source.institution_id AS source_id,
                    target.institution_id AS target_id,
                    source.display_name AS source_name,
                    target.display_name AS target_name,
                    source.macro_region AS source_region,
                    target.macro_region AS target_region,
                    source.subregion AS source_subregion,
                    target.subregion AS target_subregion,
                    source.country_code AS source_country,
                    target.country_code AS target_country,
                    source.normalized_category AS source_category,
                    target.normalized_category AS target_category,
                    reference.citing_year - reference.cited_year AS citation_lag_years,
                    1.0 / (
                        reference.citing_institution_count
                        * reference.cited_institution_count
                    ) AS fractional_weight
                FROM resolved_references reference
                INNER JOIN nodes source ON source.work_id = reference.citing_work_id
                INNER JOIN nodes target ON target.work_id = reference.cited_work_id
            )
            SELECT
                year,
                '{corpus_view}' AS corpus_view,
                '{hierarchy_view}' AS hierarchy_view,
                source_id,
                target_id,
                any_value(source_name) AS source_name,
                any_value(target_name) AS target_name,
                any_value(source_region) AS source_region,
                any_value(target_region) AS target_region,
                any_value(source_subregion) AS source_subregion,
                any_value(target_subregion) AS target_subregion,
                any_value(source_country) AS source_country,
                any_value(target_country) AS target_country,
                any_value(source_category) AS source_category,
                any_value(target_category) AS target_category,
                source_id = target_id AS is_institution_self_flow,
                count(*)::BIGINT AS full_count,
                sum(fractional_weight) AS fractional_count,
                count(*) FILTER (WHERE citation_lag_years < 0)::BIGINT
                    AS negative_lag_full_count,
                min(citation_lag_years)::INTEGER AS minimum_citation_lag_years,
                max(citation_lag_years)::INTEGER AS maximum_citation_lag_years,
                'citing institution to cited institution' AS citation_direction,
                'corpus-internal citation flow; not collaboration' AS layer_semantics
            FROM expanded
            GROUP BY year, source_id, target_id
            ORDER BY year, source_id, target_id
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """,
        [str(corpus), str(institutions), str(works)],
    )


def _write_coverage_shard(
    connection: duckdb.DuckDBPyConnection,
    *,
    works: Path,
    corpus: Path,
    institutions: Path,
    destination: Path,
    corpus_view: str,
    corpus_flag: str,
    hierarchy_view: str,
) -> None:
    connection.execute(
        f"""
        COPY (
            WITH corpus_works AS (
                SELECT work_id, publication_year
                FROM read_parquet(?)
                WHERE {corpus_flag}
            ), scoped_counts AS (
                SELECT wi.work_id, count(DISTINCT wi.institution_id)::BIGINT
                    AS institution_count
                FROM read_parquet(?) wi
                INNER JOIN corpus_works USING (work_id)
                WHERE wi.hierarchy_view = '{hierarchy_view}'
                  AND wi.is_primary_network_scope
                GROUP BY wi.work_id
            ), citing_works AS (
                SELECT corpus_works.work_id, corpus_works.publication_year,
                       scoped_counts.institution_count
                FROM corpus_works
                INNER JOIN scoped_counts USING (work_id)
            ), reference_rows AS (
                SELECT
                    citing.work_id AS citing_work_id,
                    citing.publication_year AS citing_year,
                    citing.institution_count AS citing_institution_count,
                    referenced.cited_work_id,
                    cited.publication_year AS cited_year,
                    target_count.institution_count AS cited_institution_count
                FROM citing_works citing
                INNER JOIN read_parquet(?) works USING (work_id)
                LEFT JOIN UNNEST(works.referenced_work_ids) referenced(cited_work_id) ON TRUE
                LEFT JOIN corpus_works cited ON cited.work_id = referenced.cited_work_id
                LEFT JOIN scoped_counts target_count
                    ON target_count.work_id = referenced.cited_work_id
            )
            SELECT
                citing_year AS year,
                '{corpus_view}' AS corpus_view,
                '{hierarchy_view}' AS hierarchy_view,
                count(DISTINCT citing_work_id)::BIGINT AS citing_work_count,
                count(DISTINCT citing_work_id) FILTER (WHERE cited_work_id IS NOT NULL)::BIGINT
                    AS citing_work_with_references_count,
                count(cited_work_id)::BIGINT AS reference_count,
                count(*) FILTER (WHERE cited_year IS NOT NULL)::BIGINT
                    AS internal_corpus_reference_count,
                count(*) FILTER (WHERE cited_institution_count IS NOT NULL)::BIGINT
                    AS institution_resolved_reference_count,
                count(*) FILTER (WHERE cited_work_id IS NOT NULL AND cited_year IS NULL)::BIGINT
                    AS external_or_out_of_corpus_reference_count,
                count(*) FILTER (
                    WHERE cited_year IS NOT NULL AND cited_institution_count IS NULL
                )::BIGINT AS internal_without_scoped_institution_count,
                count(*) FILTER (WHERE cited_year > citing_year)::BIGINT
                    AS negative_lag_reference_count,
                count(*) FILTER (WHERE cited_work_id = citing_work_id)::BIGINT
                    AS self_work_reference_count,
                coalesce(sum(
                    citing_institution_count * cited_institution_count
                ) FILTER (WHERE cited_institution_count IS NOT NULL), 0)::BIGINT
                    AS institution_pair_contribution_count,
                CASE
                    WHEN count(cited_work_id) = 0 THEN NULL
                    ELSE count(*) FILTER (WHERE cited_institution_count IS NOT NULL)::DOUBLE
                         / count(cited_work_id)
                END AS institution_resolved_share,
                'references from selected-corpus Works with an in-scope citing institution'
                    AS coverage_denominator,
                'citing institution to cited institution' AS citation_direction,
                'corpus-internal citation flow; not collaboration' AS layer_semantics
            FROM reference_rows
            GROUP BY citing_year
            ORDER BY citing_year
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """,
        [str(corpus), str(institutions), str(works)],
    )


def _combine_shards(
    connection: duckdb.DuckDBPyConnection,
    shards: Iterable[Path],
    destination: Path,
    *,
    order: str,
) -> None:
    sources = ", ".join(f"'{_literal(path)}'" for path in shards)
    connection.execute(
        f"""
        COPY (
            SELECT * FROM read_parquet([{sources}]) ORDER BY {order}
        ) TO '{_literal(destination)}'
        (FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 100000)
        """
    )


def _configure(
    connection: duckdb.DuckDBPyConnection,
    memory_limit: str,
    threads: int,
) -> None:
    connection.execute("SET memory_limit = ?", [memory_limit])
    connection.execute("SET threads = ?", [threads])
    connection.execute("SET preserve_insertion_order = false")
    connection.execute("SET temp_directory = 'data/interim/duckdb-citations'")


def _literal(path: Path) -> str:
    return str(path).replace("'", "''")


def _timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
