"""Ranking players by score."""
from .scores import totals


def ranking(events):
    """Player names, best first: higher total, then earlier time_reached, then name."""
    table = totals(events)
    return sorted(table, key=lambda p: (-table[p][0], table[p][1], p))
