import unittest

from candidate import merge_intervals


class HiddenTests(unittest.TestCase):
    # Public cases are repeated so that hidden success implies public success.
    def test_disjoint_unsorted(self):
        self.assertEqual(merge_intervals([(5, 6), (1, 2)]), [(1, 2), (5, 6)])

    def test_overlapping(self):
        self.assertEqual(merge_intervals([(1, 4), (2, 3), (6, 8), (7, 9)]), [(1, 4), (6, 9)])

    def test_touching_endpoints_merge(self):
        self.assertEqual(merge_intervals([(1, 3), (3, 5)]), [(1, 5)])

    def test_empty(self):
        self.assertEqual(merge_intervals([]), [])

    def test_adjacent_integers_do_not_merge(self):
        self.assertEqual(merge_intervals([(3, 4), (1, 2)]), [(1, 2), (3, 4)])

    def test_touching_chain_unsorted(self):
        self.assertEqual(merge_intervals([(7, 9), (1, 3), (5, 7), (3, 5)]), [(1, 9)])

    def test_point_intervals(self):
        self.assertEqual(merge_intervals([(2, 2), (2, 2), (0, 1)]), [(0, 1), (2, 2)])

    def test_containment_then_touch(self):
        self.assertEqual(merge_intervals([(1, 10), (2, 3), (10, 12)]), [(1, 12)])

    def test_input_not_modified(self):
        data = [(3, 4), (1, 2), (2, 3)]
        snapshot = list(data)
        merge_intervals(data)
        self.assertEqual(data, snapshot)


if __name__ == "__main__":
    unittest.main()
