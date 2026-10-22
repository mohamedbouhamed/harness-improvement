"""Half-open time intervals (start, end) in integer minutes."""


def overlaps(a, b):
    """True if half-open intervals a and b share at least one minute."""
    return a[0] < b[1] and b[0] < a[1]
