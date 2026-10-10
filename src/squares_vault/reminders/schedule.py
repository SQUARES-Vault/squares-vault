"""Reminder schedule generation and math."""

from __future__ import annotations

from typing import List, Union
from datetime import datetime, timedelta, timezone, date
import zoneinfo
import ulid

# Universal Reminder Schedule:
# Early alert: 10 days from end date (-10)
# Due date alert: 2 days before end date (-2)
# Overdue alert: on the end date (0)
DEFAULT_SCHEDULES = {
    "vehicle": [-10, -2, 0],
    "insurance": [-10, -2, 0],
    "ids & government docs": [-10, -2, 0],
    "warranty & purchases": [-10, -2, 0],
    "people & occasions": [-10, -2, 0],
    "bills & subscriptions": [-10, -2, 0],
    "health": [-10, -2, 0],
    "home": [-10, -2, 0],
    "custom": [-10, -2, 0],
}

DEFAULT_ALERT_OFFSETS = [-10, -2, 0]
DEFAULT_REMINDER_TIMES = ["11:00", "18:00"]


def generate_reminders(
    record_id: str,
    user_id: int,
    category: str,
    key_date: datetime,
    reminder_time_str: Union[str, List[str], None] = None,
    override_offsets: List[int] = None,
    user_tz: str = "Asia/Kolkata",
) -> List[dict]:
    """Generate a list of reminder dicts for a record.
    
    Default rules:
    - Early alert: 10 days from end date (-10) -> kind="pre"
    - Due date alert: 2 days before end date (-2) -> kind="due"
    - Overdue alert: on the end date (0) -> kind="overdue"
    - Reminder times: 11:00 and 18:00 on each alert date.
    """
    cat_lower = category.lower() if category else ""
    offsets = override_offsets if override_offsets is not None else DEFAULT_SCHEDULES.get(cat_lower, DEFAULT_ALERT_OFFSETS)
    
    if reminder_time_str is None:
        times = DEFAULT_REMINDER_TIMES
    elif isinstance(reminder_time_str, list):
        times = reminder_time_str
    else:
        times = [reminder_time_str]
        
    reminders = []
    
    try:
        tz = zoneinfo.ZoneInfo(user_tz)
    except Exception:
        tz = zoneinfo.ZoneInfo("Asia/Kolkata")
        
    for offset in offsets:
        if offset == -10:
            kind = "pre"
        elif offset == -2:
            kind = "due"
        elif offset == 0:
            kind = "overdue"
        else:
            kind = "pre" if offset < -2 else ("due" if offset < 0 else "overdue")

        for time_str in times:
            try:
                hr, mn = map(int, time_str.split(":"))
            except ValueError:
                hr, mn = 11, 0

            if key_date.tzinfo is not None:
                fire_at = key_date.replace(hour=hr, minute=mn, second=0, microsecond=0) + timedelta(days=offset)
                iso_str = fire_at.isoformat()
                if not iso_str.endswith("Z"):
                    iso_str += "Z"
            else:
                target_d = (key_date + timedelta(days=offset)).date()
                local_dt = datetime(target_d.year, target_d.month, target_d.day, hr, mn, 0, tzinfo=tz)
                utc_dt = local_dt.astimezone(timezone.utc)
                iso_str = utc_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            reminders.append({
                "id": str(ulid.ULID()),
                "record_id": record_id,
                "user_id": user_id,
                "kind": kind,
                "offset_days": offset,
                "fire_at": iso_str,
                "state": "pending"
            })
            
    return reminders
