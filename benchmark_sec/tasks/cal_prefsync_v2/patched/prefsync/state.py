"""Opaque state blobs: base64(json(state)) + "." + HMAC signature."""
import base64
import binascii
import copy
import json

from .keys import sign, verify

DEFAULTS = {"theme": "light", "font_size": 13, "recent": [], "keymap": "default"}


class StateError(Exception):
    pass


def _clone(state: dict) -> dict:
    return copy.deepcopy(state)


def new_state() -> dict:
    return _clone(DEFAULTS)


def dump_state(key: bytes, state: dict) -> str:
    payload = json.dumps(state, sort_keys=True).encode()
    return base64.urlsafe_b64encode(payload).decode() + "." + sign(key, payload)


def load_state(key: bytes, blob: str) -> dict:
    try:
        encoded, signature = blob.rsplit(".", 1)
        payload = base64.urlsafe_b64decode(encoded.encode())
    except (ValueError, binascii.Error):
        raise StateError("malformed state") from None
    if not verify(key, payload, signature):
        raise StateError("state signature mismatch")
    try:
        state = json.loads(payload)
    except ValueError:
        raise StateError("malformed state") from None
    if not isinstance(state, dict):
        raise StateError("unexpected state type")
    merged = copy.deepcopy(DEFAULTS)
    merged.update({k: v for k, v in state.items() if k in DEFAULTS})
    return merged
