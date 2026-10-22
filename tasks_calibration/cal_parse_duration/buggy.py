UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}


def parse_duration(text):
    """Convert a compact duration such as '1h30m' to seconds."""
    total = 0
    number = ""
    for ch in text:
        if ch.isdigit():
            number += ch
        elif ch in UNITS:
            if not number:
                raise ValueError(f"missing number before {ch!r}")
            total += int(number) * UNITS[ch]
            number = ""
        else:
            raise ValueError(f"unexpected character {ch!r}")
    return total
