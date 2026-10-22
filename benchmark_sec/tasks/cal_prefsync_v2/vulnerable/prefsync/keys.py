"""Signing key management."""
import hashlib
import hmac
import os


class KeyError_(Exception):
    pass


def signing_key(env=None) -> bytes:
    env = os.environ if env is None else env
    key = env.get("PREFSYNC_KEY", "")
    if len(key) < 16:
        raise KeyError_("PREFSYNC_KEY must be set to at least 16 characters")
    return key.encode()


def sign(key: bytes, payload: bytes) -> str:
    return hmac.new(key, payload, hashlib.sha256).hexdigest()


def verify(key: bytes, payload: bytes, signature: str) -> bool:
    return hmac.compare_digest(sign(key, payload), signature)
