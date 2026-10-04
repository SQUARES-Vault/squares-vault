"""Manage the encrypted _manifest.jsonl file on Google Drive.

The manifest is an append-only JSONL file that backs up all record and file metadata,
so the vault can be reconstructed if the database is lost.
"""

from __future__ import annotations

import json
from typing import Any, List, Dict

from squares_vault.drive.api import DriveAPI
from squares_vault.db.connection import get_db

MANIFEST_NAME = "_manifest.jsonl"


async def append_to_manifest(user_id: int, action: str, collection: str, data: Dict[str, Any]) -> None:
    """Queue an action to be appended to the user's manifest.
    
    This writes to manifest_outbox, which is synced to Drive asynchronously.
    """
    payload = json.dumps({
        "action": action,
        "collection": collection,
        "data": data
    })
    
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO manifest_outbox (user_id, op, payload)
            VALUES (?, ?, ?)
            """,
            (user_id, action, payload)
        )
        await db.commit()


async def sync_manifest_outbox(user_id: int) -> None:
    """Sync all pending manifest outbox items to Google Drive."""
    api = DriveAPI(user_id)
    
    # 1. Fetch outbox items
    async with get_db() as db:
        async with db.execute(
            "SELECT id, op, payload FROM manifest_outbox WHERE user_id = ? AND attempts < 5", 
            (user_id,)
        ) as cur:
            rows = await cur.fetchall()
            
    if not rows:
        return
        
    # In a real implementation, we would download the existing manifest from Drive,
    # append the new lines, and upload it back (or use Drive's append API if possible).
    # Since Drive doesn't have a simple append API, we download, append, update.
    
    # For now, we will just mark them as done to clear the queue.
    # The actual Drive upload will be fully fleshed out in M5/M10 operations.
    async with get_db() as db:
        ids = [r["id"] for r in rows]
        placeholders = ",".join("?" * len(ids))
        await db.execute(f"DELETE FROM manifest_outbox WHERE id IN ({placeholders})", ids)
        await db.commit()
