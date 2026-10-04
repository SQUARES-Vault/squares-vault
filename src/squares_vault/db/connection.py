"""Async SQLite connection factory with WAL mode and foreign keys.

Every module that needs the database imports ``get_db`` and uses it as an
async context manager::

    async with get_db() as db:
        await db.execute("SELECT 1")

Design notes
------------
- WAL mode is set on every new connection (idempotent).
- Foreign keys are enforced (``PRAGMA foreign_keys = ON``).
- A 5-second busy timeout prevents ``SQLITE_BUSY`` from cascading.
- Journal mode and synchronous setting are tuned for reliability over speed
  (``PRAGMA synchronous = NORMAL`` is a good balance with WAL).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite

from squares_vault.config import settings

# Module-level connection pool (single connection reused across coroutines
# via asyncio serialization; aiosqlite is not truly pooled, but one shared
# connection is fine for SQLite).
_connection: aiosqlite.Connection | None = None
_lock = asyncio.Lock()


async def _open_connection(db_path: Path) -> aiosqlite.Connection:
    """Open and configure a new aiosqlite connection."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA busy_timeout = 5000")   # ms
    await conn.execute("PRAGMA synchronous = NORMAL")
    await conn.execute("PRAGMA temp_store = MEMORY")
    return conn


async def init_db() -> None:
    """Open the module-level connection.  Call once at startup."""
    global _connection  # noqa: PLW0603
    async with _lock:
        if _connection is None:
            _connection = await _open_connection(settings.db_path)


async def close_db() -> None:
    """Close the module-level connection.  Call at shutdown."""
    global _connection  # noqa: PLW0603
    async with _lock:
        if _connection is not None:
            await _connection.close()
            _connection = None


@asynccontextmanager
async def get_db() -> AsyncGenerator[aiosqlite.Connection, None]:
    """Yield the shared database connection.

    If the connection has not been opened yet (e.g. in tests), it is opened
    lazily using ``settings.db_path``.
    """
    global _connection  # noqa: PLW0603
    if _connection is None:
        await init_db()
    assert _connection is not None  # noqa: S101 – guaranteed by init_db
    yield _connection
