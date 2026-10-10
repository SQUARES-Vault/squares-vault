"""Background worker for syncing files to Google Drive."""

from __future__ import annotations

import asyncio
from squares_vault.db.connection import get_db
from squares_vault.logging_setup import get_logger
from squares_vault.drive.api import DriveAPI

log = get_logger(__name__)

async def drive_outbox_loop(bot) -> None:
    """Sync files to Google Drive."""
    log.info("drive_outbox_started")
    while True:
        try:
            async with get_db() as db:
                # Find files that need syncing (drive_file_id is a Telegram file_id)
                # Telegram file_ids are typically > 45 chars
                async with db.execute(
                    "SELECT id, user_id, drive_file_id, coded_name, mime FROM files WHERE length(drive_file_id) > 45"
                ) as cur:
                    rows = await cur.fetchall()
                    
            for row in rows:
                try:
                    target_fid = row["drive_file_id"]
                    if target_fid.startswith("{"):
                        try:
                            import json
                            meta = json.loads(target_fid)
                            if isinstance(meta, dict) and "files" in meta and meta["files"]:
                                target_fid = meta["files"][0]["file_id"]
                            elif isinstance(meta, dict) and "file_id" in meta:
                                target_fid = meta["file_id"]
                        except Exception:
                            pass

                    # Download from Telegram
                    file_info = await bot.get_file(target_fid)
                    file_bytes = await bot.download_file(file_info.file_path)
                    raw_data = file_bytes.getvalue()
                    
                    # Detect actual mime type and file extension from file contents
                    real_mime = row["mime"] or "image/jpeg"
                    real_ext = ".jpg"
                    if raw_data[:4] == b"%PDF":
                        real_mime = "application/pdf"
                        real_ext = ".pdf"
                    elif raw_data[:8] == b"\x89PNG\r\n\x1a\n":
                        real_mime = "image/png"
                        real_ext = ".png"
                    elif raw_data[:3] == b"\xff\xd8\xff":
                        real_mime = "image/jpeg"
                        real_ext = ".jpg"
                    elif raw_data[:4] == b"RIFF" and raw_data[8:12] == b"WEBP":
                        real_mime = "image/webp"
                        real_ext = ".webp"
                    elif "pdf" in real_mime.lower():
                        real_ext = ".pdf"
                    elif "png" in real_mime.lower():
                        real_ext = ".png"

                    coded_name = row["coded_name"]
                    if "." in coded_name:
                        coded_name = coded_name.rsplit(".", 1)[0] + real_ext
                    else:
                        coded_name = f"{coded_name}{real_ext}"

                    # Upload to Google Drive
                    drive = DriveAPI(row["user_id"])
                    new_drive_id = await drive.upload_file(coded_name, real_mime, raw_data)
                    
                    # Update DB
                    async with get_db() as db:
                        await db.execute(
                            "UPDATE files SET drive_file_id = ?, coded_name = ?, mime = ?, size_bytes = ? WHERE id = ?",
                            (new_drive_id, coded_name, real_mime, len(raw_data), row["id"])
                        )
                        await db.commit()
                    log.info("synced_to_drive", file_id=row["id"], drive_id=new_drive_id)
                except Exception as e:
                    log.error("drive_outbox_failed", error=str(e), file_id=row["id"])
                    
        except asyncio.CancelledError:
            break
        except Exception as e:
            log.exception("drive_outbox_error", error=str(e))
            
        await asyncio.sleep(10)
