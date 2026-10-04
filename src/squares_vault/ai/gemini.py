"""Gemini API implementation using aiohttp or httpx."""

from __future__ import annotations

import json
import base64
import aiohttp
from typing import Dict, Any

from squares_vault import config
from squares_vault.logging_setup import get_logger
from squares_vault.ai.schema import ExtractedRecord
from squares_vault.ai.validate import validate_and_enrich

log = get_logger(__name__)

SYSTEM_PROMPT = """You are a highly secure document extraction engine for SQUARES Vault.
Your job is to read user-provided documents (images or PDFs) and extract structured data matching the provided JSON schema.
- Do NOT hallucinate data. If a field is missing, leave it null.
- Be precise with dates (YYYY-MM-DD).
- Classify the document accurately into one of the allowed categories.
- Extract any numbers accurately.
"""


class AIProviderError(Exception):
    pass


async def _get_api_key(user_id: int) -> str:
    from squares_vault.db.connection import get_db
    from squares_vault.security.crypto import decrypt_key
    
    async with get_db() as db:
        async with db.execute("SELECT api_key_enc, dek_wrapped FROM user_keys WHERE user_id = ?", (user_id,)) as cur:
            row = await cur.fetchone()
            if row:
                return decrypt_key(row["api_key_enc"], row["dek_wrapped"]).decode('utf-8')
    return config.settings.gemini_api_key

async def extract_document_data(user_id: int, file_bytes: bytes, mime_type: str) -> ExtractedRecord:
    """Send document to Gemini and parse the structured output."""
    model = config.settings.ai_model_name
    api_key = await _get_api_key(user_id)
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    
    # Gemini requires base64 inline data
    b64_data = base64.b64encode(file_bytes).decode('utf-8')
    
    # For structured output in Gemini v1beta, we can enforce JSON mode.
    # To keep it simple and robust, we ask for JSON in the prompt and parse it.
    payload = {
        "system_instruction": {
            "parts": [{"text": SYSTEM_PROMPT}]
        },
        "contents": [{
            "parts": [
                {"text": "Extract the details from this document and return a valid JSON object matching the schema."},
                {"inline_data": {"mime_type": mime_type, "data": b64_data}}
            ]
        }],
        "generationConfig": {
            "response_mime_type": "application/json",
            "temperature": 0.0
        }
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload) as resp:
            if resp.status != 200:
                text = await resp.text()
                log.error("gemini_api_error", status=resp.status, text=text)
                raise AIProviderError(f"Gemini API returned {resp.status}")
                
            data = await resp.json()
            
            try:
                # Parse response
                content_text = data["candidates"][0]["content"]["parts"][0]["text"]
                parsed_json = json.loads(content_text)
                
                # Validate with Pydantic
                record = ExtractedRecord(**parsed_json)
                
                # Post-validation rules
                record = validate_and_enrich(record)
                
                return record
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                log.error("gemini_parse_error", error=str(e), response=data)
                raise AIProviderError("Failed to parse Gemini output.")
