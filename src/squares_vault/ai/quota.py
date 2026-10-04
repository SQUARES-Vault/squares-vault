"""AI quota and rate limiting management."""

from __future__ import annotations

from datetime import datetime, timezone
from squares_vault import config
from squares_vault.db.connection import get_db


class QuotaExceededError(Exception):
    pass


class GlobalCeilingExceededError(Exception):
    pass


async def check_and_increment_quota(user_id: int) -> None:
    """Check AI quotas. Raises exceptions if blocked."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    async with get_db() as db:
        # 1. Global ceiling
        async with db.execute("SELECT count FROM ai_global WHERE day = ?", (today,)) as cur:
            row = await cur.fetchone()
            global_count = row["count"] if row else 0
            
        if global_count >= config.settings.ai_global_daily_quota:
            raise GlobalCeilingExceededError("Global daily AI quota reached.")
            
        # 2. Per-user cap
        async with db.execute("SELECT count FROM ai_usage WHERE user_id = ? AND day = ?", (user_id, today)) as cur:
            row = await cur.fetchone()
            user_count = row["count"] if row else 0
            
        if user_count >= config.settings.ai_per_user_daily_cap:
            raise QuotaExceededError("Your daily AI limit has been reached.")
            
        # 3. Increment
        await db.execute(
            "INSERT INTO ai_global (day, count) VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET count = count + 1",
            (today,)
        )
        await db.execute(
            "INSERT INTO ai_usage (user_id, day, count) VALUES (?, ?, 1) ON CONFLICT(user_id, day) DO UPDATE SET count = count + 1",
            (user_id, today)
        )
        await db.commit()
