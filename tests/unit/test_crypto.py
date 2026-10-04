"""Unit tests for security/crypto.py – AES-256-GCM envelope encryption.

Acceptance criteria (M0):
- Round-trip: encrypt → decrypt returns the original plaintext.
- Wrong master key raises InvalidTag (tamper detected).
- Tampered ciphertext raises InvalidTag.
- Each call to encrypt_secret produces a different ciphertext (nonce randomness).
- load_master_key rejects wrong-length files.
- generate_master_key returns 32 bytes.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from squares_vault.security.crypto import (
    decrypt_secret,
    encrypt_secret,
    generate_master_key,
    load_master_key,
)


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def master_key() -> bytes:
    return os.urandom(32)


@pytest.fixture()
def alt_master_key() -> bytes:
    return os.urandom(32)


# ── Round-trip tests ───────────────────────────────────────────────────────────


def test_roundtrip_short(master_key: bytes) -> None:
    """Encrypting and decrypting a short UTF-8 token returns the original."""
    plaintext = b"ya29.A0ARrdaM-some-google-token"
    ct_blob, dek_blob = encrypt_secret(master_key, plaintext)
    assert decrypt_secret(master_key, ct_blob, dek_blob) == plaintext


def test_roundtrip_long(master_key: bytes) -> None:
    """Works on a 4 KB payload (simulating a large token string)."""
    plaintext = os.urandom(4096)
    ct_blob, dek_blob = encrypt_secret(master_key, plaintext)
    assert decrypt_secret(master_key, ct_blob, dek_blob) == plaintext


def test_roundtrip_empty(master_key: bytes) -> None:
    """Empty plaintext is a valid edge case."""
    ct_blob, dek_blob = encrypt_secret(master_key, b"")
    assert decrypt_secret(master_key, ct_blob, dek_blob) == b""


def test_roundtrip_unicode(master_key: bytes) -> None:
    """UTF-8 encoded string with non-ASCII characters."""
    plaintext = "शिखर सिंह पटेल".encode()
    ct_blob, dek_blob = encrypt_secret(master_key, plaintext)
    assert decrypt_secret(master_key, ct_blob, dek_blob) == plaintext


# ── Nonce randomness ───────────────────────────────────────────────────────────


def test_unique_ciphertexts(master_key: bytes) -> None:
    """Two encryptions of the same plaintext produce different ciphertexts."""
    pt = b"same-token"
    ct1, dek1 = encrypt_secret(master_key, pt)
    ct2, dek2 = encrypt_secret(master_key, pt)
    assert ct1 != ct2, "Ciphertexts must differ due to random nonces"
    assert dek1 != dek2, "Wrapped DEKs must differ due to random DEKs and nonces"


# ── Authentication failures ────────────────────────────────────────────────────


def test_wrong_master_key_raises(master_key: bytes, alt_master_key: bytes) -> None:
    """Decrypting with the wrong master key must raise InvalidTag."""
    pt = b"secret-token"
    ct_blob, dek_blob = encrypt_secret(master_key, pt)
    with pytest.raises(InvalidTag):
        decrypt_secret(alt_master_key, ct_blob, dek_blob)


def test_tampered_ciphertext_raises(master_key: bytes) -> None:
    """Flipping a byte in the ciphertext blob must raise InvalidTag."""
    pt = b"secret-token"
    ct_blob, dek_blob = encrypt_secret(master_key, pt)
    # Flip the last byte of the ciphertext
    tampered = bytearray(ct_blob)
    tampered[-1] ^= 0xFF
    with pytest.raises(InvalidTag):
        decrypt_secret(master_key, bytes(tampered), dek_blob)


def test_tampered_dek_raises(master_key: bytes) -> None:
    """Flipping a byte in the DEK blob must raise InvalidTag."""
    pt = b"secret-token"
    ct_blob, dek_blob = encrypt_secret(master_key, pt)
    tampered_dek = bytearray(dek_blob)
    tampered_dek[-1] ^= 0xFF
    with pytest.raises(InvalidTag):
        decrypt_secret(master_key, ct_blob, bytes(tampered_dek))


def test_truncated_ciphertext_raises(master_key: bytes) -> None:
    """A truncated ciphertext blob must raise ValueError."""
    ct_blob, dek_blob = encrypt_secret(master_key, b"token")
    with pytest.raises((ValueError, InvalidTag)):
        decrypt_secret(master_key, ct_blob[:5], dek_blob)


def test_truncated_dek_raises(master_key: bytes) -> None:
    """A truncated DEK blob must raise ValueError."""
    ct_blob, dek_blob = encrypt_secret(master_key, b"token")
    with pytest.raises((ValueError, InvalidTag)):
        decrypt_secret(master_key, ct_blob, dek_blob[:5])


# ── Master key I/O ─────────────────────────────────────────────────────────────


def test_load_master_key_valid() -> None:
    """load_master_key reads a 32-byte file correctly."""
    key = os.urandom(32)
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(key)
        path = Path(f.name)
    try:
        loaded = load_master_key(path)
        assert loaded == key
    finally:
        path.unlink(missing_ok=True)


def test_load_master_key_wrong_length() -> None:
    """load_master_key raises ValueError for a file with wrong byte count."""
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(b"\x00" * 16)  # 16 bytes – wrong
        path = Path(f.name)
    try:
        with pytest.raises(ValueError, match="32 bytes"):
            load_master_key(path)
    finally:
        path.unlink(missing_ok=True)


def test_load_master_key_missing() -> None:
    """load_master_key raises FileNotFoundError for a missing file."""
    with pytest.raises(FileNotFoundError):
        load_master_key(Path("/nonexistent/path/master.key"))


# ── Key generation ─────────────────────────────────────────────────────────────


def test_generate_master_key_length() -> None:
    """generate_master_key returns exactly 32 random bytes."""
    key = generate_master_key()
    assert len(key) == 32


def test_generate_master_key_random() -> None:
    """Two generated keys are extremely unlikely to be equal."""
    k1 = generate_master_key()
    k2 = generate_master_key()
    assert k1 != k2


# ── Wrong key length ───────────────────────────────────────────────────────────


def test_encrypt_with_short_key_raises() -> None:
    """Passing a 16-byte master key must raise ValueError."""
    with pytest.raises(ValueError, match="32"):
        encrypt_secret(b"\x00" * 16, b"plaintext")


def test_decrypt_with_short_key_raises() -> None:
    """Passing a 16-byte master key to decrypt must raise ValueError."""
    mk = os.urandom(32)
    ct, dek = encrypt_secret(mk, b"plaintext")
    with pytest.raises(ValueError, match="32"):
        decrypt_secret(b"\x00" * 16, ct, dek)
