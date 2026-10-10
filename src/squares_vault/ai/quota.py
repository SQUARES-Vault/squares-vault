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
        # 1. Global ceiling across pooled keys
        num_keys = len(config.settings.gemini_api_keys) if config.settings.gemini_api_keys else 1
        total_daily_quota = max(
            config.settings.ai_global_daily_quota,
            num_keys * config.settings.ai_key_daily_limit,
        )

        async with db.execute("SELECT count FROM ai_global WHERE day = ?", (today,)) as cur:
            row = await cur.fetchone()
            global_count = row["count"] if row else 0

        if global_count >= total_daily_quota:
            raise GlobalCeilingExceededError("Global daily AI quota reached across all pooled keys.")

        # 2. Per-user cap (exempt admins)
        if user_id not in config.settings.admin_user_ids:
            async with db.execute(
                "SELECT count FROM ai_usage WHERE user_id = ? AND day = ?", (user_id, today)
            ) as cur:
                row = await cur.fetchone()
                user_count = row["count"] if row else 0

            if user_count >= config.settings.ai_per_user_daily_cap:
                raise QuotaExceededError("Your daily AI limit has been reached.")

        # 3. Increment counters
        await db.execute(
            "INSERT INTO ai_global (day, count) VALUES (?, 1) ON CONFLICT(day) DO UPDATE SET count = count + 1",
            (today,),
        )
        await db.execute(
            "INSERT INTO ai_usage (user_id, day, count) VALUES (?, ?, 1) ON CONFLICT(user_id, day) DO UPDATE SET count = count + 1",
            (user_id, today),
        )
        await db.commit()


async def get_daily_usage(day: str | None = None) -> int:
    """Return total AI requests processed on given day (YYYY-MM-DD, UTC)."""
    target_day = day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    async with get_db() as db:
        async with db.execute("SELECT count FROM ai_global WHERE day = ?", (target_day,)) as cur:
            row = await cur.fetchone()
            return row["count"] if row else 0


async def get_monthly_usage(month: str | None = None) -> int:
    """Return total AI requests processed in given month (YYYY-MM, UTC)."""
    target_month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    pattern = f"{target_month}-%"
    async with get_db() as db:
        async with db.execute(
            "SELECT COALESCE(SUM(count), 0) as total FROM ai_global WHERE day LIKE ?", (pattern,)
        ) as cur:
            row = await cur.fetchone()
            return row["total"] if row else 0


async def get_ai_quota_summary() -> dict:
    """Aggregate live daily and monthly quota statistics across pooled keys."""
    import calendar
    from squares_vault.ai.pool import get_key_pool

    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")
    _, days_in_month = calendar.monthrange(now.year, now.month)

    num_keys = len(config.settings.gemini_api_keys) if config.settings.gemini_api_keys else 1
    daily_cap_per_key = config.settings.ai_key_daily_limit

    total_daily_limit = num_keys * daily_cap_per_key
    total_monthly_limit = total_daily_limit * days_in_month

    pool = get_key_pool()
    pool_keys = pool.get_status_summary()

    # 1. Check in-memory requests handled today by the key pool
    pool_used_today = sum(k["requests_today"] for k in pool_keys)
    # 2. Check requests persisted in the database for today
    db_used_today = await get_daily_usage(today_str)

    # 3. Synchronize: if pool handled requests not yet persisted, update ai_global
    if pool_used_today > db_used_today:
        diff = pool_used_today - db_used_today
        async with get_db() as db:
            await db.execute(
                "INSERT INTO ai_global (day, count) VALUES (?, ?) ON CONFLICT(day) DO UPDATE SET count = count + ?",
                (today_str, pool_used_today, diff),
            )
            await db.commit()
        used_today = pool_used_today
    else:
        used_today = db_used_today

    # 4. Monthly usage accurately reflects all days in the month (including today)
    used_month = await get_monthly_usage(month_str)

    remaining_today = max(0, total_daily_limit - used_today)
    remaining_month = max(0, total_monthly_limit - used_month)

    return {
        "num_keys": num_keys,
        "daily_cap_per_key": daily_cap_per_key,
        "days_in_month": days_in_month,
        "total_daily_limit": total_daily_limit,
        "used_today": used_today,
        "remaining_today": remaining_today,
        "pct_used_today": (used_today / total_daily_limit * 100.0) if total_daily_limit else 0.0,
        "total_monthly_limit": total_monthly_limit,
        "used_month": used_month,
        "remaining_month": remaining_month,
        "pct_used_month": (used_month / total_monthly_limit * 100.0) if total_monthly_limit else 0.0,
        "pool_keys": pool_keys,
        "active_keys_count": pool.active_count,
    }
