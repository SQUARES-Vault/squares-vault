"""Google Drive device authorization bot flow."""

from __future__ import annotations

import asyncio

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, Message

from squares_vault.drive.client import start_device_flow, poll_for_token
from squares_vault.drive.token_store import save_token
from squares_vault.i18n.loader import t

router = Router(name="drive_auth")


@router.message(Command("connect"))
async def cmd_connect_drive(message: Message) -> None:
    """Start the device auth flow for Google Drive."""
    user_id = message.from_user.id
    
    # 1. Get device code
    flow_info = await start_device_flow()
    
    # 2. Tell user with Copy button
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text="📋 Copy Code",
            copy_text=CopyTextButton(text=flow_info.user_code)
        )]
    ])
    
    await message.answer(
        t("drive_connect_prompt").format(
            url=flow_info.verification_url,
            code=flow_info.user_code
        ),
        reply_markup=kb,
        parse_mode="Markdown"
    )
    
    # 3. Poll in background (max 5 minutes)
    max_attempts = min(300 // flow_info.interval, 60)
    
    # Fire and forget the polling loop
    asyncio.create_task(_poll_and_save(user_id, message, flow_info.device_code, flow_info.interval, max_attempts))


async def _poll_and_save(user_id: int, message: Message, device_code: str, interval: int, max_attempts: int) -> None:
    token_info = await poll_for_token(device_code, interval, max_attempts)
    
    if not token_info or not token_info.refresh_token:
        await message.answer(t("drive_connect_timeout"))
        return
        
    await save_token(
        user_id,
        token_info.access_token,
        token_info.refresh_token,
        token_info.expires_in
    )
    
    await message.answer(t("drive_connect_success"))
