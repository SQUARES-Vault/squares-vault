"""Simple JSON-based i18n loader."""

from __future__ import annotations

import json
from pathlib import Path

_LOCALE_DIR = Path(__file__).parent
_strings: dict[str, dict[str, str]] = {}


def load_strings() -> None:
    """Load all .json files from the i18n directory."""
    _strings.clear()
    for p in _LOCALE_DIR.glob("*.json"):
        locale = p.stem
        with p.open("r", encoding="utf-8") as f:
            _strings[locale] = json.load(f)


def t(key: str, locale: str = "en", **kwargs: str | int) -> str:
    """Get a translated string by key.

    If the string contains {vars}, they are formatted using kwargs.
    """
    if not _strings:
        load_strings()
    
    # Fallback to English if the locale doesn't exist
    pool = _strings.get(locale, _strings.get("en", {}))
    text = pool.get(key, f"Missing string: {key}")
    
    if kwargs:
        return text.format(**kwargs)
    return text
