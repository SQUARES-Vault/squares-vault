"""Migration runner.

Discovers and applies pending migrations in version order.  Every migration
module must define:

    VERSION: int          # unique monotonically increasing integer
    async def up(conn: aiosqlite.Connection) -> None: ...

Usage::

    from squares_vault.db.migrations import run_migrations
    async with get_db() as db:
        await run_migrations(db)
"""

from __future__ import annotations

import importlib
import pkgutil
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

from squares_vault.logging_setup import get_logger

log = get_logger(__name__)

_MIGRATIONS_PACKAGE = "squares_vault.db.migrations"
_MIGRATIONS_DIR = Path(__file__).parent


def _discover_migrations() -> list[tuple[int, ModuleType]]:
    """Return all migration modules sorted by VERSION."""
    modules: list[tuple[int, ModuleType]] = []
    for info in pkgutil.iter_modules([str(_MIGRATIONS_DIR)]):
        if info.name.startswith("_"):
            continue  # skip __init__ and private helpers
        mod = importlib.import_module(f"{_MIGRATIONS_PACKAGE}.{info.name}")
        if not hasattr(mod, "VERSION") or not hasattr(mod, "up"):
            log.warning("migration_skip", module=info.name, reason="missing VERSION or up()")
            continue
        modules.append((mod.VERSION, mod))
    return sorted(modules, key=lambda t: t[0])


async def _applied_versions(conn: "aiosqlite.Connection") -> set[int]:
    """Return the set of already-applied migration versions."""
    # Ensure the tracking table exists (bootstrapping)
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER PRIMARY KEY,
            applied_at TEXT    NOT NULL
        )
        """
    )
    await conn.commit()
    async with conn.execute("SELECT version FROM schema_migrations") as cur:
        rows = await cur.fetchall()
    return {row[0] for row in rows}


async def run_migrations(conn: "aiosqlite.Connection") -> int:
    """Apply all pending migrations in order.

    Parameters
    ----------
    conn:
        An open aiosqlite connection with WAL mode and foreign keys ON.

    Returns
    -------
    int
        Number of migrations applied in this run (0 if already up-to-date).
    """
    migrations = _discover_migrations()
    applied = await _applied_versions(conn)
    pending = [(v, mod) for v, mod in migrations if v not in applied]

    if not pending:
        log.info("migrations_up_to_date", total=len(migrations))
        return 0

    count = 0
    for version, mod in pending:
        log.info("migration_apply", version=version)
        try:
            await mod.up(conn)
            await conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
                (version, datetime.now(timezone.utc).isoformat()),
            )
            await conn.commit()
            count += 1
            log.info("migration_done", version=version)
        except Exception:
            log.exception("migration_failed", version=version)
            raise

    log.info("migrations_applied", count=count)
    return count
