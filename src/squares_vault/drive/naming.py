"""Generate privacy-preserving coded names for files."""

import base64
import os


def generate_coded_name(original_ext: str) -> str:
    """Generate a random base32 name like 'A7B2C9... .pdf'.
    
    Returns exactly 16 chars of base32 + the extension.
    """
    raw = os.urandom(10)
    # 10 bytes = 16 base32 chars
    b32 = base64.b32encode(raw).decode('ascii').upper()
    return f"{b32}{original_ext.lower()}"
