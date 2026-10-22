"""Finding free slots in a day of bookings."""
from .intervals import overlaps


def is_free(bookings, start, end):
    """True if [start, end) overlaps no booking."""
    return not any(overlaps((start, end), b) for b in bookings)


def first_free_slot(bookings, day_start, day_end, duration, step=15):
    """Earliest start = day_start + k*step whose slot [start, start + duration) fits the day and is free."""
    for start in range(day_start, day_end - duration, step):
        if is_free(bookings, start, start + duration):
            return start
    return None
