"""Google Drive asynchronous client using aiohttp.

Implements the Device Authorization flow (TVs and Limited Input devices).
"""

from __future__ import annotations

import asyncio
from typing import Dict, Any, Optional

import aiohttp
from pydantic import BaseModel

from squares_vault import config
from squares_vault.logging_setup import get_logger

log = get_logger(__name__)

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"


class DeviceFlowInfo(BaseModel):
    device_code: str
    user_code: str
    verification_url: str
    expires_in: int
    interval: int


class TokenInfo(BaseModel):
    access_token: str
    refresh_token: Optional[str]
    expires_in: int
    token_type: str


async def start_device_flow() -> DeviceFlowInfo:
    """Step 1: Request a device code and user code."""
    async with aiohttp.ClientSession() as session:
        data = {
            "client_id": config.settings.google_client_id,
            "scope": "https://www.googleapis.com/auth/drive.file",
        }
        async with session.post(DEVICE_CODE_URL, data=data) as resp:
            resp.raise_for_status()
            res = await resp.json()
            return DeviceFlowInfo(**res)


async def poll_for_token(device_code: str, interval: int, max_attempts: int = 60) -> Optional[TokenInfo]:
    """Step 2: Poll Google to see if the user entered the code."""
    async with aiohttp.ClientSession() as session:
        data = {
            "client_id": config.settings.google_client_id,
            "client_secret": config.settings.google_client_secret,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }
        
        for _ in range(max_attempts):
            await asyncio.sleep(interval)
            async with session.post(TOKEN_URL, data=data) as resp:
                if resp.status == 200:
                    res = await resp.json()
                    return TokenInfo(**res)
                
                res = await resp.json()
                if res.get("error") == "authorization_pending":
                    continue
                elif res.get("error") == "slow_down":
                    interval += 2
                    continue
                else:
                    # e.g., expired_token, access_denied
                    log.warning("device_flow_error", error=res.get("error"))
                    return None
                    
    return None


async def refresh_access_token(refresh_token: str) -> Optional[TokenInfo]:
    """Refresh the OAuth access token."""
    async with aiohttp.ClientSession() as session:
        data = {
            "client_id": config.settings.google_client_id,
            "client_secret": config.settings.google_client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        async with session.post(TOKEN_URL, data=data) as resp:
            if resp.status == 200:
                res = await resp.json()
                # Google doesn't always return a new refresh token on refresh
                # If they don't, we just keep using the old one.
                res.setdefault("refresh_token", refresh_token)
                return TokenInfo(**res)
            
            log.warning("token_refresh_failed", status=resp.status, text=await resp.text())
            return None
