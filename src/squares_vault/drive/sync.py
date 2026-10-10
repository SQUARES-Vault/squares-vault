"""Sync operations between local SQLite DB and Drive manifest."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from squares_vault.db.connection import get_db
from squares_vault.logging_setup import get_logger

log = get_logger(__name__)


async def rebuild_from_manifest(user_id: int, manifest_lines: list[str]) -> int:
    """Wipe all user records and rebuild them from manifest lines.
    
    Returns the count of active records successfully restored.
    """
    from squares_vault.reminders.schedule import generate_reminders
    from squares_vault.search.index import sync_fts_for_record

    restored_record_ids: set[str] = set()

    async with get_db() as db:
        # Wipe existing records for this user (cascades to files and reminders)
        await db.execute("DELETE FROM records WHERE user_id = ?", (user_id,))
        await db.commit()

        for line in manifest_lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except Exception:
                continue

            action = item.get("action", "").upper()
            col = item.get("collection", "")
            data = item.get("data", {})

            if col == "records":
                rid = data.get("id")
                if action in ("INSERT", "UPSERT"):
                    # Fill default parameters to prevent missing key errors
                    params = {
                        "id": rid,
                        "user_id": data.get("user_id", user_id),
                        "category": data.get("category", "other"),
                        "title": data.get("title", "Untitled Document"),
                        "code": data.get("code") or f"SQ-{rid[:8]}",
                        "amount_minor": data.get("amount_minor"),
                        "currency": data.get("currency", "INR"),
                        "notes": data.get("notes"),
                        "tags": data.get("tags"),
                        "extra_json": data.get("extra_json"),
                        "key_date": data.get("key_date"),
                        "key_date_type": data.get("key_date_type"),
                        "parent_record_id": data.get("parent_record_id"),
                        "status": data.get("status", "active"),
                        "created_at": data.get("created_at") or datetime.now(timezone.utc).isoformat(),
                        "updated_at": data.get("updated_at") or datetime.now(timezone.utc).isoformat(),
                    }
                    await db.execute(
                        """
                        INSERT OR REPLACE INTO records (
                            id, user_id, category, title, code, amount_minor, currency, 
                            notes, tags, extra_json, key_date, key_date_type, 
                            parent_record_id, status, created_at, updated_at
                        ) VALUES (
                            :id, :user_id, :category, :title, :code, :amount_minor, :currency, 
                            :notes, :tags, :extra_json, :key_date, :key_date_type, 
                            :parent_record_id, :status, :created_at, :updated_at
                        )
                        """,
                        params
                    )
                    if params["status"] == "active":
                        restored_record_ids.add(rid)
                    else:
                        restored_record_ids.discard(rid)

                elif action == "UPDATE":
                    await db.execute(
                        """
                        UPDATE records SET
                            title = COALESCE(:title, title),
                            category = COALESCE(:category, category),
                            amount_minor = :amount_minor,
                            currency = COALESCE(:currency, currency),
                            key_date = :key_date,
                            key_date_type = :key_date_type,
                            notes = :notes,
                            tags = :tags,
                            updated_at = datetime('now')
                        WHERE id = :id AND user_id = :user_id
                        """,
                        {
                            "id": rid,
                            "user_id": user_id,
                            "title": data.get("title"),
                            "category": data.get("category"),
                            "amount_minor": data.get("amount_minor"),
                            "currency": data.get("currency"),
                            "key_date": data.get("key_date"),
                            "key_date_type": data.get("key_date_type"),
                            "notes": data.get("notes"),
                            "tags": data.get("tags"),
                        }
                    )

                elif action == "DELETE":
                    await db.execute(
                        "UPDATE records SET status = 'soft_deleted', deleted_at = datetime('now') WHERE id = ? AND user_id = ?",
                        (rid, user_id)
                    )
                    restored_record_ids.discard(rid)

            elif col == "files":
                fid = data.get("id")
                rid = data.get("record_id")
                if action in ("INSERT", "UPSERT") and rid:
                    params = {
                        "id": fid,
                        "record_id": rid,
                        "user_id": data.get("user_id", user_id),
                        "drive_file_id": data.get("drive_file_id", ""),
                        "coded_name": data.get("coded_name", "file"),
                        "mime": data.get("mime", "application/octet-stream"),
                        "size_bytes": data.get("size_bytes", 0),
                        "created_at": data.get("created_at") or datetime.now(timezone.utc).isoformat(),
                    }
                    await db.execute(
                        """
                        INSERT OR REPLACE INTO files (id, record_id, user_id, drive_file_id, coded_name, mime, size_bytes, created_at)
                        VALUES (:id, :record_id, :user_id, :drive_file_id, :coded_name, :mime, :size_bytes, :created_at)
                        """,
                        params
                    )
        await db.commit()

        # Re-index FTS and regenerate active reminders
        now_iso = datetime.now(timezone.utc).isoformat()
        for rid in restored_record_ids:
            async with db.execute("SELECT * FROM records WHERE id = ?", (rid,)) as cur:
                r_row = await cur.fetchone()
                if not r_row:
                    continue

            # Resync FTS index
            await sync_fts_for_record(
                r_row["id"], user_id, r_row["title"], r_row["category"], r_row["tags"], r_row["notes"]
            )

            # Recreate reminders if record has key_date
            if r_row["key_date"]:
                try:
                    kd_str = r_row["key_date"][:10]
                    dt_obj = datetime.strptime(kd_str, "%Y-%m-%d")
                    reminders = generate_reminders(rid, user_id, r_row["category"], dt_obj)
                    for rm in reminders:
                        st = "skipped" if rm["fire_at"] < now_iso else "pending"
                        await db.execute(
                            """
                            INSERT OR IGNORE INTO reminders (id, record_id, user_id, kind, offset_days, fire_at, state)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (rm["id"], rm["record_id"], rm["user_id"], rm["kind"], rm["offset_days"], rm["fire_at"], st)
                        )
                except Exception as e:
                    log.warning("rebuild_reminders_skipped", record_id=rid, error=str(e))
        await db.commit()

    return len(restored_record_ids)


async def rebuild_user_from_drive(user_id: int) -> int:
    """Download _manifest.jsonl from Google Drive and rebuild the user's vault."""
    from squares_vault.drive.api import DriveAPI
    from squares_vault.drive.manifest import MANIFEST_NAME
    from squares_vault.drive.token_store import get_token

    tokens = await get_token(user_id)
    if not tokens:
        raise ValueError(f"User {user_id} does not have Google Drive connected.")

    api = DriveAPI(user_id)
    folder_id = await api.get_app_folder()

    res = await api._request(
        "GET",
        "files",
        params={
            "q": f"'{folder_id}' in parents and name='{MANIFEST_NAME}' and trashed=false",
            "fields": "files(id, name)"
        }
    )
    files = res.get("files", [])
    if not files:
        log.info("no_manifest_found_on_drive", user_id=user_id)
        return 0

    manifest_file_id = files[0]["id"]
    content_bytes = await api.download_file(manifest_file_id)
    lines = content_bytes.decode("utf-8", errors="replace").splitlines()

    return await rebuild_from_manifest(user_id, lines)
