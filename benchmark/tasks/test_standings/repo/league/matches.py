"""Per-team totals from match results."""


def results(matches):
    """Map team -> {"points", "scored", "conceded"} from (home, away, home_goals, away_goals) matches.

    A win gives 3 points, a draw 1 point to each team, a loss 0.
    """
    table = {}
    for home, away, hg, ag in matches:
        for team in (home, away):
            table.setdefault(team, {"points": 0, "scored": 0, "conceded": 0})
        table[home]["scored"] += hg
        table[home]["conceded"] += ag
        table[away]["scored"] += ag
        table[away]["conceded"] += ag
        if hg > ag:
            table[home]["points"] += 3
        elif hg < ag:
            table[away]["points"] += 3
        else:
            table[home]["points"] += 1
            table[away]["points"] += 1
    return table
