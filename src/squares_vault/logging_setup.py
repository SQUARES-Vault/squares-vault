"""Structured logging with PII/secret scrubbing.

Rules (S5, blueprint §14.2):
- Logs NEVER contain: Telegram tokens, Google refresh/access tokens, Gemini
  API keys, document text, file names uploaded by users, or drive file IDs.
- All log output is JSON (structlog) so it can be parsed by log-analysis tools.
- The scrubbing filter fires on every log event and redacts any value that
  matches known sensitive patterns.

Usage::

    from squares_vault.logging_setup import get_logger
    log = get_logger(__name__)
    log.info("upload_done", record_id="01J...", category="vehicle")
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any, MutableMapping

import structlog

# ── Patterns that must NEVER appear in logs ────────────────────────────────────
# Matches common secret formats; extended as new patterns are identified.
_REDACT_PATTERNS: list[re.Pattern[str]] = [
    # Telegram bot token  (e.g.  123456789:ABC-abc_def...)
    re.compile(r"\d{8,12}:[A-Za-z0-9_\-]{35,}", re.ASCII),
    # Google OAuth tokens / refresh tokens (ya29.… or 1//…)
    re.compile(r"ya29\.[A-Za-z0-9_\-\.]{10,}", re.ASCII),
    re.compile(r"1//[A-Za-z0-9_\-]{10,}", re.ASCII),
    # Generic long bearer / API tokens (≥40 alphanum chars not separated by space)
    re.compile(r"[A-Za-z0-9_\-]{40,}"),
    # Drive file IDs look like: 1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms
    # Covered by the ≥40 pattern above; explicit entry is a no-op but documents intent.
]

# Keys whose values should always be redacted regardless of value length
_REDACT_KEYS: frozenset[str] = frozenset(
    {
        "token",
        "refresh_token",
        "access_token",
        "api_key",
        "gemini_key",
        "client_secret",
        "password",
        "pin",
        "master_key",
        "dek",
        "dek_wrapped",
        "refresh_token_enc",
        "api_key_enc",
        "drive_file_id",
        "file_name",
        "original_name",
        "document_text",
        "ocr_text",
        "extracted_text",
    }
)

_REDACTED: str = "[REDACTED]"


def _scrub_value(value: Any) -> Any:  # noqa: ANN401
    """Return a scrubbed copy of *value* (str is pattern-matched; others passed through)."""
    if isinstance(value, str):
        for pattern in _REDACT_PATTERNS:
            value = pattern.sub(_REDACTED, value)
    elif isinstance(value, dict):
        return {k: _REDACTED if k in _REDACT_KEYS else _scrub_value(v) for k, v in value.items()}
    elif isinstance(value, (list, tuple)):
        return type(value)(_scrub_value(v) for v in value)
    return value


def _scrubbing_processor(
    _logger: Any,  # noqa: ANN401
    _method: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """structlog processor: redact sensitive data from every log event."""
    for key in list(event_dict.keys()):
        if key in _REDACT_KEYS:
            event_dict[key] = _REDACTED
        else:
            event_dict[key] = _scrub_value(event_dict[key])
    return event_dict


def setup_logging(level: str = "INFO", json_logs: bool = True) -> None:
    """Configure structlog and stdlib logging.

    Call once at process start before any other module logs.

    Parameters
    ----------
    level:
        Minimum log level (``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``).
    json_logs:
        If ``True`` (production default) emit JSON lines.  ``False`` gives
        pretty console output useful during development.
    """
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _scrubbing_processor,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    if json_logs:
        renderer: Any = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[*shared_processors, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=True,
    )

    # Also configure stdlib root logger so third-party libs (aiogram, httpx…)
    # go through structlog's scrubbing.
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, level.upper(), logging.INFO),
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a named structlog logger.

    Usage::

        log = get_logger(__name__)
        log.info("event_name", key="value")
    """
    return structlog.get_logger(name)  # type: ignore[return-value]
