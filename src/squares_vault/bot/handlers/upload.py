"""Handler for incoming documents and photos."""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message

from squares_vault.i18n.loader import t
from squares_vault.ai.queue import queue_document

router = Router(name="upload")

@router.message(F.photo)
async def handle_photo(message: Message) -> None:
    """Handle photo uploads."""
    # Get highest resolution photo
    photo = message.photo[-1]
    file_id = photo.file_id
    
    await _handle_file(message, file_id)


@router.message(F.document)
async def handle_document(message: Message) -> None:
    """Handle document uploads (PDFs/Images)."""
    mime = message.document.mime_type
    if mime not in ["application/pdf", "image/jpeg", "image/png"]:
        await message.reply("❌ I can only process PDFs and images.")
        return
        
    file_id = message.document.file_id
    await _handle_file(message, file_id)


async def _handle_file(message: Message, file_id: str) -> None:
    """Common logic to queue a file."""
    # Add to queue
    await queue_document(message.from_user.id, file_id, file_id)
    await message.reply("⏳ Upload received! I am reading the document...")
