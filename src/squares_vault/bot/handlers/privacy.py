"""Privacy and lifecycle handlers."""

from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import Command

from squares_vault import config
from squares_vault.bot.states import FamilyAccess
from squares_vault.db.connection import get_db
from squares_vault.logging_setup import get_logger

log = get_logger(__name__)
router = Router(name="privacy")

@router.callback_query(F.data == "settings_storage")
async def on_settings_storage(cq: CallbackQuery) -> None:
    user_id = cq.from_user.id
    text, kb = await _build_storage_view(user_id)
    await cq.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await cq.answer()


@router.callback_query(F.data == "settings_export")
async def on_settings_export(cq: CallbackQuery) -> None:
    await cmd_export(cq.message)
    await cq.answer()


@router.callback_query(F.data == "settings_delete")
async def on_settings_delete(cq: CallbackQuery) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⚠️ Yes, Delete Everything", callback_data="settings_delete_confirm")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="settings_back")]
    ])
    await cq.message.edit_text(
        "🚨 **DANGER ZONE**\n\n"
        "Are you absolutely sure you want to permanently delete your account, all your records, and disconnect your Drive?\n\n"
        "This action CANNOT be undone.",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await cq.answer()


@router.callback_query(F.data == "settings_delete_confirm")
async def on_settings_delete_confirm(cq: CallbackQuery) -> None:
    await cmd_deletemydata(cq.message, user_id_override=cq.from_user.id)
    await cq.answer()


async def _build_storage_view(user_id: int):
    async with get_db() as db:
        async with db.execute(
            "SELECT COUNT(*) as c FROM records WHERE user_id = ? AND status != 'soft_deleted'",
            (user_id,)
        ) as cur:
            records = (await cur.fetchone())["c"]
        async with db.execute(
            "SELECT COUNT(*) as c FROM reminders WHERE user_id = ? AND state = 'pending'",
            (user_id,)
        ) as cur:
            reminders = (await cur.fetchone())["c"]
        async with db.execute(
            "SELECT category, COUNT(*) as c FROM records WHERE user_id = ? AND status != 'soft_deleted' GROUP BY category ORDER BY c DESC",
            (user_id,)
        ) as cur:
            cat_rows = await cur.fetchall()

    _CAT_EMOJIS = {
        "vehicle": "🚗", "health": "🏥", "finance": "🏦", "home": "🏠",
        "business": "👔", "bills & subscriptions": "🧾", "identity": "🪪",
        "education": "🎓", "travel": "✈️", "other": "📁"
    }
    cat_lines = []
    for r in cat_rows:
        cat_lower = r["category"].lower()
        emoji = _CAT_EMOJIS.get(cat_lower, "📁")
        cat_lines.append(f"{emoji} *{r['category'].title()}:* {r['c']} record(s)")

    breakdown_text = "\n".join(cat_lines) if cat_lines else "_No records stored yet._"

    msg = (
        f"📊 *Storage Breakdown*\n\n"
        f"📁 *Total Active Documents:* {records}\n"
        f"⏰ *Active Reminders:* {reminders}\n\n"
        f"📂 *Folders:*\n{breakdown_text}\n\n"
        f"☁️ _All files are securely synced to your connected Google Drive._"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Back", callback_data="settings_back")]])
    return msg, kb


@router.message(Command("storage"))
async def cmd_storage(message: Message, user_id_override=None) -> None:
    """Show storage stats."""
    user_id = user_id_override or message.from_user.id
    text, kb = await _build_storage_view(user_id)
    await message.reply(text, reply_markup=kb, parse_mode="Markdown")

@router.message(Command("export"))
async def cmd_export(message: Message) -> None:
    """Export data."""
    await message.reply(
        "📦 Your data is already safely stored in your Google Drive folder named 'SQUARES Vault Backup'.\n"
        "You can view and download everything directly from there!", parse_mode="Markdown"
    )

@router.message(Command("deletemydata"))
async def cmd_deletemydata(message: Message, user_id_override=None) -> None:
    """Delete all user data (7 steps)."""
    user_id = user_id_override or message.from_user.id
    
    # 1. Ask for confirmation
    await message.reply(
        "⚠️ *DANGER ZONE* ⚠️\n\n"
        "Are you absolutely sure you want to delete your account? This will:\n"
        "1. Delete all your data from my database.\n"
        "2. Keep a tombstone for synchronization.\n"
        "3. (Your files in Google Drive will remain untouched).\n\n"
        "Reply with `CONFIRM DELETE` to proceed.", parse_mode="Markdown"
    )


@router.message(F.text == "CONFIRM DELETE")
async def process_delete_my_data(message: Message) -> None:
    """Execute deletion."""
    user_id = message.from_user.id
    
    import hashlib
    from datetime import datetime, timezone
    
    now = datetime.now(timezone.utc).isoformat() + "Z"
    user_hash = hashlib.sha256(f"deleted_{user_id}_{now}".encode()).hexdigest()
    
    async with get_db() as db:
        # Delete everything
        await db.execute("DELETE FROM users WHERE id = ?", (user_id,))
        await db.execute("DELETE FROM oauth_tokens WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM user_keys WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM records WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM files WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM reminders WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM ai_usage WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM drafts WHERE user_id = ?", (user_id,))
        await db.execute("DELETE FROM ai_queue WHERE user_id = ?", (user_id,))
        
        # Add tombstone
        await db.execute("INSERT INTO tombstones (user_hash, deleted_at) VALUES (?, ?)", (user_hash, now))
        await db.commit()
        
    await message.reply("✅ Your account and data have been permanently deleted.")


@router.callback_query(F.data == "drive_auth_start")
async def on_drive_auth_start(cq: CallbackQuery) -> None:
    from squares_vault.flows.drive_auth import cmd_connect_drive
    await cq.message.delete()
    await cmd_connect_drive(cq.message)
    await cq.answer()

@router.callback_query(F.data == "settings_connections")
async def on_settings_connections(cq: CallbackQuery) -> None:
    user_id = cq.from_user.id
    
    # Check Drive tokens
    from squares_vault.drive.token_store import get_token
    tokens = await get_token(user_id)
    drive_status = "🟢 Connected" if tokens else "🔴 Disconnected"
    
    # Check Gemini API
    from squares_vault import config
    gemini_status = "🟢 Active" if config.settings.gemini_api_key else "🔴 Missing"
    
    # Check if rate limited (mock check or from log)
    # Simple check for now
    
    text = f"🔗 **Connections Dashboard**\n\n" \
           f"**Google Drive:** {drive_status}\n" \
           f"**Gemini AI API:** {gemini_status}\n"
           
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔄 Reconnect Google Account", callback_data="drive_auth_start")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="settings_back")]
    ])
    
    await cq.message.edit_text(text, reply_markup=kb, parse_mode="Markdown")
    await cq.answer()

