"""Migration 0001 – initial schema.

Implements the complete data model from blueprint Section 5.
Run via the migration runner (``db/migrations/__init__.py``).
"""

from __future__ import annotations

SCHEMA_SQL = """\
-- ─────────────────────────────────────────────────────────────────────────────
-- Users and authentication
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY,   -- Telegram user id
    chat_id             INTEGER NOT NULL,
    status              TEXT    NOT NULL DEFAULT 'active',
        -- active | paused | needs_reconnect | deleting | blocked
    tz                  TEXT    NOT NULL DEFAULT 'Asia/Kolkata',
    remind_time         TEXT    NOT NULL DEFAULT '09:00',   -- HH:MM local
    quiet_start         TEXT,   -- HH:MM local, optional
    quiet_end           TEXT,   -- HH:MM local, optional
    ai_enabled          INTEGER NOT NULL DEFAULT 1,
    weekly_digest       INTEGER NOT NULL DEFAULT 0,
    pin_hash            TEXT,
    pin_fail_count      INTEGER DEFAULT 0,
    pin_locked_until    TEXT,   -- UTC ISO-8601, null = not locked
    locale              TEXT    DEFAULT 'en',
    age_confirmed_at    TEXT,   -- UTC ISO-8601
    consent_version     TEXT,
    consent_at          TEXT,   -- UTC ISO-8601
    invite_id           INTEGER,
    created_at          TEXT,
    last_active_at      TEXT
);

CREATE TABLE IF NOT EXISTS invite_codes (
    id          INTEGER PRIMARY KEY,
    code_hash   TEXT    UNIQUE NOT NULL,   -- SHA-256 hex of the raw invite code
    created_at  TEXT,
    used_by     INTEGER,   -- user id
    used_at     TEXT,
    revoked     INTEGER DEFAULT 0
);

-- ─────────────────────────────────────────────────────────────────────────────
-- OAuth tokens (envelope-encrypted, S1)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS oauth_tokens (
    user_id             INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    refresh_token_enc   BLOB    NOT NULL,   -- AES-GCM ciphertext blob
    dek_wrapped         BLOB    NOT NULL,   -- wrapped DEK blob
    scopes              TEXT,
    status              TEXT    DEFAULT 'ok',
        -- ok | needs_reconnect
    last_refreshed_at   TEXT,
    last_touch_at       TEXT,
    root_folder_id      TEXT,   -- Drive folder id (not logged)
    manifest_file_id    TEXT    -- appDataFolder file id (not logged)
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Optional user-supplied Gemini key (BYOK, A3/D11)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_keys (
    user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    api_key_enc BLOB    NOT NULL,
    dek_wrapped BLOB    NOT NULL,
    created_at  TEXT
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Entities: vehicles, people, products, properties
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS entities (
    id                  TEXT    PRIMARY KEY,   -- ULID
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind                TEXT    NOT NULL,
        -- vehicle | person | product | property | other
    display_name        TEXT    NOT NULL,
    identifier_masked   TEXT,   -- e.g. MH12••••34
    aliases             TEXT,   -- JSON array of strings
    created_at          TEXT
);

CREATE INDEX IF NOT EXISTS idx_entities_user ON entities(user_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Records: the core document/event records
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS records (
    id                  TEXT    PRIMARY KEY,   -- ULID
    user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    category            TEXT    NOT NULL,
        -- vehicle | warranty | occasion | bill | insurance | id | health | home | custom
    subtype             TEXT,
    title               TEXT    NOT NULL,
    entity_id           TEXT    REFERENCES entities(id),
    key_date            TEXT,   -- YYYY-MM-DD
    key_date_type       TEXT,   -- expiry | due | event | renewal | end | null
    recurrence          TEXT,   -- none | yearly | monthly | custom:<rule>
    amount_minor        INTEGER,            -- integer minor units (paise)
    currency            TEXT    DEFAULT 'INR',
    notes               TEXT,
    tags                TEXT,   -- JSON array of strings
    extra_json          TEXT,   -- JSON object; category-specific extras
    status              TEXT    DEFAULT 'active',
        -- active | done | renewed | lapsed | soft_deleted
    parent_record_id    TEXT,   -- for renewal chains
    reminder_override   TEXT,   -- JSON list of day offsets, null = use default
    code                TEXT    UNIQUE NOT NULL,   -- 10-char random base32
    created_at          TEXT,
    updated_at          TEXT,
    deleted_at          TEXT    -- set on soft-delete; NULL otherwise
);

CREATE INDEX IF NOT EXISTS idx_records_user     ON records(user_id);
CREATE INDEX IF NOT EXISTS idx_records_key_date ON records(user_id, key_date);
CREATE INDEX IF NOT EXISTS idx_records_status   ON records(user_id, status);
CREATE INDEX IF NOT EXISTS idx_records_code     ON records(code);

-- ─────────────────────────────────────────────────────────────────────────────
-- Files: one or more Drive files per record
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS files (
    id              TEXT    PRIMARY KEY,   -- ULID
    record_id       TEXT    NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    user_id         INTEGER NOT NULL,
    drive_file_id   TEXT    NOT NULL,   -- Google Drive file ID (never logged)
    drive_folder_id TEXT,
    coded_name      TEXT    NOT NULL,   -- SV-<code>-<n>.<ext>
    mime            TEXT,
    size_bytes      INTEGER,
    sha256          TEXT,               -- hex digest of the original bytes
    page_no         INTEGER DEFAULT 1,
    created_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_files_record ON files(record_id);
CREATE INDEX IF NOT EXISTS idx_files_user   ON files(user_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Reminders
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS reminders (
    id              TEXT    PRIMARY KEY,   -- ULID
    record_id       TEXT    NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    user_id         INTEGER NOT NULL,
    kind            TEXT    NOT NULL,   -- pre | due | overdue | digest
    offset_days     INTEGER,            -- days relative to key_date (signed)
    fire_at         TEXT    NOT NULL,   -- UTC ISO-8601 datetime
    state           TEXT    NOT NULL DEFAULT 'pending',
        -- pending | sending | sent | snoozed | done | cancelled | failed
    snoozed_until   TEXT,
    attempts        INTEGER DEFAULT 0,
    last_error      TEXT,
    sent_message_id INTEGER             -- Telegram message id if sent
);

CREATE INDEX IF NOT EXISTS idx_rem_due      ON reminders(state, fire_at);
CREATE INDEX IF NOT EXISTS idx_rem_record   ON reminders(record_id);
CREATE INDEX IF NOT EXISTS idx_rem_user     ON reminders(user_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- Conversation state machine
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_state (
    user_id     INTEGER PRIMARY KEY,
    state       TEXT,       -- e.g. IDLE, ADD_AWAIT_FILE, DRAFT_REVIEW, …
    data_json   TEXT,       -- JSON; flow-specific context
    expires_at  TEXT        -- UTC ISO-8601; expired states are ignored
);

-- ─────────────────────────────────────────────────────────────────────────────
-- AI pipeline
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS drafts (
    id          TEXT    PRIMARY KEY,   -- ULID
    user_id     INTEGER NOT NULL,
    state       TEXT,
        -- reading | review | editing | saved | discarded
    temp_paths  TEXT,   -- JSON array of temp file paths (deleted after use)
    ai_json     TEXT,   -- raw AI output JSON
    edited_json TEXT,   -- user-edited version
    created_at  TEXT,
    expires_at  TEXT    -- UTC ISO-8601; drafts expire after 30 minutes
);

CREATE TABLE IF NOT EXISTS ai_queue (
    id          TEXT    PRIMARY KEY,   -- ULID
    user_id     INTEGER NOT NULL,
    draft_id    TEXT    NOT NULL,
    state       TEXT    DEFAULT 'queued',
        -- queued | processing | done | failed | deferred
    attempts    INTEGER DEFAULT 0,
    next_try_at TEXT,   -- UTC ISO-8601
    last_error  TEXT
);

CREATE INDEX IF NOT EXISTS idx_ai_queue_state ON ai_queue(state, next_try_at);

-- Per-user and global AI usage counters (keyed by IST calendar day YYYY-MM-DD)
CREATE TABLE IF NOT EXISTS ai_usage (
    user_id INTEGER,
    day     TEXT,
    count   INTEGER,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS ai_global (
    day     TEXT PRIMARY KEY,
    count   INTEGER
);

-- ─────────────────────────────────────────────────────────────────────────────
-- Drive manifest outbox (async sync with retries)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS manifest_outbox (
    id          INTEGER PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    op          TEXT    NOT NULL,   -- upsert_record | delete_record | rebuild | …
    payload     TEXT,               -- JSON
    attempts    INTEGER DEFAULT 0,
    next_try_at TEXT                -- UTC ISO-8601; null = ready immediately
);

CREATE INDEX IF NOT EXISTS idx_outbox_ready ON manifest_outbox(next_try_at, attempts);

-- ─────────────────────────────────────────────────────────────────────────────
-- Privacy and compliance
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS consent_log (
    id              INTEGER PRIMARY KEY,
    user_id         INTEGER,
    policy_version  TEXT,
    action          TEXT,   -- agreed | declined | withdrawn
    ts              TEXT    -- UTC ISO-8601
);

-- Audit log for every Drive access (S9, LOCKED)
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY,
    ts      TEXT    NOT NULL,   -- UTC ISO-8601
    user_id INTEGER NOT NULL,
    action  TEXT    NOT NULL,   -- upload | fetch | delete | list | manifest_read | …
    target  TEXT,               -- drive_file_id – logged here only; not elsewhere
    result  TEXT                -- ok | error:<code>
);

CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_log(user_id, ts);

-- Tombstones: kept ≥30 days so deletions can be replayed after restore (S11)
CREATE TABLE IF NOT EXISTS tombstones (
    user_hash   TEXT PRIMARY KEY,   -- salted SHA-256 hex of user id + deletion ts
    deleted_at  TEXT NOT NULL
);

-- ─────────────────────────────────────────────────────────────────────────────
-- FTS search indexes (Q1 – LOCKED)
-- ─────────────────────────────────────────────────────────────────────────────

-- Primary unicode FTS (word-level, diacritic-insensitive)
CREATE VIRTUAL TABLE IF NOT EXISTS fts_records
    USING fts5(
        record_id   UNINDEXED,
        user_id     UNINDEXED,
        text,
        tokenize='unicode61 remove_diacritics 2'
    );

-- Trigram FTS for typo-tolerant search
CREATE VIRTUAL TABLE IF NOT EXISTS fts_trigram
    USING fts5(
        record_id   UNINDEXED,
        user_id     UNINDEXED,
        text,
        tokenize='trigram'
    );

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration tracking
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);
"""

VERSION: int = 1


async def up(conn: "aiosqlite.Connection") -> None:  # type: ignore[name-defined]  # noqa: F821
    """Apply migration 0001: create all tables and indexes."""
    await conn.executescript(SCHEMA_SQL)
