import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.types import CallbackQuery, User, Message, Chat
from aiogram.fsm.context import FSMContext

from squares_vault.bot.handlers.search import on_delete_file_confirm, on_delete_file_exec
from squares_vault.db.connection import get_db

@pytest.fixture
def dummy_user():
    return User(id=88003, is_bot=False, first_name="Shikhar")

@pytest.fixture
def dummy_chat():
    return Chat(id=88003, type="private")

@pytest.mark.asyncio
async def test_delete_individual_file_removes_from_drive_and_db(dummy_user, dummy_chat):
    """Deleting an individual file must call DriveAPI.delete_file and remove only that file row from DB."""
    user_id = dummy_user.id
    record_id = "rec_del_file_test"
    file1_id = "file_to_del_1"
    file2_id = "file_to_keep_2"

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, created_at, status)
            VALUES (?, ?, 'vehicle', 'Car Policy Documents', 'CODE_CAR', '2026-10-01', 'active')
            ON CONFLICT(id) DO UPDATE SET status = 'active'
            """,
            (record_id, user_id)
        )
        await db.execute(
            """
            INSERT INTO files (id, record_id, user_id, drive_file_id, coded_name, mime, size_bytes, created_at)
            VALUES (?, ?, ?, 'drive_f1_xyz', 'car_policy.pdf', 'application/pdf', 1024, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO NOTHING
            """,
            (file1_id, record_id, user_id)
        )
        await db.execute(
            """
            INSERT INTO files (id, record_id, user_id, drive_file_id, coded_name, mime, size_bytes, created_at)
            VALUES (?, ?, ?, 'drive_f2_abc', 'car_receipt.jpg', 'image/jpeg', 2048, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO NOTHING
            """,
            (file2_id, record_id, user_id)
        )
        await db.commit()

    # Step 1: Confirm dialog
    cq_confirm = MagicMock(spec=CallbackQuery)
    cq_confirm.from_user = dummy_user
    cq_confirm.data = f"del_file_confirm:{file1_id}:{record_id}"
    cq_confirm.message = MagicMock(spec=Message)
    cq_confirm.message.edit_text = AsyncMock()
    cq_confirm.answer = AsyncMock()

    await on_delete_file_confirm(cq_confirm)
    cq_confirm.message.edit_text.assert_called_once()
    assert "DELETE FILE" in cq_confirm.message.edit_text.call_args[0][0].upper()

    # Step 2: Execute deletion
    cq_exec = MagicMock(spec=CallbackQuery)
    cq_exec.from_user = dummy_user
    cq_exec.data = f"del_file_exec:{file1_id}:{record_id}"
    cq_exec.message = MagicMock(spec=Message)
    cq_exec.message.edit_text = AsyncMock()
    cq_exec.answer = AsyncMock()

    with patch("squares_vault.drive.api.DriveAPI.delete_file", new_callable=AsyncMock) as mock_drive_delete:
        await on_delete_file_exec(cq_exec)

        mock_drive_delete.assert_called_once_with("drive_f1_xyz")
        assert any("deleted" in str(c).lower() for c in cq_exec.answer.call_args_list)

    # Step 3: Verify DB state
    async with get_db() as db:
        # file1 should be deleted
        async with db.execute("SELECT * FROM files WHERE id = ?", (file1_id,)) as cur:
            assert await cur.fetchone() is None
        # file2 must still exist
        async with db.execute("SELECT * FROM files WHERE id = ?", (file2_id,)) as cur:
            row2 = await cur.fetchone()
            assert row2 is not None
            assert row2["drive_file_id"] == "drive_f2_abc"
        # Record itself must be active
        async with db.execute("SELECT * FROM records WHERE id = ?", (record_id,)) as cur:
            rec = await cur.fetchone()
            assert rec is not None
            assert rec["status"] == "active"


@pytest.mark.asyncio
async def test_delete_file_authorization_security(dummy_user):
    """A user cannot delete files belonging to someone else."""
    owner_id = 88003
    hacker_id = 99999
    record_id = "rec_auth_test"
    file_id = "file_secret_99"

    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (owner_id, owner_id))
        await db.execute(
            """
            INSERT INTO records (id, user_id, category, title, code, created_at, status)
            VALUES (?, ?, 'ids', 'Secret Passport', 'CODE_PASS', '2026-10-01', 'active')
            ON CONFLICT(id) DO UPDATE SET status = 'active'
            """,
            (record_id, owner_id)
        )
        await db.execute(
            """
            INSERT INTO files (id, record_id, user_id, drive_file_id, coded_name, mime, size_bytes, created_at)
            VALUES (?, ?, ?, 'drive_passport_secret', 'passport.pdf', 'application/pdf', 1024, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO NOTHING
            """,
            (file_id, record_id, owner_id)
        )
        await db.commit()

    hacker_user = User(id=hacker_id, is_bot=False, first_name="Intruder")
    cq_exec = MagicMock(spec=CallbackQuery)
    cq_exec.from_user = hacker_user
    cq_exec.data = f"del_file_exec:{file_id}:{record_id}"
    cq_exec.message = MagicMock(spec=Message)
    cq_exec.message.edit_text = AsyncMock()
    cq_exec.answer = AsyncMock()

    with patch("squares_vault.drive.api.DriveAPI.delete_file", new_callable=AsyncMock) as mock_drive_delete:
        await on_delete_file_exec(cq_exec)

        mock_drive_delete.assert_not_called()
        cq_exec.answer.assert_called_once()
        assert "not found" in cq_exec.answer.call_args[0][0].lower() or "denied" in cq_exec.answer.call_args[0][0].lower()

    # Verify file still in DB
    async with get_db() as db:
        async with db.execute("SELECT * FROM files WHERE id = ?", (file_id,)) as cur:
            assert await cur.fetchone() is not None
