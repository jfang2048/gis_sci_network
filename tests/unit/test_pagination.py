import json
from pathlib import Path
from typing import Any

import pytest

from gisnet.openalex.cache import RawResponseCache
from gisnet.openalex.client import NetworkError, OpenAlexResponse, RateLimitError
from gisnet.openalex.pagination import CursorLoopError, CursorPaginator, RepeatedPageError


class FakeClient:
    def __init__(self, pages: dict[str, dict[str, Any]], *, fail_on: str | None = None) -> None:
        self.pages = pages
        self.fail_on = fail_on
        self.calls: list[str] = []

    def get(self, _: str, *, params: dict[str, Any]) -> OpenAlexResponse:
        cursor = str(params["cursor"])
        self.calls.append(cursor)
        if cursor == self.fail_on:
            raise NetworkError("simulated safe network failure")
        return OpenAlexResponse(
            data=self.pages[cursor],
            status_code=200,
            retrieved_at_utc="2026-08-05T00:00:00Z",
            rate_limit={},
        )


def _paginator(tmp_path: Path, client: FakeClient) -> CursorPaginator:
    return CursorPaginator(
        client,  # type: ignore[arg-type]
        RawResponseCache(tmp_path / "cache"),
        checkpoint_directory=tmp_path / "checkpoints",
        failure_directory=tmp_path / "failures",
    )


def test_interrupted_pagination_resumes_at_next_unwritten_page(tmp_path: Path) -> None:
    pages = {
        "*": {"results": [{"id": "W1"}], "meta": {"next_cursor": "c2"}},
        "c2": {"results": [{"id": "W2"}], "meta": {"next_cursor": "c3"}},
        "c3": {"results": [{"id": "W3"}], "meta": {"next_cursor": "c4"}},
        "c4": {"results": [{"id": "W4"}], "meta": {"next_cursor": None}},
    }
    interrupted = FakeClient(pages, fail_on="c4")
    with pytest.raises(NetworkError):
        _paginator(tmp_path, interrupted).download(query_id="q1", endpoint="works")
    assert interrupted.calls == ["*", "c2", "c3", "c4"]

    resumed = FakeClient(pages)
    checkpoint = _paginator(tmp_path, resumed).download(query_id="q1", endpoint="works")
    assert resumed.calls == ["c4"]
    assert checkpoint["status"] == "complete"
    assert checkpoint["page_count"] == checkpoint["result_count"] == 4

    completed = FakeClient(pages)
    assert (
        _paginator(tmp_path, completed).download(query_id="q1", endpoint="works")["status"]
        == "complete"
    )
    assert completed.calls == []


def test_cursor_loop_is_detected_and_recorded(tmp_path: Path) -> None:
    pages = {"*": {"results": [{"id": "W1"}], "meta": {"next_cursor": "*"}}}
    with pytest.raises(CursorLoopError):
        _paginator(tmp_path, FakeClient(pages)).download(query_id="loop", endpoint="works")
    assert (tmp_path / "failures" / "loop.json").exists()


def test_repeated_result_page_is_detected(tmp_path: Path) -> None:
    pages = {
        "*": {"results": [{"id": "W1"}], "meta": {"next_cursor": "c2"}},
        "c2": {"results": [{"id": "W1"}], "meta": {"next_cursor": None}},
    }
    with pytest.raises(RepeatedPageError):
        _paginator(tmp_path, FakeClient(pages)).download(query_id="repeat", endpoint="works")


def test_rate_limit_creates_resumable_blocked_checkpoint(tmp_path: Path) -> None:
    class RateLimitedClient(FakeClient):
        def get(self, _: str, *, params: dict[str, Any]) -> OpenAlexResponse:
            raise RateLimitError("rate limit exhausted")

    pages = {"*": {"results": [], "meta": {"next_cursor": None}}}
    with pytest.raises(RateLimitError):
        _paginator(tmp_path, RateLimitedClient(pages)).download(
            query_id="limited", endpoint="works"
        )
    checkpoint = json.loads((tmp_path / "checkpoints" / "limited.json").read_text(encoding="utf-8"))
    assert checkpoint["status"] == "blocked"
    assert checkpoint["block_reason"] == "rate_limit"

    resumed = FakeClient(pages)
    assert (
        _paginator(tmp_path, resumed).download(query_id="limited", endpoint="works")["status"]
        == "complete"
    )
