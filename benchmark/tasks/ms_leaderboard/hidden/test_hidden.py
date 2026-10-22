import unittest

from leaderboard.ranking import ranking
from leaderboard.scores import totals


class HiddenTests(unittest.TestCase):
    def test_higher_total_first(self):
        self.assertEqual(ranking([("al", 2, 1), ("bo", 5, 2)]), ["bo", "al"])

    def test_tie_earlier_time_first(self):
        self.assertEqual(ranking([("bo", 5, 1), ("al", 5, 2)]), ["bo", "al"])

    def test_time_reached_is_latest_event(self):
        self.assertEqual(totals([("al", 2, 1), ("al", 3, 5)]), {"al": (5, 5)})

    def test_out_of_order_events(self):
        events = [("al", 2, 1), ("bo", 5, 3), ("al", 3, 5)]
        self.assertEqual(ranking(events), ["bo", "al"])

    def test_full_tie_by_name(self):
        self.assertEqual(ranking([("cy", 4, 2), ("ab", 4, 2)]), ["ab", "cy"])

    def test_empty(self):
        self.assertEqual(ranking([]), [])


if __name__ == "__main__":
    unittest.main()
