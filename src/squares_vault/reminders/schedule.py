"""Reminder schedule generation and math."""

from __future__ import annotations

from typing import List
from datetime import datetime, timedelta
import ulid

DEFAULT_SCHEDULES = {
    "vehicle": [-30, -7, -1, 0, 1],
    "insurance": [-30, -7, -1, 0, 1],
    "ids & government docs": [-30, -7, -1, 0, 1],
    "warranty & purchases": [-30, -7],
    "people & occasions": [-1, 0],
    "bills & subscriptions": [-3, 0],
    "health": [-7, -1],
    "home": [-30, -7, -1, 0],
}


def generate_reminders(
    record_id: str,
    user_id: int,
    category: str,
    key_date: datetime,
    reminder_time_str: str = "09:00",
    override_offsets: List[int] = None
) -> List[dict]:
    """Generate a list of reminder dicts for a record."""
    cat_lower = category.lower()
    offsets = override_offsets if override_offsets is not None else DEFAULT_SCHEDULES.get(cat_lower, [])
    
    # Parse reminder time (HH:MM)
    try:
        hr, mn = map(int, reminder_time_str.split(":"))
    except ValueError:
        hr, mn = 9, 0
        
    reminders = []
    
    # Generate standard pre/due reminders
    for offset in offsets:
        # A day is a full 24h. We want it at exactly the user's preferred time on that day
        # offset=0 means on the key date itself. offset=-7 means 7 days before.
        # Ensure key_date has the time set to hr:mn
        fire_at = key_date.replace(hour=hr, minute=mn, second=0, microsecond=0) + timedelta(days=offset)
        
        kind = "pre" if offset < 0 else "due" if offset == 0 else "overdue"
        
        reminders.append({
            "id": str(ulid.ULID()),
            "record_id": record_id,
            "user_id": user_id,
            "kind": kind,
            "offset_days": offset,
            "fire_at": fire_at.isoformat() + "Z", # Store as UTC ISO (we assume key_date passed in is already UTC-converted for that local day)
            "state": "pending"
        })
        
    return reminders
