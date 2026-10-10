"""Search indexing and querying."""

from __future__ import annotations

from typing import List, Dict, Any
from squares_vault.db.connection import get_db
from squares_vault.logging_setup import get_logger

log = get_logger(__name__)

async def sync_fts_for_record(record_id: str, user_id: int, title: str, category: str, tags: str = None, notes: str = None) -> None:
    """Sync a record into both FTS5 indexes."""
    text = f"{title} {category} {tags or ''} {notes or ''}".lower()
    
    async with get_db() as db:
        # Delete old entries
        await db.execute("DELETE FROM fts_records WHERE record_id = ?", (record_id,))
        await db.execute("DELETE FROM fts_trigram WHERE record_id = ?", (record_id,))
        
        # Insert new
        await db.execute(
            "INSERT INTO fts_records (record_id, user_id, text) VALUES (?, ?, ?)",
            (record_id, user_id, text)
        )
        await db.execute(
            "INSERT INTO fts_trigram (record_id, user_id, text) VALUES (?, ?, ?)",
            (record_id, user_id, text)
        )
        await db.commit()


async def search_records(user_id: int, query: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Search records for a user using FTS5."""
    # Simple prefix search for unicode61
    import re
    # Strip all special characters to avoid FTS5 syntax errors
    safe_text = re.sub(r'[^a-zA-Z0-9\s]', ' ', query)
    words = safe_text.split()
    if not words:
        return []
    fts_query = " AND ".join(f'"{w}"*' for w in words)
    safe_query = safe_text
    
    async with get_db() as db:
        async with db.execute(
            """
            SELECT r.id, r.title, r.category, r.key_date, r.status
            FROM fts_records f
            JOIN records r ON f.record_id = r.id
            WHERE (
                f.user_id = ? 
                OR r.id IN (
                    SELECT r2.id FROM records r2 
                    JOIN access_grants ag ON r2.user_id = ag.owner_id 
                    WHERE ag.granted_id = ? AND (ag.category = 'all' OR r2.category LIKE '%' || ag.category || '%')
                )
            ) AND fts_records MATCH ? AND r.status != 'soft_deleted'
            ORDER BY rank
            LIMIT ?
            """,
            (user_id, user_id, fts_query, limit)
        ) as cur:
            results = await cur.fetchall()
            
        # Fallback to trigram if no results
        if not results and len(query) >= 3:
            async with db.execute(
                """
                SELECT r.id, r.title, r.category, r.key_date, r.status
                FROM fts_trigram f
                JOIN records r ON f.record_id = r.id
                WHERE (
                    f.user_id = ? 
                    OR r.id IN (
                        SELECT r2.id FROM records r2 
                        JOIN access_grants ag ON r2.user_id = ag.owner_id 
                        WHERE ag.granted_id = ? AND (ag.category = 'all' OR r2.category LIKE '%' || ag.category || '%')
                    )
                ) AND text LIKE ? AND r.status != 'soft_deleted'
                LIMIT ?
                """,
                (user_id, user_id, f"%{safe_query}%", limit)
            ) as cur:
                results = await cur.fetchall()
                
    return [dict(r) for r in results]
