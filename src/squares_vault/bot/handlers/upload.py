"""Handler for incoming documents and photos, including multi-page albums."""

from __future__ import annotations

import asyncio
from typing import Dict, List
from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import Message

from squares_vault.i18n.loader import t
from squares_vault.ai.queue import queue_document, queue_multi_document

router = Router(name="upload")

_MEDIA_GROUPS: Dict[str, List[dict[str, str]]] = {}
_MEDIA_GROUP_TIMERS: Dict[str, asyncio.Task] = {}
_MEDIA_GROUP_MESSAGES: Dict[str, Message] = {}


async def _process_media_group_after_delay(group_id: str, user_id: int) -> None:
    """Wait for all album items to arrive before queueing."""
    await asyncio.sleep(1.2)
    files = _MEDIA_GROUPS.pop(group_id, [])
    msg = _MEDIA_GROUP_MESSAGES.pop(group_id, None)
    _MEDIA_GROUP_TIMERS.pop(group_id, None)

    if not files:
        return

    from squares_vault.db.connection import get_db
    async with get_db() as db:
        async with db.execute("SELECT id FROM users WHERE id = ?", (user_id,)) as cur:
            exists = await cur.fetchone()
    if not exists:
        if msg:
            await msg.reply("👋 Welcome! Please send /start to set up your Vault before adding documents.")
        return

    from squares_vault.bot.handlers.admin import get_system_setting
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    if (await get_system_setting("ai_enabled", "1")) != "1":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Add Manually", callback_data="add_manual")]
        ])
        if msg:
            await msg.reply(
                "⏸️ **AI Document Scanning is Temporarily Paused**\n\n"
                "Automated document reading is currently paused by the administrator. "
                "You can add this record manually right now, or upload again once AI is resumed.",
                reply_markup=kb,
                parse_mode="Markdown",
            )
        return

    if len(files) == 1:
        await queue_document(user_id, files[0]["file_id"], files[0]["file_id"])
    else:
        await queue_multi_document(user_id, files)

    if msg:
        count_str = f" ({len(files)} pages)" if len(files) > 1 else ""
        await msg.reply(f"⏳ Upload received{count_str}! I am reading the document...")


@router.message(StateFilter(None), F.media_group_id)
async def handle_media_group(message: Message) -> None:
    """Handle photos/documents sent together in a Telegram album."""
    group_id = message.media_group_id
    user_id = message.from_user.id
    
    file_id = None
    mime = "image/jpeg"
    if message.photo:
        file_id = message.photo[-1].file_id
        mime = "image/jpeg"
    elif message.document:
        if message.document.mime_type in ["application/pdf", "image/jpeg", "image/png"]:
            file_id = message.document.file_id
            mime = message.document.mime_type

    if not file_id:
        return

    if group_id not in _MEDIA_GROUPS:
        _MEDIA_GROUPS[group_id] = []
        
    _MEDIA_GROUPS[group_id].append({"file_id": file_id, "mime": mime})
    _MEDIA_GROUP_MESSAGES[group_id] = message

    # Cancel previous timer and reset debounce
    old_timer = _MEDIA_GROUP_TIMERS.get(group_id)
    if old_timer and not old_timer.done():
        old_timer.cancel()

    _MEDIA_GROUP_TIMERS[group_id] = asyncio.create_task(_process_media_group_after_delay(group_id, user_id))


@router.message(StateFilter(None), F.photo)
async def handle_photo(message: Message) -> None:
    """Handle single photo uploads."""
    photo = message.photo[-1]
    await _handle_file(message, photo.file_id)


@router.message(StateFilter(None), F.document)
async def handle_document(message: Message) -> None:
    """Handle single document uploads (PDFs/Images)."""
    mime = message.document.mime_type
    if mime not in ["application/pdf", "image/jpeg", "image/png"]:
        await message.reply("❌ I can only process PDFs and images.")
        return
        
    await _handle_file(message, message.document.file_id)


async def _handle_file(message: Message, file_id: str) -> None:
    """Common logic to queue a single file."""
    from squares_vault.db.connection import get_db
    user_id = message.from_user.id
    async with get_db() as db:
        async with db.execute("SELECT id FROM users WHERE id = ?", (user_id,)) as cur:
            exists = await cur.fetchone()
    if not exists:
        await message.reply("👋 Welcome! Please send /start to set up your Vault before adding documents.")
        return

    from squares_vault.bot.handlers.admin import get_system_setting
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    if (await get_system_setting("ai_enabled", "1")) != "1":
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✏️ Add Manually", callback_data="add_manual")]
        ])
        await message.reply(
            "⏸️ **AI Document Scanning is Temporarily Paused**\n\n"
            "Automated document reading is currently paused by the administrator. "
            "You can add this record manually right now, or upload again once AI is resumed.",
            reply_markup=kb,
            parse_mode="Markdown",
        )
        return

    await queue_document(user_id, file_id, file_id)
    await message.reply("⏳ Upload received! I am reading the document...")
