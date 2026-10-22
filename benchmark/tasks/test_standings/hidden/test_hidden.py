import unittest

from league.matches import results
from league.table import standings


class HiddenTests(unittest.TestCase):
    def test_points_then_goal_difference(self):
        matches = [("A", "C", 2, 0), ("B", "D", 4, 3)]
        self.assertEqual(standings(matches), ["A", "B", "D", "C"])

    def test_draw(self):
        self.assertEqual(standings([("X", "Y", 1, 1)]), ["X", "Y"])

    def test_away_win_goal_difference(self):
        matches = [("X", "Y", 0, 2), ("Z", "W", 1, 2)]
        self.assertEqual(standings(matches), ["Y", "W", "Z", "X"])

    def test_conceded_for_away_team(self):
        self.assertEqual(results([("H", "A", 3, 1)])["A"], {"points": 0, "scored": 1, "conceded": 3})

    def test_goals_scored_breaks_equal_difference(self):
        matches = [("P", "Q", 3, 2), ("R", "S", 1, 0)]
        self.assertEqual(standings(matches), ["P", "R", "Q", "S"])

    def test_name_breaks_full_tie(self):
        self.assertEqual(standings([("b", "a", 0, 0)]), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
