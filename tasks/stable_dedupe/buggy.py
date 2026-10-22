def stable_dedupe(items, key=None):
    """Remove duplicates by key, keeping the first element for each key."""
    seen = {}
    for item in items:
        k = item if key is None else key(item)
        seen[k] = item
    return list(seen.values())
