from pathlib import Path
from typing import Any

from gisnet.openalex.cache import RawResponseCache
from gisnet.openalex.client import OpenAlexResponse
from gisnet.openalex.downloader import execute_download_plan


class WorkClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def get(self, _: str, *, params: dict[str, Any]) -> OpenAlexResponse:
        marker = str(params["filter"])
        cursor = str(params["cursor"])
        self.calls.append((marker, cursor))
        page = 1 if cursor == "*" else 2
        next_cursor = f"{marker}-page-2" if page == 1 else None
        return OpenAlexResponse(
            data={
                "meta": {"next_cursor": next_cursor},
                "results": [
                    {
                        "id": f"https://openalex.org/W{marker[-1]}{page}",
                        "updated_date": f"2026-08-0{page}T00:00:00.000000",
                    }
                ],
            },
            status_code=200,
            retrieved_at_utc=f"2026-08-0{page}T00:00:00Z",
            rate_limit={},
        )


def plan() -> dict[str, Any]:
    queries = [
        {
            "query_id": f"Q{index}",
            "predicted_result_count": 2,
            "predicted_page_count": 2,
            "parameters": {
                "filter": f"publication_year:202{index}",
                "select": "id,updated_date",
                "per-page": 1,
                "cursor": "*",
            },
        }
        for index in (0, 1)
    ]
    return {
        "logical_plan_hash": "plan-v1",
        "query_count": len(queries),
        "per_page": 1,
        "queries": queries,
    }


def test_downloader_completes_validates_and_resumes(tmp_path: Path) -> None:
    client = WorkClient()
    cache = RawResponseCache(tmp_path / "cache")
    status_path = tmp_path / "status.json"
    checkpoint_directory = tmp_path / "checkpoints"

    result = execute_download_plan(
        plan(),
        client,  # type: ignore[arg-type]
        cache,
        checkpoint_directory=checkpoint_directory,
        status_path=status_path,
    )

    assert result["status"] == "complete"
    assert result["status_counts"]["complete"] == 2
    assert result["actual_page_count"] == 4
    assert result["actual_result_count_including_duplicates"] == 4
    assert result["all_raw_page_checksums_validated"]
    assert all(record["source_updated_date_min"] for record in result["queries"])
    assert len(client.calls) == 4

    rerun = execute_download_plan(
        plan(),
        client,  # type: ignore[arg-type]
        cache,
        checkpoint_directory=checkpoint_directory,
        status_path=status_path,
    )
    assert rerun["status"] == "complete"
    assert len(client.calls) == 4


def test_downloader_records_unattempted_queries_as_blocked(tmp_path: Path) -> None:
    result = execute_download_plan(
        plan(),
        WorkClient(),  # type: ignore[arg-type]
        RawResponseCache(tmp_path / "cache"),
        checkpoint_directory=tmp_path / "checkpoints",
        status_path=tmp_path / "status.json",
        max_queries=1,
    )
    assert result["status"] == "blocked"
    assert result["status_counts"] == {
        "blocked": 1,
        "complete": 1,
        "failed": 0,
        "non_terminal": 0,
    }
    assert result["queries"][1]["status_reason"] == "not_started_after_max_queries"


def test_downloader_recovers_retrieval_labels_from_complete_checkpoints(tmp_path: Path) -> None:
    client = WorkClient()
    cache = RawResponseCache(tmp_path / "cache")
    status_path = tmp_path / "status.json"
    checkpoint_directory = tmp_path / "checkpoints"
    execute_download_plan(
        plan(),
        client,  # type: ignore[arg-type]
        cache,
        checkpoint_directory=checkpoint_directory,
        status_path=status_path,
    )
    call_count = len(client.calls)
    status_path.unlink()

    recovered = execute_download_plan(
        plan(),
        client,  # type: ignore[arg-type]
        cache,
        checkpoint_directory=checkpoint_directory,
        status_path=status_path,
    )

    assert len(client.calls) == call_count
    assert all(record["first_retrieved_at_utc"] for record in recovered["queries"])
    assert all(record["last_retrieved_at_utc"] for record in recovered["queries"])
    assert all(record["source_updated_date_min"] for record in recovered["queries"])
    assert all(record["source_updated_date_max"] for record in recovered["queries"])
