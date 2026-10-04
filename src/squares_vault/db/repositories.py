"""Database repositories for entities, records, and files."""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional
import ulid

from squares_vault.db.connection import get_db


async def create_record(user_id: int, category: str, title: str, code: str, **kwargs: Any) -> str:
    """Create a new record and return its ULID."""
    record_id = str(ulid.ULID())
    
    # We serialize extra fields into extra_json
    extra_json = json.dumps(kwargs.get("extra_json", {})) if "extra_json" in kwargs else None
    
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, amount_minor, currency, notes, tags, extra_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                record_id, user_id, category, title, code,
                kwargs.get("amount_minor"), kwargs.get("currency", "INR"),
                kwargs.get("notes"), kwargs.get("tags"), extra_json
            )
        )
        await db.commit()
        
    return record_id


async def add_file(record_id: str, user_id: int, drive_file_id: str, coded_name: str, mime: str, size: int) -> str:
    """Attach a file to a record."""
    file_id = str(ulid.ULID())
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO files (id, record_id, user_id, drive_file_id, coded_name, mime, size_bytes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (file_id, record_id, user_id, drive_file_id, coded_name, mime, size)
        )
        await db.commit()
    return file_id


async def soft_delete_record(user_id: int, record_id: str) -> None:
    """Mark a record as soft_deleted."""
    async with get_db() as db:
        await db.execute(
            """
            UPDATE records SET status = 'soft_deleted', deleted_at = CURRENT_TIMESTAMP
            WHERE id = ? AND user_id = ?
            """,
            (record_id, user_id)
        )
        await db.commit()
