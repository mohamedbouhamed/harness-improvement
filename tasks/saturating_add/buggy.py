INT32_MIN = -2**31
INT32_MAX = 2**31


def saturating_add(a, b):
    """Add two signed 32-bit integers, clamping the result to the int32 range."""
    total = a + b
    if total > INT32_MAX:
        return INT32_MAX
    if total < INT32_MIN:
        return INT32_MIN
    return total
