UNITS = {"d": 86400, "h": 3600, "m": 60, "s": 1}
ORDER = "dhms"


def parse_duration(text):
    """Convert a compact duration such as '1h30m' to seconds."""
    if not text:
        raise ValueError("empty duration")
    total = 0
    number = ""
    last_rank = -1
    for ch in text:
        if ch in "0123456789":
            number += ch
        elif ch in UNITS:
            if not number:
                raise ValueError(f"missing number before {ch!r}")
            rank = ORDER.index(ch)
            if rank <= last_rank:
                raise ValueError(f"unit {ch!r} repeated or out of order")
            last_rank = rank
            total += int(number) * UNITS[ch]
            number = ""
        else:
            raise ValueError(f"unexpected character {ch!r}")
    if number:
        raise ValueError("number without unit")
    return total
