"""League table ordering."""
from .matches import results


def standings(matches):
    """Team names: most points first, then goal difference, then goals scored (all descending), then name."""
    table = results(matches)

    def key(team):
        row = table[team]
        return (-row["points"], row["scored"] - row["conceded"], -row["scored"], team)

    return sorted(table, key=key)
