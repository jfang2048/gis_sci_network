"""Structured JSON logging with mandatory secret redaction."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gisnet.secrets import redact_text


class RedactingJsonFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_text(record.getMessage(), secrets=self.secrets),
        }
        if record.exc_info:
            payload["exception"] = redact_text(
                self.formatException(record.exc_info), secrets=self.secrets
            )
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def configure_json_logging(
    path: str | Path,
    *,
    level: int = logging.INFO,
    secrets: tuple[str, ...] = (),
) -> logging.Logger:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"gisnet.{destination.stem}")
    logger.handlers.clear()
    logger.setLevel(level)
    logger.propagate = False
    handler = logging.FileHandler(destination, encoding="utf-8")
    handler.setFormatter(RedactingJsonFormatter(secrets))
    logger.addHandler(handler)
    return logger
