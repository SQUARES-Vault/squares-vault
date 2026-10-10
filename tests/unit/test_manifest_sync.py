"""Unit tests for background manifest outbox syncing and zero-data-loss rebuilds."""

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock

from squares_vault.db.connection import get_db
from squares_vault.db.repositories import create_record, add_file, soft_delete_record
from squares_vault.drive.manifest import append_to_manifest, sync_manifest_outbox, MANIFEST_NAME
from squares_vault.drive.sync import rebuild_from_manifest


@pytest.mark.asyncio
async def test_manifest_outbox_queue_on_crud(setup_db):
    """Verify repository operations automatically append events to manifest_outbox."""
    user_id = 771122
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.commit()

    # 1. Create Record
    rid = await create_record(
        user_id=user_id,
        category="vehicle",
        title="BMW Registration",
        code="SQ-BMW-001",
        key_date="2027-05-15",
        key_date_type="expiry"
    )

    # 2. Add File
    fid = await add_file(
        record_id=rid,
        user_id=user_id,
        drive_file_id="drive_file_abc",
        coded_name="SV-BMW-001-1.pdf",
        mime="application/pdf",
        size=10240
    )

    # 3. Soft Delete
    await soft_delete_record(user_id, rid)

    # Check manifest_outbox rows
    async with get_db() as db:
        async with db.execute(
            "SELECT op, payload FROM manifest_outbox WHERE user_id = ? ORDER BY id ASC", 
            (user_id,)
        ) as cur:
            rows = await cur.fetchall()

    assert len(rows) == 3
    # Check ops
    assert rows[0]["op"] == "INSERT"
    p0 = json.loads(rows[0]["payload"])
    assert p0["collection"] == "records"
    assert p0["data"]["title"] == "BMW Registration"

    assert rows[1]["op"] == "INSERT"
    p1 = json.loads(rows[1]["payload"])
    assert p1["collection"] == "files"
    assert p1["data"]["id"] == fid

    assert rows[2]["op"] == "DELETE"
    p2 = json.loads(rows[2]["payload"])
    assert p2["data"]["id"] == rid


@pytest.mark.asyncio
async def test_sync_manifest_outbox_drive_interaction(setup_db, monkeypatch):
    """Verify sync_manifest_outbox uploads/updates manifest on Drive and clears outbox."""
    user_id = 998811
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.commit()

    # Mock token store to return valid tokens
    monkeypatch.setattr(
        "squares_vault.drive.token_store.get_token", 
        AsyncMock(return_value=("fake_access_token", "fake_refresh_token"))
    )

    # Mock DriveAPI methods
    mock_upload = AsyncMock(return_value="drive_manifest_file_id_1")
    mock_update = AsyncMock()
    mock_download = AsyncMock(return_value=b'{"action":"INSERT","collection":"records","data":{"id":"old_rec"}}\n')
    mock_get_app_folder = AsyncMock(return_value="vault_root_folder_id")

    # First test: No existing manifest on Drive -> uploads new
    mock_request_no_file = AsyncMock(return_value={"files": []})
    monkeypatch.setattr("squares_vault.drive.api.DriveAPI.get_app_folder", mock_get_app_folder)
    monkeypatch.setattr("squares_vault.drive.api.DriveAPI._request", mock_request_no_file)
    monkeypatch.setattr("squares_vault.drive.api.DriveAPI.upload_file", mock_upload)
    monkeypatch.setattr("squares_vault.drive.api.DriveAPI.update_file_content", mock_update)
    monkeypatch.setattr("squares_vault.drive.api.DriveAPI.download_file", mock_download)

    # Queue an item
    await append_to_manifest(user_id, "INSERT", "records", {"id": "rec_test_1", "title": "Passport"})

    # Sync
    await sync_manifest_outbox(user_id)

    # Verify upload was called
    mock_upload.assert_called_once()
    assert mock_upload.call_args[0][0] == MANIFEST_NAME

    # Outbox must now be empty
    async with get_db() as db:
        async with db.execute("SELECT count(*) as c FROM manifest_outbox WHERE user_id = ?", (user_id,)) as cur:
            assert (await cur.fetchone())["c"] == 0

    # Second test: Existing manifest on Drive -> downloads and updates
    mock_request_with_file = AsyncMock(return_value={"files": [{"id": "existing_fid", "name": MANIFEST_NAME}]})
    monkeypatch.setattr("squares_vault.drive.api.DriveAPI._request", mock_request_with_file)

    await append_to_manifest(user_id, "UPDATE", "records", {"id": "rec_test_1", "title": "Passport Renewed"})
    await sync_manifest_outbox(user_id)

    mock_download.assert_called_once_with("existing_fid")
    mock_update.assert_called_once()
    updated_bytes = mock_update.call_args[0][2]
    assert b"Passport Renewed" in updated_bytes
    assert b"old_rec" in updated_bytes


@pytest.mark.asyncio
async def test_rebuild_from_manifest_full_restoration(setup_db):
    """Verify zero-data-loss rebuild restores records, files, reminders and FTS."""
    user_id = 334455
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.commit()

    now_utc = datetime.now(timezone.utc)
    future_date = (now_utc + timedelta(days=90)).strftime("%Y-%m-%d")

    manifest_lines = [
        json.dumps({
            "action": "INSERT",
            "collection": "records",
            "data": {
                "id": "rec_restored_1",
                "user_id": user_id,
                "category": "insurance",
                "title": "Health Insurance Policy",
                "code": "SQ-INS-001",
                "amount_minor": 1500000,
                "currency": "INR",
                "key_date": future_date,
                "key_date_type": "expiry",
                "status": "active"
            }
        }),
        json.dumps({
            "action": "INSERT",
            "collection": "files",
            "data": {
                "id": "file_restored_1",
                "record_id": "rec_restored_1",
                "user_id": user_id,
                "drive_file_id": "google_drive_file_id_xyz",
                "coded_name": "SV-INS-001-1.pdf",
                "mime": "application/pdf",
                "size_bytes": 204800
            }
        })
    ]

    restored_count = await rebuild_from_manifest(user_id, manifest_lines)
    assert restored_count == 1

    # Verify records restored
    async with get_db() as db:
        async with db.execute("SELECT * FROM records WHERE id = 'rec_restored_1'") as cur:
            rec = await cur.fetchone()
            assert rec is not None
            assert rec["title"] == "Health Insurance Policy"
            assert rec["amount_minor"] == 1500000

        # Verify files restored
        async with db.execute("SELECT * FROM files WHERE id = 'file_restored_1'") as cur:
            f = await cur.fetchone()
            assert f is not None
            assert f["drive_file_id"] == "google_drive_file_id_xyz"

        # Verify reminders automatically regenerated
        async with db.execute("SELECT count(*) as c FROM reminders WHERE record_id = 'rec_restored_1'") as cur:
            rem_count = (await cur.fetchone())["c"]
            assert rem_count > 0

        # Verify FTS index restored
        from squares_vault.search.index import search_records
        results = await search_records(user_id, "Health")
        assert len(results) >= 1
        assert results[0]["id"] == "rec_restored_1"
