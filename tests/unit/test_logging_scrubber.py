"""Unit tests for the log scrubbing filter (logging_setup.py).

Acceptance criteria (M0):
- A Telegram bot token never appears in a log event dict after scrubbing.
- A Google OAuth token never appears.
- A value in a _REDACT_KEYS field is replaced with [REDACTED].
- Normal log keys are passed through unchanged.
- Nested dicts and lists are scrubbed recursively.
"""

from __future__ import annotations

import pytest

from squares_vault.logging_setup import _scrub_value, _scrubbing_processor, _REDACTED

# ── _scrub_value ───────────────────────────────────────────────────────────────


def test_scrub_telegram_token() -> None:
    """Telegram bot tokens are scrubbed from string values."""
    raw = "Bot token is 123456789:ABCDEFGabcdefgABCDEFGabcdefg12345678"
    scrubbed = _scrub_value(raw)
    assert "123456789:" not in scrubbed
    assert _REDACTED in scrubbed


def test_scrub_google_ya29_token() -> None:
    """ya29.* access tokens are scrubbed."""
    raw = "Authorization: Bearer ya29.A0ARrdaM_LongAccessTokenXXXXXXXXXXXXXXXX"
    scrubbed = _scrub_value(raw)
    assert "ya29." not in scrubbed


def test_scrub_google_refresh_token() -> None:
    """1//... refresh tokens are scrubbed."""
    raw = "refresh=1//0gLong-refresh-token-value-padded-to-forty-characters"
    scrubbed = _scrub_value(raw)
    assert "1//" not in scrubbed


def test_short_string_passthrough() -> None:
    """Short strings that don't match any pattern are passed through."""
    assert _scrub_value("hello") == "hello"
    assert _scrub_value("ok") == "ok"


def test_non_string_passthrough() -> None:
    """Integers, booleans and None are never modified."""
    assert _scrub_value(42) == 42
    assert _scrub_value(True) is True
    assert _scrub_value(None) is None


def test_nested_dict_scrubbed() -> None:
    """Sensitive keys inside nested dicts are redacted."""
    data = {
        "token": "ya29.AAAA_real_token",
        "user": {"api_key": "somekey123456789012345678901234567890"},
        "count": 5,
    }
    result = _scrub_value(data)
    assert result["token"] == _REDACTED
    assert result["user"]["api_key"] == _REDACTED
    assert result["count"] == 5


def test_list_scrubbed() -> None:
    """Lists are scrubbed element-by-element."""
    data = ["short", "ya29.A0ARrdaM_LongAccessTokenXXXXXXXXXXXXXXXX"]
    result = _scrub_value(data)
    assert result[0] == "short"
    assert "ya29." not in result[1]


# ── _scrubbing_processor ───────────────────────────────────────────────────────


def _make_event(**kwargs: object) -> dict:
    return {"event": "test_event", **kwargs}


def test_processor_redacts_token_key() -> None:
    """The 'token' key is always redacted regardless of value length."""
    event = _make_event(token="short")
    result = _scrubbing_processor(None, "info", event)
    assert result["token"] == _REDACTED


def test_processor_redacts_refresh_token_key() -> None:
    event = _make_event(refresh_token="1//some-refresh-token-value-padded")
    result = _scrubbing_processor(None, "info", event)
    assert result["refresh_token"] == _REDACTED


def test_processor_passes_safe_keys() -> None:
    event = _make_event(record_id="01J_ULID_VALUE", category="vehicle", count=3)
    result = _scrubbing_processor(None, "info", event)
    assert result["record_id"] == "01J_ULID_VALUE"
    assert result["category"] == "vehicle"
    assert result["count"] == 3


def test_processor_redacts_drive_file_id() -> None:
    """drive_file_id must never appear in logs."""
    event = _make_event(drive_file_id="1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgVE2upms")
    result = _scrubbing_processor(None, "info", event)
    assert result["drive_file_id"] == _REDACTED


def test_processor_redacts_file_name() -> None:
    """file_name and original_name must never appear in logs."""
    event = _make_event(file_name="invoice_june.pdf", original_name="IMG_4321.jpg")
    result = _scrubbing_processor(None, "info", event)
    assert result["file_name"] == _REDACTED
    assert result["original_name"] == _REDACTED


def test_processor_redacts_document_text() -> None:
    """Extracted document text must never appear in logs."""
    event = _make_event(document_text="Vehicle No: MH12AB1234, Expiry: 2027-03-01")
    result = _scrubbing_processor(None, "info", event)
    assert result["document_text"] == _REDACTED
