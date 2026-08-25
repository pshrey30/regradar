"""Tests for core.api_keys: key generation and hashing."""

from regradar.core.api_keys import generate_api_key, hash_api_key


def test_generate_api_key_has_prefix():
    key = generate_api_key()
    assert key.startswith("rr_")


def test_generate_api_key_is_high_entropy_and_unique():
    keys = {generate_api_key() for _ in range(100)}
    assert len(keys) == 100


def test_generate_api_key_is_reasonably_long():
    key = generate_api_key()
    # "rr_" + urlsafe_b64(32 random bytes) is well over 40 chars
    assert len(key) > 40


def test_hash_api_key_is_deterministic():
    key = generate_api_key()
    assert hash_api_key(key) == hash_api_key(key)


def test_hash_api_key_differs_for_different_keys():
    assert hash_api_key(generate_api_key()) != hash_api_key(generate_api_key())


def test_hash_api_key_is_sha256_hex_digest():
    import hashlib

    key = "rr_known-test-value"
    expected = hashlib.sha256(key.encode()).hexdigest()
    assert hash_api_key(key) == expected


def test_hash_api_key_never_returns_the_raw_key():
    key = generate_api_key()
    assert hash_api_key(key) != key
