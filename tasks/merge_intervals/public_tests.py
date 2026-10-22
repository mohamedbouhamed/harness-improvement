import unittest

from candidate import merge_intervals


class PublicTests(unittest.TestCase):
    def test_disjoint_unsorted(self):
        self.assertEqual(merge_intervals([(5, 6), (1, 2)]), [(1, 2), (5, 6)])

    def test_overlapping(self):
        self.assertEqual(merge_intervals([(1, 4), (2, 3), (6, 8), (7, 9)]), [(1, 4), (6, 9)])

    def test_touching_endpoints_merge(self):
        self.assertEqual(merge_intervals([(1, 3), (3, 5)]), [(1, 5)])


if __name__ == "__main__":
    unittest.main()
