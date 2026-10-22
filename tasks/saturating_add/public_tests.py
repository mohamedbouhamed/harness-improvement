import unittest

from candidate import saturating_add


class PublicTests(unittest.TestCase):
    def test_small_values(self):
        self.assertEqual(saturating_add(1, 2), 3)

    def test_overflow_clamps_to_max(self):
        self.assertEqual(saturating_add(2**31 - 1, 5), 2**31 - 1)

    def test_underflow_clamps_to_min(self):
        self.assertEqual(saturating_add(-2**31, -1), -2**31)


if __name__ == "__main__":
    unittest.main()
