"""Counting events inside a trailing time window."""


def count_in_window(timestamps, now, size):
    """Number of timestamps t with now - size < t <= now (the window excludes its start)."""
    return sum(1 for t in timestamps if now - size <= t <= now)
