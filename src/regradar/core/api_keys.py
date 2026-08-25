"""Shared API key generation and hashing — used by both key issuance (`cli.py`)
and verification (`api/deps.py`) so the two can never drift out of sync.

API keys are high-entropy random strings, not low-entropy user passwords, so
they're hashed with a fast deterministic digest (SHA-256) rather than a slow
salted one (bcrypt/argon2) — the same choice Stripe and GitHub make for API
keys. This is what makes an indexed `WHERE key_hash = ?` lookup possible at
verification time; a salted hash would require iterating and checking every
active key's hash per request instead.
"""

import hashlib
import secrets

_KEY_PREFIX = "rr_"


def generate_api_key() -> str:
    """Generate a new, high-entropy, plaintext API key.

    The returned value is shown to the caller exactly once and is never
    stored — only its hash (see `hash_api_key`) is persisted.
    """
    return _KEY_PREFIX + secrets.token_urlsafe(32)


def hash_api_key(raw: str) -> str:
    """Deterministically hash a raw API key for storage/lookup."""
    return hashlib.sha256(raw.encode()).hexdigest()
