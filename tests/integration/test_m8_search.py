import pytest
import time
from squares_vault.db.connection import get_db
from squares_vault.search.index import sync_fts_for_record, search_records

@pytest.mark.asyncio
async def test_m8_search_performance(setup_db):
    """Test FTS5 search performance over 10k records."""
    
    user_id = 108
    # 1. Setup DB
    async with get_db() as db:
        await db.execute("INSERT OR IGNORE INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        
        # Insert 10k fake FTS records
        # Batch insert to make it fast for testing setup
        queries = []
        for i in range(10000):
            # Just some gibberish text with a needle in a haystack
            text = f"record {i} category_vehicle test_data"
            queries.append((f"rec_m8_{i}", user_id, text))
            
        await db.executemany("INSERT INTO fts_records (record_id, user_id, text) VALUES (?, ?, ?)", queries)
        
        # Insert actual records for the JOIN to work
        records = []
        for i in range(10000):
            records.append((f"rec_m8_{i}", user_id, 'vehicle', f"Title {i}", f"ABC_{i}"))
        await db.executemany("INSERT INTO records (id, user_id, category, title, code) VALUES (?, ?, ?, ?, ?)", records)
        await db.commit()
        
    # 2. Search for the needle
    start_time = time.time()
    results = await search_records(user_id, "record 999")
    end_time = time.time()
    
    # Check results
    assert len(results) > 0
    assert any(r["title"] == "Title 999" for r in results)
    
    # Must be instant (sub-100ms)
    assert (end_time - start_time) < 0.1
