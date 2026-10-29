"""Publish a complete, source-grounded Strict GIS publication-activity ordering."""

from __future__ import annotations

import argparse
import hashlib
from io import BytesIO
from pathlib import Path

import pandas as pd  # type: ignore[import-untyped]

from gisnet.artifacts import current_git_commit, utc_timestamp
from gisnet.atomic import atomic_write_bytes, atomic_write_text
from gisnet.manifest import DatasetManifest
from gisnet.state import ProjectStateStore, RunLock, make_run_id

SOURCE = Path("dashboard/data/school_index.parquet")
REPORT = Path("outputs/reports/gis_strict_activity_ranking.md")
CSV = Path("outputs/reports/gis_strict_activity_ranking.csv")
SOURCE_MANIFESTS = [
    ".agent/manifests/dashboard_bundle_summary.json",
    ".agent/manifests/school_index.json",
]
POLICY_VERSION = "strict-gis-publication-activity-2010-2025-v1"
COLUMNS = [
    "position",
    "school_id",
    "display_name",
    "institution_category",
    "country_code",
    "country_name",
    "macro_region",
    "subregion",
    "strict_work_count",
    "broad_work_count",
    "openalex_id",
    "ror_id",
    "identity_status",
]


def build_ranking(index: pd.DataFrame) -> pd.DataFrame:
    """Sort the entire Broad-eligible school index by one disclosed activity metric."""
    required = set(COLUMNS) - {"position"}
    missing = sorted(required.difference(index.columns))
    if missing:
        raise ValueError(f"school index lacks ranking columns: {', '.join(missing)}")
    if index.empty or index["school_id"].isna().any():
        raise ValueError("school index requires nonempty, non-null stable IDs")
    if index["school_id"].eq("").any() or index["school_id"].duplicated().any():
        raise ValueError("school index requires unique, nonblank stable IDs")
    for count in ("strict_work_count", "broad_work_count"):
        if not pd.api.types.is_integer_dtype(index[count]) or index[count].isna().any():
            raise ValueError(f"{count} must contain non-null integers")
        if (index[count] < 0).any():
            raise ValueError(f"{count} must be nonnegative")
    if (index["strict_work_count"] > index["broad_work_count"]).any():
        raise ValueError("Strict GIS Work count cannot exceed Broad Work count")
    if index["display_name"].isna().any():
        raise ValueError("every ranked institution requires a display name")
    ranked = index.loc[:, COLUMNS[1:]].sort_values(
        ["strict_work_count", "display_name", "school_id"],
        ascending=[False, True, True],
        kind="stable",
    )
    ranked = ranked.reset_index(drop=True)
    ranked.insert(0, "position", range(1, len(ranked) + 1))
    return ranked.loc[:, COLUMNS]


def _cell(value: object) -> str:
    if pd.isna(value):
        return ""
    return (
        str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")
    )


def render_markdown(ranking: pd.DataFrame, *, source_sha256: str) -> str:
    """Render all institutions, not a preview or thresholded Top-N subset."""
    if list(ranking.columns) != COLUMNS or ranking.empty:
        raise ValueError("ranking schema is incomplete or empty")
    zero_count = int((ranking["strict_work_count"] == 0).sum())
    lines = [
        "# 全部机构的 Strict GIS 发表活动排序",
        "",
        "本表按 **2010-2025 完整日历年 Strict GIS 收录作品数** 从高到低排列。",
        "这是一项发表活动数量指标，不是综合 GIS 实力、科研质量、教学质量或择校排名。",  # noqa: RUF001
        "同数机构按显示名称、稳定 ID 排列；序号只是行号，不表示同数机构存在强弱差别。",  # noqa: RUF001
        "",
        f"- 机构总数：{len(ranking):,}；其中 Strict GIS 作品数为 0 的机构：{zero_count:,}。",  # noqa: RUF001
        "- 范围：完整 Broad Geospatial 主研究范围机构，包括大学、政府/非营利研究机构及"  # noqa: RUF001
        "研究设施，覆盖所有地区。",  # noqa: RUF001
        "- Strict GIS 是 Broad Geospatial 的子集；Strict 为 0 的 Broad 机构仍保留在名单中。",  # noqa: RUF001
        "- Topic 注册表仍属暂定、尚待人工审核；大型机构的数量优势、机构身份歧义和"  # noqa: RUF001
        "收录覆盖均影响解读。",
        f"- 来源：`{SOURCE.as_posix()}`（SHA-256: `{source_sha256}`）。",  # noqa: RUF001
        "- 可筛选原始字段：[下载完整 CSV](gis_strict_activity_ranking.csv)。",  # noqa: RUF001
        "",
        "| 序号 | Strict GIS 作品数 | Broad 作品数 | 机构 | 稳定 ID | "
        "机构类别 | 国家/地区 | 大区 |",
        "| ---: | ---: | ---: | --- | --- | --- | --- | --- |",
    ]
    for row in ranking.itertuples(index=False):
        lines.append(
            f"| {row.position} | {row.strict_work_count} | {row.broad_work_count} | "
            f"{_cell(row.display_name)} | {_cell(row.school_id)} | "
            f"{_cell(row.institution_category)} | {_cell(row.country_name)} | "
            f"{_cell(row.macro_region)} |"
        )
    return "\n".join(lines) + "\n"


