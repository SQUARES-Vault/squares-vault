"""AES-256-GCM envelope encryption for refresh tokens and user API keys.

Decision S1 (LOCKED): each secret gets a fresh 32-byte data-encryption key
(DEK).  The DEK is wrapped (encrypted) by the 32-byte master key that lives
in a root-only file outside the database and outside backups.  Only the
ciphertext + wrapped DEK + nonce are stored in the database.  A leaked
database alone cannot decrypt any secret.

Scheme
------
  master_key (32 B, from file)
  dek         = os.urandom(32)          # fresh per secret
  nonce_dek   = os.urandom(12)
  dek_wrapped = AES256GCM(master_key).encrypt(nonce_dek, dek, aad=b"dek")
  stored_dek  = nonce_dek || dek_wrapped          # 12 + 48 = 60 bytes

  nonce_ct    = os.urandom(12)
  ciphertext  = AES256GCM(dek).encrypt(nonce_ct, plaintext_bytes, aad=b"ct")
  stored_ct   = nonce_ct || ciphertext            # 12 + len(pt) + 16 bytes

On decrypt: unwrap DEK → decrypt ciphertext.

Public API
----------
  encrypt_secret(master_key, plaintext) -> (ciphertext_blob, dek_wrapped_blob)
  decrypt_secret(master_key, ciphertext_blob, dek_wrapped_blob) -> bytes
  load_master_key(path) -> bytes
"""

from __future__ import annotations

import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# Associated-data constants (domain separation)
_AAD_DEK: bytes = b"sv-dek-v1"
_AAD_CT: bytes = b"sv-ct-v1"

_NONCE_LEN: int = 12   # 96-bit nonce as recommended for AES-GCM
_KEY_LEN: int = 32     # AES-256


# ── Public functions ───────────────────────────────────────────────────────────


def load_master_key(path: Path | str) -> bytes:
    """Read and validate the master key from *path*.

    The file must contain exactly 32 raw bytes.  It should be owned by root
    with mode 0440 (root:squares readable) on production.

    Raises
    ------
    FileNotFoundError
        If the key file does not exist.
    ValueError
        If the file does not contain exactly 32 bytes.
    """
    p = Path(path)
    raw = p.read_bytes()
    if len(raw) != _KEY_LEN:
        raise ValueError(
            f"Master key at {p} must be exactly {_KEY_LEN} bytes, got {len(raw)}"
        )
    return raw


def encrypt_secret(master_key: bytes, plaintext: bytes) -> tuple[bytes, bytes]:
    """Encrypt *plaintext* under a fresh DEK wrapped by *master_key*.

    Parameters
    ----------
    master_key:
        32-byte key loaded from the master key file.
    plaintext:
        Raw bytes to encrypt (e.g. a UTF-8 refresh token).

    Returns
    -------
    ciphertext_blob:
        ``nonce(12) || AES-GCM-ciphertext+tag``.  Store in DB column
        ``refresh_token_enc`` / ``api_key_enc``.
    dek_wrapped_blob:
        ``nonce(12) || AES-GCM-wrapped-DEK+tag``.  Store in DB column
        ``dek_wrapped``.
    """
    _validate_master_key(master_key)

    # 1. Generate a fresh DEK
    dek = os.urandom(_KEY_LEN)

    # 2. Wrap the DEK with the master key
    nonce_dek = os.urandom(_NONCE_LEN)
    wrapped_dek = AESGCM(master_key).encrypt(nonce_dek, dek, _AAD_DEK)
    dek_wrapped_blob = nonce_dek + wrapped_dek  # 12 + 48 = 60 bytes

    # 3. Encrypt the plaintext with the DEK
    nonce_ct = os.urandom(_NONCE_LEN)
    ciphertext = AESGCM(dek).encrypt(nonce_ct, plaintext, _AAD_CT)
    ciphertext_blob = nonce_ct + ciphertext  # 12 + len(pt) + 16 bytes

    return ciphertext_blob, dek_wrapped_blob


def decrypt_secret(
    master_key: bytes,
    ciphertext_blob: bytes,
    dek_wrapped_blob: bytes,
) -> bytes:
    """Decrypt a secret encrypted by :func:`encrypt_secret`.

    Parameters
    ----------
    master_key:
        32-byte key loaded from the master key file.
    ciphertext_blob:
        The ``refresh_token_enc`` / ``api_key_enc`` DB value.
    dek_wrapped_blob:
        The ``dek_wrapped`` DB value.

    Returns
    -------
    bytes
        The original plaintext.

    Raises
    ------
    ValueError
        If either blob is too short (truncated / corrupted).
    cryptography.exceptions.InvalidTag
        If authentication fails (wrong key or tampered data).
    """
    _validate_master_key(master_key)

    # 1. Unwrap the DEK
    if len(dek_wrapped_blob) < _NONCE_LEN + 1:
        raise ValueError("dek_wrapped_blob is too short")
    nonce_dek = dek_wrapped_blob[:_NONCE_LEN]
    wrapped_dek = dek_wrapped_blob[_NONCE_LEN:]
    dek = AESGCM(master_key).decrypt(nonce_dek, wrapped_dek, _AAD_DEK)

    # 2. Decrypt the ciphertext
    if len(ciphertext_blob) < _NONCE_LEN + 1:
        raise ValueError("ciphertext_blob is too short")
    nonce_ct = ciphertext_blob[:_NONCE_LEN]
    ciphertext = ciphertext_blob[_NONCE_LEN:]
    return AESGCM(dek).decrypt(nonce_ct, ciphertext, _AAD_CT)


def generate_master_key() -> bytes:
    """Generate a new random 32-byte master key.

    Used by ``install.sh`` to write ``/etc/squares-vault/master.key``.
    Never call this at runtime if a key already exists.
    """
    return os.urandom(_KEY_LEN)


# ── Internal helpers ───────────────────────────────────────────────────────────


def _validate_master_key(key: bytes) -> None:
    if len(key) != _KEY_LEN:
        raise ValueError(f"master_key must be {_KEY_LEN} bytes, got {len(key)}")
