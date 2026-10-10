"""Unit tests for config.py – the settings loader.

Tests that:
- load_settings() succeeds when all required env vars are set.
- Missing required vars raise ValueError with the key name.
- Integer parsing works and raises on bad values.
- Admin ID parsing handles comma-separated values.
- All DEFAULT values are correct.
"""

from __future__ import annotations

import pytest

from squares_vault.config import load_settings


def test_load_settings_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_settings returns a Settings object when all required vars are set."""
    # conftest sets the required vars; just call and assert no exception
    cfg = load_settings()
    assert bool(cfg.telegram_bot_token)
    assert cfg.max_users == 100
    assert cfg.default_tz == "Asia/Kolkata"
    assert cfg.default_remind_time == "09:00"
    assert cfg.ai_per_user_daily_cap > 0
    assert cfg.ai_global_ceiling_pct == 80.0


def test_missing_telegram_token_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "")
    with pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"):
        load_settings()


def test_missing_google_client_id_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    with pytest.raises(ValueError, match="GOOGLE_CLIENT_ID"):
        load_settings()


def test_bad_integer_env_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAX_USERS", "not-a-number")
    with pytest.raises(ValueError, match="MAX_USERS"):
        load_settings()


def test_admin_ids_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_USER_IDS", "111,222,333")
    cfg = load_settings()
    assert cfg.admin_user_ids == frozenset({111, 222, 333})


def test_admin_ids_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_USER_IDS", "")
    cfg = load_settings()
    assert cfg.admin_user_ids == frozenset()


def test_fallback_chain_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_FALLBACK_CHAIN", "gemini-2.0-flash,gemini-1.5-pro")
    cfg = load_settings()
    assert cfg.ai_fallback_chain == ("gemini-2.0-flash", "gemini-1.5-pro")


def test_fallback_chain_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_FALLBACK_CHAIN", "")
    cfg = load_settings()
    assert cfg.ai_fallback_chain == ()


def test_custom_ai_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AI_PER_USER_DAILY_CAP", "5")
    cfg = load_settings()
    assert cfg.ai_per_user_daily_cap == 5


def test_settings_is_frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings must be immutable (frozen dataclass)."""
    cfg = load_settings()
    with pytest.raises((AttributeError, TypeError)):
        cfg.max_users = 999  # type: ignore[misc]


def test_gemini_api_keys_pool_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    """GEMINI_API_KEYS comma/newline separated should be parsed and deduplicated."""
    monkeypatch.delenv("AI_GLOBAL_DAILY_QUOTA", raising=False)
    monkeypatch.setenv("GEMINI_API_KEYS", "key_alpha, key_beta, key_gamma, key_alpha")
    cfg = load_settings()
    assert cfg.gemini_api_keys == ("key_alpha", "key_beta", "key_gamma")
    assert cfg.gemini_api_key == "key_alpha"
    assert cfg.ai_global_daily_quota == 1500 * 3


def test_gemini_api_key_single_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """When GEMINI_API_KEYS is missing, fallback to single GEMINI_API_KEY."""
    monkeypatch.delenv("GEMINI_API_KEYS", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "single_key_xyz")
    cfg = load_settings()
    assert cfg.gemini_api_keys == ("single_key_xyz",)
    assert cfg.gemini_api_key == "single_key_xyz"

