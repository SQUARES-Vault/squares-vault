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
    from squares_vault.drive.token_store import get_token
    from squares_vault.logging_setup import get_logger

    log = get_logger(__name__)

    tokens = await get_token(user_id)
    if not tokens:
        return

    # 1. Fetch outbox items
    async with get_db() as db:
        async with db.execute(
            "SELECT id, op, payload FROM manifest_outbox WHERE user_id = ? AND attempts < 5 ORDER BY id ASC LIMIT 50", 
            (user_id,)
        ) as cur:
            rows = await cur.fetchall()
            
    if not rows:
        return

    synced_ids = [r["id"] for r in rows]
    new_lines = [r["payload"].strip() for r in rows if r["payload"]]
    if not new_lines:
        async with get_db() as db:
            placeholders = ",".join("?" * len(synced_ids))
            await db.execute(f"DELETE FROM manifest_outbox WHERE id IN ({placeholders})", synced_ids)
            await db.commit()
        return

    chunk_bytes = ("\n".join(new_lines) + "\n").encode("utf-8")
    api = DriveAPI(user_id)

    try:
        folder_id = await api.get_app_folder()
        res = await api._request(
            "GET", 
            "files", 
            params={
                "q": f"'{folder_id}' in parents and name='{MANIFEST_NAME}' and trashed=false",
                "fields": "files(id, name)"
            }
        )
        existing_files = res.get("files", [])

        if existing_files:
            target_file_id = existing_files[0]["id"]
            try:
                existing_bytes = await api.download_file(target_file_id)
            except Exception:
                existing_bytes = b""
            combined_bytes = existing_bytes.rstrip(b"\n") + b"\n" + chunk_bytes if existing_bytes else chunk_bytes
            await api.update_file_content(target_file_id, "application/x-ndjson", combined_bytes)
        else:
            await api.upload_file(MANIFEST_NAME, "application/x-ndjson", chunk_bytes)

        # 2. Mark outbox records as successfully synced
        async with get_db() as db:
            placeholders = ",".join("?" * len(synced_ids))
            await db.execute(f"DELETE FROM manifest_outbox WHERE id IN ({placeholders})", synced_ids)
            await db.commit()

        log.info("manifest_synced", user_id=user_id, count=len(synced_ids))

    except Exception as e:
        log.warning("manifest_sync_failed", user_id=user_id, error=str(e))
        async with get_db() as db:
            placeholders = ",".join("?" * len(synced_ids))
            await db.execute(
                f"UPDATE manifest_outbox SET attempts = attempts + 1 WHERE id IN ({placeholders})",
                synced_ids
            )
            await db.commit()
