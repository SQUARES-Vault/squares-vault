import pytest
import ulid

from squares_vault.db.connection import get_db
from squares_vault.db.repositories import create_record, add_file, soft_delete_record
from squares_vault.drive.naming import generate_coded_name
from squares_vault.ops.images import process_image

# Provide a small valid JPEG for testing
TINY_JPEG = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xdb\x00C\x01\t\t\t\x0c\x0b\x0c\x18\r\r\x182!\x1c!22222222222222222222222222222222222222222222222222\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x10\x00\x02\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12!1A\x06\x13Qa\x07"q\x142\x81\x91\xa1\x08#B\xb1\xc1\x15R\xd1\xf0$3br\x82\t\n\x16\x17\x18\x19\x1a%&\'()*456789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe1\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf1\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xc4\x00\x1f\x01\x00\x03\x01\x01\x01\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xc4\x00\xb5\x11\x00\x02\x01\x02\x04\x04\x03\x04\x07\x05\x04\x04\x00\x01\x02\x11\x00\x01\x02\x03\x11\x04\x05!1\x06\x12AQ\x07aq\x13"2\x81\x08\x14B\x91\xa1\xb1\xc1\t#3R\xf0\x15br\xd1\n\x16$4\xe1%\xf1\x17\x18\x19\x1a&\'()*56789:CDEFGHIJSTUVWXYZcdefghijstuvwxyz\x82\x83\x84\x85\x86\x87\x88\x89\x8a\x92\x93\x94\x95\x96\x97\x98\x99\x9a\xa2\xa3\xa4\xa5\xa6\xa7\xa8\xa9\xaa\xb2\xb3\xb4\xb5\xb6\xb7\xb8\xb9\xba\xc2\xc3\xc4\xc5\xc6\xc7\xc8\xc9\xca\xd2\xd3\xd4\xd5\xd6\xd7\xd8\xd9\xda\xe2\xe3\xe4\xe5\xe6\xe7\xe8\xe9\xea\xf2\xf3\xf4\xf5\xf6\xf7\xf8\xf9\xfa\xff\xda\x00\x0c\x03\x01\x00\x02\x11\x03\x11\x00?\x00\xfd\xfc\xa8\xa2\x8a\xfc\xff\xd9'


@pytest.mark.asyncio
async def test_m4_repositories(setup_db):
    user_id = 999
    
    # Insert dummy user to satisfy foreign keys
    async with get_db() as db:
        await db.execute("INSERT INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.commit()
    
    # 1. Create a record
    code = generate_coded_name(".jpg")
    record_id = await create_record(
        user_id=user_id,
        category="vehicle",
        title="RC Book",
        code=code,
        notes="My car RC"
    )
    
    # Verify DB
    async with get_db() as db:
        async with db.execute("SELECT * FROM records WHERE id = ?", (record_id,)) as cur:
            row = await cur.fetchone()
            assert row["title"] == "RC Book"
            assert row["status"] == "active"
            
    # 2. Add a file
    file_id = await add_file(record_id, user_id, "drive_id_123", code, "image/jpeg", 50000)
    async with get_db() as db:
        async with db.execute("SELECT size_bytes FROM files WHERE id = ?", (file_id,)) as cur:
            row = await cur.fetchone()
            assert row["size_bytes"] == 50000
            
    # 3. Soft delete
    await soft_delete_record(user_id, record_id)
    async with get_db() as db:
        async with db.execute("SELECT status FROM records WHERE id = ?", (record_id,)) as cur:
            row = await cur.fetchone()
            assert row["status"] == "soft_deleted"


def test_m4_image_processor():
    from PIL import Image
    import io
    img = Image.new("RGB", (1000, 1000), color="red")
    out = io.BytesIO()
    img.save(out, format="JPEG")
    img_data = out.getvalue()
    
    processed = process_image(img_data, max_size=100)
    assert processed.startswith(b'\xff\xd8')
    assert len(processed) > 0


@pytest.mark.asyncio
async def test_m4_rebuild(setup_db):
    from squares_vault.drive.sync import rebuild_from_manifest
    import json
    
    user_id = 888
    async with get_db() as db:
        await db.execute("INSERT INTO users (id, chat_id) VALUES (?, ?)", (user_id, user_id))
        await db.commit()
        
    manifest = [
        json.dumps({
            "action": "INSERT", "collection": "records",
            "data": {
                "id": "01H123", "user_id": user_id, "category": "vehicle",
                "title": "Bike RC", "code": "ABCDEFG", "amount_minor": None,
                "currency": "INR", "notes": None, "tags": None, "extra_json": None,
                "status": "active", "created_at": "2024-01-01T12:00:00Z", "updated_at": "2024-01-01T12:00:00Z"
            }
        })
    ]
    
    await rebuild_from_manifest(user_id, manifest)
    
    async with get_db() as db:
        async with db.execute("SELECT * FROM records WHERE id = '01H123'") as cur:
            row = await cur.fetchone()
            assert row["title"] == "Bike RC"

