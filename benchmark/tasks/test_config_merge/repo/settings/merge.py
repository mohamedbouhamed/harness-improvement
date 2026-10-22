"""Recursive dictionary merging."""


def deep_merge(base, override):
    """Return a new dict with the keys of both inputs.

    Where both values are dicts they are merged recursively; otherwise the override value wins.
    Neither input is modified, and the result shares no dicts or lists with the inputs.
    """
    result = base
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
