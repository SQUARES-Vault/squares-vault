import pytest
from datetime import datetime, timezone
from squares_vault.reminders.schedule import generate_reminders

def test_m5_schedule_math_vehicle():
    key_date = datetime(2025, 1, 31, tzinfo=timezone.utc)
    reminders = generate_reminders("rec_1", 999, "vehicle", key_date, "10:30")
    
    assert len(reminders) == 3
    
    offsets = [r["offset_days"] for r in reminders]
    assert offsets == [-10, -2, 0]
    
    # Check that time is set correctly
    r_due = next(r for r in reminders if r["offset_days"] == 0)
    assert r_due["fire_at"] == "2025-01-31T10:30:00+00:00Z"
    assert r_due["kind"] == "overdue"

def test_m5_schedule_math_warranty():
    key_date = datetime(2025, 1, 31, tzinfo=timezone.utc)
    reminders = generate_reminders("rec_2", 999, "Warranty & purchases", key_date, "09:00")
    
    assert len(reminders) == 3
    offsets = [r["offset_days"] for r in reminders]
    assert offsets == [-10, -2, 0]

def test_m5_schedule_math_override():
    key_date = datetime(2025, 1, 31, tzinfo=timezone.utc)
    reminders = generate_reminders("rec_3", 999, "vehicle", key_date, "09:00", override_offsets=[-14, 0])
    
    assert len(reminders) == 2
    offsets = [r["offset_days"] for r in reminders]
    assert offsets == [-14, 0]


def test_m5_schedule_math_dual_times():
    key_date = datetime(2026, 11, 18)
    reminders = generate_reminders("rec_4", 999, "vehicle", key_date)
    
    assert len(reminders) == 6
    offsets = [r["offset_days"] for r in reminders]
    assert offsets == [-10, -10, -2, -2, 0, 0]
    
    # Check kinds
    assert reminders[0]["kind"] == "pre"
    assert reminders[2]["kind"] == "due"
    assert reminders[4]["kind"] == "overdue"
