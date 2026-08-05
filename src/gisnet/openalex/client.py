"""Authenticated OpenAlex HTTP client with bounded, observable retry behavior."""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from gisnet.config import OpenAlexConfig
from gisnet.secrets import get_openalex_api_key, non_secret_parameters, redact_text


class OpenAlexError(RuntimeError):
    """Base error whose message is safe to persist."""


class AuthenticationError(OpenAlexError):
    pass


class RateLimitError(OpenAlexError):
    pass


class NetworkError(OpenAlexError):
    pass


class ResponseError(OpenAlexError):
    pass


@dataclass(frozen=True)
class OpenAlexResponse:
    data: dict[str, Any]
    status_code: int
    retrieved_at_utc: str
    rate_limit: dict[str, str]


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class OpenAlexClient:
    """Small access layer that never includes credentials in diagnostics."""

    def __init__(
        self,
        config: OpenAlexConfig | None = None,
        *,
        api_key: str | None = None,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        random_seed: int = 20250805,
    ) -> None:
        self.config = config or OpenAlexConfig()
        self.api_key = api_key if api_key is not None else get_openalex_api_key()
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.config.base_url,
            timeout=httpx.Timeout(self.config.timeout_seconds),
            follow_redirects=False,
            headers={"User-Agent": "gis-collaboration-network/0.1.0"},
        )
        self._sleep = sleep
        self._random = random.Random(random_seed)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OpenAlexClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def get(
        self,
        endpoint: str,
        *,
        select: str | list[str] | None = None,
        filter: str | None = None,
        search: str | None = None,
        group_by: str | None = None,
        per_page: int | None = None,
        cursor: str | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> OpenAlexResponse:
        request_params: dict[str, Any] = dict(params or {})
        optional = {
            "select": ",".join(select) if isinstance(select, list) else select,
            "filter": filter,
            "search": search,
            "group_by": group_by,
            "per-page": per_page,
            "cursor": cursor,
        }
        request_params.update({key: value for key, value in optional.items() if value is not None})
        if self.api_key:
            request_params["api_key"] = self.api_key
        return self._request(endpoint, request_params)

    def _request(self, endpoint: str, params: Mapping[str, Any]) -> OpenAlexResponse:
        normalized_endpoint = "/" + endpoint.lstrip("/")
        safe_context = f"endpoint={normalized_endpoint} params={non_secret_parameters(params)}"
        attempts = self.config.max_retries + 1
        for attempt in range(attempts):
            try:
                response = self._client.get(normalized_endpoint, params=params)
            except httpx.RequestError as exc:
                if attempt + 1 >= attempts:
                    raise NetworkError(
                        f"OpenAlex network failure after {attempt + 1} attempts; {safe_context}; "
                        f"error={redact_text(type(exc).__name__, secrets=(self.api_key or '',))}"
                    ) from None
                self._sleep(self._backoff(attempt, None))
                continue

            if response.status_code in {401, 403}:
                raise AuthenticationError(
                    "OpenAlex authentication failed with "
                    f"HTTP {response.status_code}; {safe_context}"
                )
            if response.status_code == 429:
                if attempt + 1 >= attempts:
                    limits = self._rate_limit_headers(response.headers)
                    raise RateLimitError(
                        f"OpenAlex rate limit exhausted after {attempt + 1} attempts; "
                        f"{safe_context}; rate_limit={limits}"
                    )
                self._sleep(self._backoff(attempt, response.headers.get("retry-after")))
                continue
            if 500 <= response.status_code <= 599:
                if attempt + 1 >= attempts:
                    raise ResponseError(
                        f"OpenAlex server error HTTP {response.status_code} after "
                        f"{attempt + 1} attempts; {safe_context}"
                    )
                self._sleep(self._backoff(attempt, response.headers.get("retry-after")))
                continue
            if response.status_code >= 400:
                raise ResponseError(f"OpenAlex HTTP {response.status_code}; {safe_context}")
            try:
                data = response.json()
            except ValueError:
                raise ResponseError(
                    "OpenAlex returned invalid JSON with "
                    f"HTTP {response.status_code}; {safe_context}"
                ) from None
            if not isinstance(data, dict):
                raise ResponseError(f"OpenAlex returned a non-object JSON response; {safe_context}")
            return OpenAlexResponse(
                data=data,
                status_code=response.status_code,
                retrieved_at_utc=_utc_timestamp(),
                rate_limit=self._rate_limit_headers(response.headers),
            )
        raise AssertionError("retry loop exhausted without returning or raising")

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        delay = _retry_after_seconds(retry_after)
        if delay is None:
            delay = self.config.backoff_base_seconds * (2**attempt)
        delay = min(delay, self.config.backoff_max_seconds)
        if self.config.jitter_seconds:
            delay += self._random.uniform(0, self.config.jitter_seconds)
        return delay

    @staticmethod
    def _rate_limit_headers(headers: httpx.Headers) -> dict[str, str]:
        result: dict[str, str] = {}
        for name, value in headers.items():
            normalized = name.lower()
            if normalized.startswith("x-ratelimit-") or normalized == "retry-after":
                result[normalized] = value
        return result


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return max((parsed - datetime.now(UTC)).total_seconds(), 0.0)