@router.callback_query(F.data == "settings_back")
async def on_settings_back(cq: CallbackQuery) -> None:
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Storage Stats", callback_data="settings_storage")],
        [InlineKeyboardButton(text="🔗 Connections", callback_data="settings_connections")],
        [InlineKeyboardButton(text="👥 Family / Co-Pilot Access", callback_data="settings_access")],
        [InlineKeyboardButton(text="💾 Export Data", callback_data="settings_export")],
        [InlineKeyboardButton(text="📜 Privacy Policy & Terms", url=config.settings.privacy_policy_url)],
        [InlineKeyboardButton(text="🛑 Delete Account", callback_data="settings_delete")],
        [InlineKeyboardButton(text="⬅️ Back", callback_data="settings_close")]
    ])
    await cq.message.edit_text("⚙️ **Settings & Privacy**\n\nManage your data, export backups, or delete your account.", reply_markup=kb, parse_mode="Markdown")
    await cq.answer()


@router.callback_query(F.data == "settings_access")
async def on_settings_access(cq: CallbackQuery) -> None:
    """View and manage family / co-pilot access grants."""
    user_id = cq.from_user.id
    
    async with get_db() as db:
        async with db.execute(
            "SELECT id, granted_id, category, created_at FROM access_grants WHERE owner_id = ? ORDER BY created_at DESC",
            (user_id,)
        ) as cur:
            grants = await cur.fetchall()

    kb_buttons = []
    if grants:
        lines = ["👥 **Family & Co-Pilot Access**\n\nThe following members have access to your specified folders:\n"]
        for g in grants:
            cat_name = g['category'].title()
            gid = g['granted_id']
            lines.append(f"• User ID `{gid}`: *{cat_name}*")
            kb_buttons.append([InlineKeyboardButton(text=f"❌ Revoke `{gid}` ({cat_name})", callback_data=f"access_rev:{g['id']}")])
    else:
        lines = [
            "👥 **Family & Co-Pilot Access**\n\n"
            "You have not granted access to any family members yet.\n\n"
            "You can whitelist specific Telegram User IDs to view only specific folders (e.g. *Health*, *Home*, or *All*), keeping all your other documents strictly private."
        ]

    kb_buttons.append([InlineKeyboardButton(text="➕ Grant Access to Member", callback_data="access_add")])
    kb_buttons.append([InlineKeyboardButton(text="⬅️ Back", callback_data="settings_back")])
    kb = InlineKeyboardMarkup(inline_keyboard=kb_buttons)

    await cq.message.edit_text("\n".join(lines), reply_markup=kb, parse_mode="Markdown")
    await cq.answer()


