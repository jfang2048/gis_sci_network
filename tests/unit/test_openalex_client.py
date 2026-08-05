from collections.abc import Iterator

import httpx
import pytest

from gisnet.config import OpenAlexConfig
from gisnet.openalex.client import (
    AuthenticationError,
    OpenAlexClient,
    RateLimitError,
)


def _client(
    responses: Iterator[httpx.Response], *, max_retries: int = 2, sleeps: list[float] | None = None
) -> OpenAlexClient:
    def handler(_: httpx.Request) -> httpx.Response:
        return next(responses)

    transport = httpx.MockTransport(handler)
    http_client = httpx.Client(transport=transport, base_url="https://api.openalex.org")
    config = OpenAlexConfig(
        max_retries=max_retries,
        backoff_base_seconds=0,
        backoff_max_seconds=0,
        jitter_seconds=0,
    )
    return OpenAlexClient(
        config,
        api_key="secret-key",
        client=http_client,
        sleep=(sleeps.append if sleeps is not None else lambda _: None),
    )


def test_transient_server_errors_retry_then_succeed() -> None:
    sleeps: list[float] = []
    client = _client(
        iter(
            [
                httpx.Response(500, json={"error": "temporary"}),
                httpx.Response(503, json={"error": "temporary"}),
                httpx.Response(200, json={"results": [], "meta": {}}),
            ]
        ),
        sleeps=sleeps,
    )
    response = client.get("works", select=["id"], filter="publication_year:2025", per_page=1)
    assert response.status_code == 200
    assert len(sleeps) == 2


def test_authentication_failure_is_not_retried_or_leaked() -> None:
    client = _client(iter([httpx.Response(401, json={"error": "bad key"})]))
    with pytest.raises(AuthenticationError) as error:
        client.get("works")
    assert "secret-key" not in str(error.value)


def test_rate_limit_exhaustion_is_bounded_and_safe() -> None:
    client = _client(
        iter(
            [
                httpx.Response(429, headers={"Retry-After": "0"}, json={}),
                httpx.Response(429, headers={"Retry-After": "0"}, json={}),
            ]
        ),
        max_retries=1,
    )
    with pytest.raises(RateLimitError) as error:
        client.get("works", cursor="*")
    assert "secret-key" not in str(error.value)
    assert "2 attempts" in str(error.value)


def test_client_supports_all_required_query_parameters() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"results": [], "meta": {}})

    http_client = httpx.Client(
        transport=httpx.MockTransport(handler), base_url="https://api.openalex.org"
    )
    client = OpenAlexClient(OpenAlexConfig(), api_key="secret-key", client=http_client)
    client.get(
        "works",
        select="id,title",
        filter="publication_year:2025",
        search="GIS",
        group_by="type",
        per_page=20,
        cursor="*",
    )
    query = captured[0].url.params
    assert query["select"] == "id,title"
    assert query["filter"] == "publication_year:2025"
    assert query["search"] == "GIS"
    assert query["group_by"] == "type"
    assert query["per-page"] == "20"
    assert query["cursor"] == "*"
