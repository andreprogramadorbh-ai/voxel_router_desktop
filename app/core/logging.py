"""Logging estruturado sem segredos ou identificadores clínicos desnecessários."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import structlog

from app.config.settings import AppPaths

SENSITIVE_KEYS = {
    "password", "password_hash", "token", "authorization", "api_key", "secret", "cookie",
    "patient_name", "patient_address", "patient_birth_date", "cpf",
}
TOKEN_PATTERN = re.compile(r"(?i)(bearer\s+|token[=:]\s*|password[=:]\s*)[^\s,;]+")


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "[REDACTED]" if key.lower() in SENSITIVE_KEYS else redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return TOKEN_PATTERN.sub(r"\1[REDACTED]", value)
    return value


def _redact_processor(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    return redact_value(event_dict)


def configure_logging(paths: AppPaths | None = None, level: int = logging.INFO) -> None:
    current_paths = paths or AppPaths.from_environment()
    current_paths.ensure()
    log_file = Path(current_paths.logs) / "router.jsonl"
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=[structlog.stdlib.add_log_level, structlog.processors.TimeStamper(fmt="iso")],
    )
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _redact_processor,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
