import unittest

from league.table import standings


class PublicTests(unittest.TestCase):
    def test_points_then_goal_difference(self):
        matches = [("A", "C", 2, 0), ("B", "D", 4, 3)]
        self.assertEqual(standings(matches), ["A", "B", "D", "C"])

    def test_draw(self):
        self.assertEqual(standings([("X", "Y", 1, 1)]), ["X", "Y"])


if __name__ == "__main__":
    unittest.main()
