"""FE-02 — email/password hashing.

Deliberately separate from core/api_keys.py's hash_api_key: that function
uses fast, unsalted SHA-256, which is correct for a high-entropy random
API key (an attacker who steals the hash still can't feasibly brute-force
the original 256-bit token) but would be a real vulnerability for a
user-chosen password (low entropy, brute-forceable at SHA-256 speeds).
bcrypt is slow and salted by design — the actual security requirement
here, not an interchangeable implementation detail.
"""

import bcrypt

_MIN_PASSWORD_LENGTH = 8


class WeakPasswordError(ValueError):
    """Raised by hash_password for a password that fails the minimum bar."""


def hash_password(raw: str) -> str:
    if len(raw) < _MIN_PASSWORD_LENGTH:
        raise WeakPasswordError(f"Password must be at least {_MIN_PASSWORD_LENGTH} characters")
    return bcrypt.hashpw(raw.encode(), bcrypt.gensalt()).decode()


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(raw.encode(), hashed.encode())
    except ValueError:
        # A malformed/corrupt stored hash must fail closed, not raise past
        # the caller into an unhandled 500.
        return False
