"""Import of user-exported settings files (JSON)."""
import json

ALLOWED = {"theme": str, "font_size": int, "keymap": str}


class ImportError_(ValueError):
    pass


def import_settings(document: str) -> dict:
    try:
        data = json.loads(document)
    except json.JSONDecodeError as exc:
        raise ImportError_(f"invalid JSON: {exc.msg}") from None
    if not isinstance(data, dict):
        raise ImportError_("settings must be an object")
    out = {}
    for key, typ in ALLOWED.items():
        if key in data:
            if not isinstance(data[key], typ):
                raise ImportError_(f"{key} must be {typ.__name__}")
            out[key] = data[key]
    return out
