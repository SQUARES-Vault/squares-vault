"""Integration tests for DB migrations.

Acceptance criteria (M0):
- run_migrations on an empty DB applies all migrations without error.
- Re-running run_migrations is idempotent (no error, count = 0).
- All expected tables exist after migration.
- Both FTS virtual tables (fts_records, fts_trigram) exist.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio


async def _open(path: Path) -> aiosqlite.Connection:
    conn = await aiosqlite.connect(path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA foreign_keys = ON")
    return conn


@pytest.fixture()
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "test.sqlite3"


@pytest.mark.asyncio()
async def test_migrations_apply_cleanly(tmp_db: Path) -> None:
    """run_migrations on a fresh DB succeeds and returns count ≥ 1."""
    from squares_vault.db.migrations import run_migrations

    conn = await _open(tmp_db)
    try:
        count = await run_migrations(conn)
        assert count >= 1, "Expected at least one migration to be applied"
    finally:
        await conn.close()


@pytest.mark.asyncio()
async def test_migrations_idempotent(tmp_db: Path) -> None:
    """Running migrations twice is safe; second run returns 0."""
    from squares_vault.db.migrations import run_migrations

    conn = await _open(tmp_db)
    try:
        await run_migrations(conn)
        count2 = await run_migrations(conn)
        assert count2 == 0
    finally:
        await conn.close()


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "table",
    [
        "users",
        "invite_codes",
        "oauth_tokens",
        "user_keys",
        "entities",
        "records",
        "files",
        "reminders",
        "user_state",
        "drafts",
        "ai_queue",
        "ai_usage",
        "ai_global",
        "manifest_outbox",
        "consent_log",
        "audit_log",
        "tombstones",
        "schema_migrations",
    ],
)
async def test_table_exists(tmp_db: Path, table: str) -> None:
    """Every table from blueprint Section 5 must exist after migration."""
    from squares_vault.db.migrations import run_migrations

    conn = await _open(tmp_db)
    try:
        await run_migrations(conn)
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ) as cur:
            row = await cur.fetchone()
        assert row is not None, f"Table '{table}' not found after migration"
    finally:
        await conn.close()


@pytest.mark.asyncio()
@pytest.mark.parametrize("fts_table", ["fts_records", "fts_trigram"])
async def test_fts_tables_exist(tmp_db: Path, fts_table: str) -> None:
    """Both FTS5 virtual tables must exist after migration."""
    from squares_vault.db.migrations import run_migrations

    conn = await _open(tmp_db)
    try:
        await run_migrations(conn)
        async with conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (fts_table,)
        ) as cur:
            row = await cur.fetchone()
        assert row is not None, f"FTS table '{fts_table}' not found after migration"
    finally:
        await conn.close()


@pytest.mark.asyncio()
async def test_records_code_unique_constraint(tmp_db: Path) -> None:
    """The 'code' column on records has a UNIQUE constraint."""
    from squares_vault.db.migrations import run_migrations
    import aiosqlite as _aio

    conn = await _open(tmp_db)
    try:
        await run_migrations(conn)

        # Insert a minimal user row first (foreign key)
        import datetime as dt
        now = dt.datetime.now(dt.timezone.utc).isoformat()
        await conn.execute(
            "INSERT INTO users (id, chat_id, created_at) VALUES (1, 1, ?)", (now,)
        )
        # Insert one record
        await conn.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, created_at, updated_at)
            VALUES ('01J0000000000000000000001', 1, 'vehicle', 'PUC', 'AAAAAAAAAA', ?, ?)
            """,
            (now, now),
        )
        await conn.commit()

        # Attempt to insert another record with the same code
        with pytest.raises(Exception, match="UNIQUE"):
            await conn.execute(
                """
                INSERT INTO records (id, user_id, category, title, code, created_at, updated_at)
                VALUES ('01J0000000000000000000002', 1, 'vehicle', 'PUC2', 'AAAAAAAAAA', ?, ?)
                """,
                (now, now),
            )
            await conn.commit()
    finally:
        await conn.close()
