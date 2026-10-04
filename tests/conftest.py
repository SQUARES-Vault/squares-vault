"""Pytest configuration and shared fixtures for SQUARES Vault tests."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _set_test_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Set required environment variables for all tests.

    This ensures that importing ``squares_vault.config`` in tests does not
    blow up because the required variables are missing.  Tests that need
    specific values can override individual keys with ``monkeypatch.setenv``.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA")
    monkeypatch.setenv("ADMIN_USER_IDS", "999999999")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-client-secret-value-for-tests")
    monkeypatch.setenv("GEMINI_API_KEY", "AIzaSyTESTKEYFORUNITTESTSONLYNOTREAL12345")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.sqlite3"))
    monkeypatch.setenv("MASTER_KEY_PATH", str(tmp_path / "master.key"))
    monkeypatch.setenv("TMP_DIR", str(tmp_path / "tmp"))

    from squares_vault.config import init_settings
    init_settings()


@pytest.fixture(autouse=True)
async def setup_db(tmp_path):
    from squares_vault.db.connection import get_db
    from squares_vault.db.migrations import run_migrations
    
    async with get_db() as db:
        await run_migrations(db)
