"""Secret retrieval and defensive redaction."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

SECRET_PARAMETER_NAMES = frozenset(
    {"api_key", "apikey", "access_token", "authorization", "openalex_api_key", "openalex_api"}
)
_QUERY_SECRET = re.compile(r"(?i)([?&](?:api[_-]?key|access[_-]?token|authorization)=)[^&#\s]+")
_BEARER_SECRET = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]+")


def get_openalex_api_key(environ: Mapping[str, str] | None = None) -> str | None:
    """Return the configured key, preferring the canonical uppercase variable."""
    values = os.environ if environ is None else environ
    return values.get("OPENALEX_API_KEY") or values.get("openalex_api") or None


def redact_text(value: object, *, secrets: tuple[str, ...] = ()) -> str:
    """Redact key-bearing query values, bearer tokens, and explicitly supplied secrets."""
    text = str(value)
    text = _QUERY_SECRET.sub(r"\1<redacted>", text)
    text = _BEARER_SECRET.sub(r"\1<redacted>", text)
    for secret in secrets:
        if secret:
            text = text.replace(secret, "<redacted>")
    return text


def sanitize_parameters(parameters: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return parameters that are safe to hash, cache, or include in diagnostics."""
    if not parameters:
        return {}
    return {
        str(key): ("<redacted>" if str(key).lower() in SECRET_PARAMETER_NAMES else value)
        for key, value in parameters.items()
    }


def non_secret_parameters(parameters: Mapping[str, Any] | None) -> dict[str, Any]:
    """Remove secret parameters entirely for deterministic cache identities."""
    if not parameters:
        return {}
    return {
        str(key): value
        for key, value in parameters.items()
        if str(key).lower() not in SECRET_PARAMETER_NAMES
    }
