"""Typed configuration loader.

Reads environment variables (from a .env file or the real environment) and
exposes them as a frozen ``Settings`` dataclass.  Every external call –
Telegram, Google, Gemini, backup targets – is driven by a value from here,
not from any hard-coded literal anywhere else in the codebase.

Values marked DEFAULT in the blueprint are given sensible defaults; they can
all be overridden via the environment without touching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from dotenv import load_dotenv

# Load a .env file if present (dev convenience; production uses real env vars)
load_dotenv()


def _require(key: str) -> str:
    """Return env var *key* or raise a clear error at startup."""
    value = os.environ.get(key, "").strip()
    if not value:
        raise ValueError(
            f"Required environment variable '{key}' is not set. "
            "Check .env.example for the full list."
        )
    return value


def _opt(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


def _int(key: str, default: int) -> int:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ValueError(f"Environment variable '{key}' must be an integer, got: {raw!r}")


def _float(key: str, default: float) -> float:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        raise ValueError(f"Environment variable '{key}' must be a float, got: {raw!r}")


def _admin_ids(key: str) -> frozenset[int]:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return frozenset()
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    try:
        return frozenset(int(p) for p in parts)
    except ValueError:
        raise ValueError(f"'{key}' must be comma-separated integers, got: {raw!r}")


def _fallback_chain(key: str) -> tuple[str, ...]:
    raw = _opt(key)
    if not raw:
        return ()
    return tuple(m.strip() for m in raw.split(",") if m.strip())


# ── Main settings dataclass ────────────────────────────────────────────────────

@dataclass(frozen=True)
class Settings:
    """All runtime configuration for SQUARES Vault.

    Instantiate once at startup via :func:`load_settings` and pass the
    resulting object around.  Do **not** call ``os.environ`` anywhere else.
    """

    # Telegram
    telegram_bot_token: str
    admin_user_ids: frozenset[int]

    # Google OAuth (device flow)
    google_client_id: str
    google_client_secret: str

    # Gemini AI
    gemini_api_key: str
    ai_model_name: str
    ai_fallback_chain: tuple[str, ...]
    ai_per_user_daily_cap: int
    ai_global_daily_quota: int
    ai_global_ceiling_pct: float  # 0–100

    # Database
    db_path: Path

    # Security
    master_key_path: Path
    tmp_dir: Path

    # Backups – Cloudflare R2 (primary)
    backup_r2_endpoint: str
    backup_r2_bucket: str
    backup_r2_access_key: str
    backup_r2_secret_key: str

    # Backups – Backblaze B2 (secondary)
    backup_b2_endpoint: str
    backup_b2_bucket: str
    backup_b2_access_key: str
    backup_b2_secret_key: str

    # age public key for encrypted backups
    backup_age_recipient: str

    # Operations
    uptime_ping_url: str  # empty string = disabled

    # App settings
    max_users: int
    policy_version: str
    default_tz: str
    default_remind_time: str  # HH:MM local


def load_settings() -> Settings:
    """Load and validate all configuration from the environment.

    Call once at process start; store the result in a module-level singleton
    (see :data:`settings`).

    Raises
    ------
    ValueError
        If any *required* variable is missing or any value is malformed.
    """
    return Settings(
        # Telegram
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        admin_user_ids=_admin_ids("ADMIN_USER_IDS"),
        # Google
        google_client_id=_require("GOOGLE_CLIENT_ID"),
        google_client_secret=_require("GOOGLE_CLIENT_SECRET"),
        # Gemini
        gemini_api_key=_require("GEMINI_API_KEY"),
        ai_model_name=_opt("AI_MODEL_NAME", "gemini-2.0-flash-lite"),
        ai_fallback_chain=_fallback_chain("AI_FALLBACK_CHAIN"),
        ai_per_user_daily_cap=_int("AI_PER_USER_DAILY_CAP", 3),
        ai_global_daily_quota=_int("AI_GLOBAL_DAILY_QUOTA", 500),
        ai_global_ceiling_pct=_float("AI_GLOBAL_CEILING_PCT", 80.0),
        # DB
        db_path=Path(_opt("DB_PATH", "/var/lib/squares-vault/db/squares.sqlite3")),
        # Security
        master_key_path=Path(_opt("MASTER_KEY_PATH", "/etc/squares-vault/master.key")),
        tmp_dir=Path(_opt("TMP_DIR", "/var/lib/squares-vault/tmp")),
        # R2
        backup_r2_endpoint=_opt("BACKUP_R2_ENDPOINT"),
        backup_r2_bucket=_opt("BACKUP_R2_BUCKET"),
        backup_r2_access_key=_opt("BACKUP_R2_ACCESS_KEY"),
        backup_r2_secret_key=_opt("BACKUP_R2_SECRET_KEY"),
        # B2
        backup_b2_endpoint=_opt("BACKUP_B2_ENDPOINT"),
        backup_b2_bucket=_opt("BACKUP_B2_BUCKET"),
        backup_b2_access_key=_opt("BACKUP_B2_ACCESS_KEY"),
        backup_b2_secret_key=_opt("BACKUP_B2_SECRET_KEY"),
        # age
        backup_age_recipient=_opt("BACKUP_AGE_RECIPIENT"),
        # Ops
        uptime_ping_url=_opt("UPTIME_PING_URL"),
        # App
        max_users=_int("MAX_USERS", 100),
        policy_version=_opt("POLICY_VERSION", "1.0"),
        default_tz=_opt("DEFAULT_TZ", "Asia/Kolkata"),
        default_remind_time=_opt("DEFAULT_REMIND_TIME", "09:00"),
    )


# Module-level lazy singleton – import and use ``settings`` from other modules.
# It is populated by calling ``init_settings()`` at bot startup.
settings: Settings  # forward declaration (set by init_settings)


def init_settings() -> Settings:
    """Initialise the module-level :data:`settings` singleton."""
    global settings  # noqa: PLW0603
    settings = load_settings()
    return settings
