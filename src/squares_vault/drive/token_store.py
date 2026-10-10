"""Store and retrieve encrypted OAuth tokens from the database.

Uses AES-256-GCM envelope encryption (crypto.py) to protect tokens at rest.
"""

from __future__ import annotations

import json
from typing import Optional, Tuple

from squares_vault import config
from squares_vault.db.connection import get_db
from squares_vault.security.crypto import encrypt_secret, decrypt_secret, load_master_key

# We load the master key into memory once
_master_key: Optional[bytes] = None


def _get_master_key() -> bytes:
    global _master_key
    if _master_key is None:
        _master_key = load_master_key(config.settings.master_key_path)
    return _master_key


async def save_token(user_id: int, access_token: str, refresh_token: str, expires_in: int) -> None:
    """Encrypt and save a user's tokens to the DB."""
    # We serialize both into a JSON string to encrypt as a single blob
    payload = json.dumps({
        "access_token": access_token,
        "refresh_token": refresh_token
    }).encode("utf-8")
    
    ct, dek = encrypt_secret(_get_master_key(), payload)
    
    async with get_db() as db:
        await db.execute(
            """
            INSERT INTO oauth_tokens (user_id, refresh_token_enc, dek_wrapped)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                refresh_token_enc = excluded.refresh_token_enc,
                dek_wrapped = excluded.dek_wrapped,
                last_refreshed_at = CURRENT_TIMESTAMP
            """,
            (user_id, ct, dek)
        )
        # S4: Change user status if they were pending
        await db.execute("UPDATE users SET status = 'active' WHERE id = ?", (user_id,))
        await db.commit()


async def get_token(user_id: int) -> Optional[Tuple[str, str]]:
    """Retrieve and decrypt a user's tokens. Returns (access_token, refresh_token)."""
    async with get_db() as db:
        async with db.execute("SELECT refresh_token_enc, dek_wrapped FROM oauth_tokens WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            
    if not row:
        return None
        
    try:
        pt = decrypt_secret(_get_master_key(), row["refresh_token_enc"], row["dek_wrapped"])
        data = json.loads(pt.decode("utf-8"))
        return data["access_token"], data["refresh_token"]
    except Exception:
        return None


async def delete_token(user_id: int) -> None:
    """Delete a user's tokens from the DB."""
    async with get_db() as db:
        await db.execute("DELETE FROM oauth_tokens WHERE user_id = ?", (user_id,))
        await db.execute("UPDATE users SET status = 'needs_reconnect' WHERE id = ?", (user_id,))
        await db.commit()
