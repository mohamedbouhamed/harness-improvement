"""League table ordering."""
from .matches import results


def standings(matches):
    """Team names: most points first, then goal difference, then goals scored (all descending), then name."""
    table = results(matches)
    return sorted(table, key=lambda t: (-table[t]["points"], -table[t]["scored"], t))
