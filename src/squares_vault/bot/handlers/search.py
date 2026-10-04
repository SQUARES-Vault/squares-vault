"""Search handler."""

from __future__ import annotations

from aiogram import Router, F
from aiogram.types import Message
from aiogram.filters import Command

from squares_vault.search.index import search_records

router = Router(name="search")


@router.message(Command("search"))
async def cmd_search(message: Message) -> None:
    """Prompt user for a search query."""
    await message.reply("🔍 What are you looking for? (e.g. 'Car RC', 'Passport')")


@router.message(F.text & ~F.text.startswith("/"))
async def handle_search_query(message: Message) -> None:
    """Execute a search query on text messages."""
    query = message.text.strip()
    
    # Simple heuristic to not search very short garbage
    if len(query) < 2:
        return
        
    results = await search_records(message.from_user.id, query)
    
    if not results:
        await message.reply("❌ No records found.")
        return
        
    lines = [f"🔍 Found {len(results)} results:\n"]
    for i, r in enumerate(results, 1):
        dt = r['key_date'] or 'No date'
        lines.append(f"{i}. <b>{r['title']}</b> ({r['category']}) - {dt}")
        
    await message.reply("\n".join(lines))
