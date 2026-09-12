"""Invite code generation — reuses core/api_keys.py's hashing scheme.

An invite code is the same shape of thing as an API key (a high-entropy
random token whose hash alone is stored), so it's hashed the same way
(`hash_api_key`) rather than inventing a second scheme — see that
module's docstring for why a fast SHA-256 digest is correct here and
would not be for a user-chosen password.
"""

import secrets

_CODE_PREFIX = "rrinv_"


def generate_invite_code() -> str:
    """Generate a new, high-entropy, plaintext invite code.

    Shown to the Admin who creates it exactly once and never stored —
    only its hash (via `core.api_keys.hash_api_key`) is persisted.
    """
    return _CODE_PREFIX + secrets.token_urlsafe(16)
