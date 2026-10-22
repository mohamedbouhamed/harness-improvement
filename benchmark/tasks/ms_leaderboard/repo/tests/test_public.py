import unittest

from leaderboard.ranking import ranking


class PublicTests(unittest.TestCase):
    def test_higher_total_first(self):
        self.assertEqual(ranking([("al", 2, 1), ("bo", 5, 2)]), ["bo", "al"])

    def test_tie_earlier_time_first(self):
        self.assertEqual(ranking([("bo", 5, 1), ("al", 5, 2)]), ["bo", "al"])


if __name__ == "__main__":
    unittest.main()