@router.callback_query(F.data == "access_add")
async def on_access_add(cq: CallbackQuery, state) -> None:
    from squares_vault.bot.states import FamilyAccess
    await state.set_state(FamilyAccess.waiting_for_grant)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ Cancel", callback_data="settings_access")]])
    await cq.message.edit_text(
        "👥 **Add Family / Co-Pilot Member**\n\n"
        "Send the member's Telegram User ID and the folder to share, separated by space.\n\n"
        "**Examples:**\n"
        "• `12345678 health`\n"
        "• `12345678 home`\n"
        "• `12345678 vehicle`\n"
        "• `12345678 all`",
        reply_markup=kb,
        parse_mode="Markdown"
    )
    await cq.answer()


@router.message(FamilyAccess.waiting_for_grant, F.text)
async def handle_access_grant_input(message: Message, state: FSMContext) -> None:
    """Handle text input granting access to a user."""
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[0].isdigit():
        await message.reply(
            "❌ Invalid format. Please send Telegram User ID and folder separated by space.\nExample: `12345678 health`"
        )
        return

    await state.clear()
    granted_id_str = parts[0]
    category = parts[1].strip().lower()

    granted_id = int(granted_id_str)
    import ulid
    grant_id = str(ulid.ULID())
    owner_id = message.from_user.id

    async with get_db() as db:
        await db.execute(
            "INSERT OR REPLACE INTO access_grants (id, owner_id, granted_id, category) VALUES (?, ?, ?, ?)",
            (grant_id, owner_id, granted_id, category)
        )
        await db.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="👥 View Family Access", callback_data="settings_access")]])
    await message.reply(
        f"✅ **Access Granted!**\n\nTelegram User `{granted_id}` can now view your *{category.title()}* documents in Browse & Search.",
        reply_markup=kb,
        parse_mode="Markdown"
    )


@router.callback_query(F.data.startswith("access_rev:"))
async def on_access_revoke(cq: CallbackQuery) -> None:
    grant_id = cq.data.split(":", 1)[1]
    async with get_db() as db:
        await db.execute("DELETE FROM access_grants WHERE id = ? AND owner_id = ?", (grant_id, cq.from_user.id))
        await db.commit()
    await cq.answer("Access revoked successfully.")
    await on_settings_access(cq)


@router.callback_query(F.data == "settings_close")
async def on_settings_close(cq: CallbackQuery) -> None:
    try:
        await cq.message.delete()
    except Exception:
        pass
    await cq.answer()