"""Structured, secret-conscious application logging."""

from __future__ import annotations

import logging
from pathlib import Path

SENSITIVE_MARKERS = ("authorization", "api_key", "token", "secret", "bearer")


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        lowered = rendered.lower()
        if any(marker in lowered for marker in SENSITIVE_MARKERS):
            return f"{self.formatTime(record)} {record.levelname} {record.name} [redacted]"
        return rendered


def configure_logging(log_file: Path, *, level: str = "INFO") -> None:
    log_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(
        RedactingFormatter(
            fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
