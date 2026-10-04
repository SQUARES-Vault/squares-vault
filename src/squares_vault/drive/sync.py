"""Sync operations between local SQLite DB and Drive manifest."""

from __future__ import annotations

import json
from squares_vault.db.connection import get_db

async def rebuild_from_manifest(user_id: int, manifest_lines: list[str]) -> None:
    """Wipe all user records and rebuild them from a downloaded manifest."""
    async with get_db() as db:
        # Wipe existing
        await db.execute("DELETE FROM records WHERE user_id = ?", (user_id,))
        # cascades to files and reminders
        
        for line in manifest_lines:
            if not line.strip():
                continue
            item = json.loads(line)
            action = item["action"]
            col = item["collection"]
            data = item["data"]
            
            if action == "INSERT":
                if col == "records":
                    await db.execute(
                        """
                        INSERT INTO records (id, user_id, category, title, code, amount_minor, currency, notes, tags, extra_json, status, created_at, updated_at)
                        VALUES (:id, :user_id, :category, :title, :code, :amount_minor, :currency, :notes, :tags, :extra_json, :status, :created_at, :updated_at)
                        """,
                        data
                    )
                elif col == "files":
                    await db.execute(
                        """
                        INSERT INTO files (id, record_id, user_id, drive_file_id, coded_name, mime, size_bytes, created_at)
                        VALUES (:id, :record_id, :user_id, :drive_file_id, :coded_name, :mime, :size_bytes, :created_at)
                        """,
                        data
                    )
        await db.commit()
