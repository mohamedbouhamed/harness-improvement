"""Aggregating scoring events per player."""


def totals(events):
    """Map each player to (total_points, time_reached) from (player, points, time) events.

    `time_reached` is the time of the player's latest event. Events may arrive in any order.
    """
    result = {}
    for player, points, time in events:
        total, reached = result.get(player, (0, time))
        result[player] = (total + points, reached)
    return result