def write_ranking_artifacts(root: Path, *, run_id: str) -> dict[str, object]:
    """Validate temporary Markdown/CSV before replacement and write provenance manifests."""
    source = root / SOURCE
    if not source.is_file():
        raise ValueError(f"source snapshot is missing: {SOURCE}")
    index = pd.read_parquet(source)
    ranking = build_ranking(index)
    source_sha = hashlib.sha256(source.read_bytes()).hexdigest()
    csv_payload = ranking.to_csv(index=False, lineterminator="\n").encode("utf-8")
    markdown_payload = render_markdown(ranking, source_sha256=source_sha)

    def validate_csv(temporary: Path) -> None:
        actual = pd.read_csv(BytesIO(temporary.read_bytes()), dtype={"school_id": "string"})
        if (
            list(actual.columns) != COLUMNS
            or len(actual) != len(ranking)
            or actual["school_id"].tolist() != ranking["school_id"].tolist()
            or actual["strict_work_count"].tolist() != ranking["strict_work_count"].tolist()
            or temporary.read_bytes() != csv_payload
        ):
            raise ValueError("temporary CSV failed complete ranking validation")

    def validate_markdown(temporary: Path) -> None:
        actual = temporary.read_text(encoding="utf-8")
        rows = [line for line in actual.splitlines() if line.startswith("| ")]
        if actual != markdown_payload or len(rows) != len(ranking) + 2:
            raise ValueError("temporary Markdown failed complete ranking validation")

    csv_path = root / CSV
    report_path = root / REPORT
    atomic_write_bytes(csv_path, csv_payload, validate=validate_csv)
    atomic_write_text(report_path, markdown_payload, validate=validate_markdown)
    state = ProjectStateStore(root / ".agent/state.json").load()
    project_hash = state.config_hashes["project"]
    for destination, dataset_name, column_count in (
        (csv_path, "gis_strict_activity_ranking_csv", len(COLUMNS)),
        (report_path, "gis_strict_activity_ranking_markdown", 8),
    ):
        manifest = DatasetManifest(
            dataset_name=dataset_name,
            created_at_utc=utc_timestamp(),
            run_id=run_id,
            git_commit=current_git_commit(),
            config_hashes={"project": project_hash},
            source_manifests=SOURCE_MANIFESTS,
            source_versions={"ranking_policy": POLICY_VERSION, "school_index_sha256": source_sha},
            row_count=len(ranking),
            column_count=column_count,
            primary_key=["school_id"],
            min_year=2010,
            max_year=2025,
            null_counts={column: int(ranking[column].isna().sum()) for column in COLUMNS},
            checksum_sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
            command="python -m gisnet.reporting.gis_activity_ranking --root .",
        )
        manifest.write(str(root / ".agent/manifests" / f"{dataset_name}.json"))
    return {
        "institution_count": len(ranking),
        "zero_strict_count": int((ranking["strict_work_count"] == 0).sum()),
        "source_sha256": source_sha,
        "csv_sha256": hashlib.sha256(csv_payload).hexdigest(),
        "markdown_sha256": hashlib.sha256(markdown_payload.encode("utf-8")).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--run-id")
    args = parser.parse_args()
    root = args.root.resolve()
    run_id = args.run_id or make_run_id(current_git_commit())
    with RunLock(root / ".agent/locks/run.lock", run_id=run_id, task_id="GISNET-141"):
        result = write_ranking_artifacts(root, run_id=run_id)
    print(
        f"Wrote {result['institution_count']:,} institutions in Strict GIS count order; "
        f"{result['zero_strict_count']:,} have zero Strict Works."
    )


if __name__ == "__main__":
    main()
