import pytest
from squares_vault.ai.schema import ExtractedRecord
from squares_vault.ai.validate import validate_and_enrich
from squares_vault.ai.quota import check_and_increment_quota, QuotaExceededError, GlobalCeilingExceededError
from squares_vault.db.connection import get_db

def test_m6_golden_json_vehicle():
    raw_json = {
        "category": "vehicle",
        "title": "My Car RC",
        "vehicle_number": "KA 01 AB 1234",
        "key_date": "2030-05-10",
        "key_date_type": "expiry"
    }
    
    rec = ExtractedRecord(**raw_json)
    enriched = validate_and_enrich(rec)
    
    assert enriched.vehicle_number == "KA01AB1234"
    assert enriched.title == "My Car RC"


def test_m6_golden_json_warranty():
    raw_json = {
        "category": "warranty & purchases",
        "title": "Laptop",
        "purchase_date": "2024-01-01",
        "warranty_months": 12
    }
    
    rec = ExtractedRecord(**raw_json)
    enriched = validate_and_enrich(rec)
    
    assert enriched.key_date == "2025-01-01"
    assert enriched.key_date_type == "expiry"


@pytest.mark.asyncio
async def test_m6_cap_and_ceiling(setup_db, monkeypatch):
    monkeypatch.setenv("AI_GLOBAL_DAILY_QUOTA", "5")
    monkeypatch.setenv("AI_PER_USER_DAILY_CAP", "2")
    
    from squares_vault.config import init_settings
    init_settings()
    
    # Insert users
    async with get_db() as db:
        await db.execute("INSERT INTO users (id, chat_id) VALUES (1, 1), (2, 2)")
        await db.commit()
        
    # User 1 does 2 calls (hits personal cap)
    await check_and_increment_quota(1)
    await check_and_increment_quota(1)
    
    with pytest.raises(QuotaExceededError):
        await check_and_increment_quota(1)
        
    # User 2 does 2 calls
    await check_and_increment_quota(2)
    await check_and_increment_quota(2)
    
    # Global quota is 5. We used 4. We can do 1 more (from user 2 or 3)
    async with get_db() as db:
        await db.execute("INSERT INTO users (id, chat_id) VALUES (3, 3)")
        await db.commit()
        
    await check_and_increment_quota(3)
    
    # Now global ceiling is hit
    with pytest.raises(GlobalCeilingExceededError):
        await check_and_increment_quota(3)
