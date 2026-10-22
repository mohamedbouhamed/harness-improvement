def stable_dedupe(items, key=None):
    """Remove duplicates by key, keeping the first element for each key."""
    seen = set()
    result = []
    for item in items:
        k = item if key is None else key(item)
        if k not in seen:
            seen.add(k)
            result.append(item)
    return result
