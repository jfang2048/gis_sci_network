import json
from pathlib import Path

from gisnet.logging import configure_json_logging
from gisnet.secrets import get_openalex_api_key, non_secret_parameters, redact_text


def test_api_key_variable_priority() -> None:
    assert get_openalex_api_key({"OPENALEX_API_KEY": "upper", "openalex_api": "lower"}) == "upper"
    assert get_openalex_api_key({"openalex_api": "lower"}) == "lower"
    assert get_openalex_api_key({}) is None


def test_redaction_covers_urls_bearer_tokens_and_explicit_secret() -> None:
    secret = "super-secret-123"
    message = f"https://x.test?a=1&api_key={secret} Authorization: Bearer {secret} {secret}"
    redacted = redact_text(message, secrets=(secret,))
    assert secret not in redacted
    assert redacted.count("<redacted>") >= 2


def test_non_secret_parameters_exclude_credentials() -> None:
    assert non_secret_parameters({"api_key": "secret", "cursor": "*"}) == {"cursor": "*"}


def test_structured_log_never_contains_secret(tmp_path: Path) -> None:
    secret = "test-key-that-must-not-leak"
    destination = tmp_path / "run.jsonl"
    logger = configure_json_logging(destination, secrets=(secret,))
    logger.info("request https://api.test/works?api_key=%s", secret)
    for handler in logger.handlers:
        handler.flush()
    raw = destination.read_text(encoding="utf-8")
    assert secret not in raw
    assert "<redacted>" in raw
    assert json.loads(raw)["level"] == "INFO"
