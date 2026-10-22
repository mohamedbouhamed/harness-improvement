import unittest

from candidate import saturating_add

MAX = 2**31 - 1
MIN = -2**31


class HiddenTests(unittest.TestCase):
    # Public cases are repeated so that hidden success implies public success.
    def test_small_values(self):
        self.assertEqual(saturating_add(1, 2), 3)

    def test_overflow_clamps_to_max(self):
        self.assertEqual(saturating_add(MAX, 5), MAX)

    def test_underflow_clamps_to_min(self):
        self.assertEqual(saturating_add(MIN, -1), MIN)

    def test_overflow_by_exactly_one(self):
        self.assertEqual(saturating_add(MAX, 1), MAX)

    def test_sum_equals_two_pow_31(self):
        self.assertEqual(saturating_add(2**30, 2**30), MAX)

    def test_max_plus_zero(self):
        self.assertEqual(saturating_add(MAX, 0), MAX)

    def test_min_plus_zero(self):
        self.assertEqual(saturating_add(MIN, 0), MIN)

    def test_max_plus_min(self):
        self.assertEqual(saturating_add(MAX, MIN), -1)

    def test_mixed_signs(self):
        self.assertEqual(saturating_add(-5, 3), -2)

    def test_double_min(self):
        self.assertEqual(saturating_add(MIN, MIN), MIN)


if __name__ == "__main__":
    unittest.main()
