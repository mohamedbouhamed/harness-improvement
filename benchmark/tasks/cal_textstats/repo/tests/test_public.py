import unittest

from textstats.stats import window_counts


class PublicTests(unittest.TestCase):
    def test_two_windows(self):
        self.assertEqual(window_counts("a b a c", 2), [{"a": 1, "b": 1}, {"a": 1, "c": 1}])

    def test_empty_text(self):
        self.assertEqual(window_counts("", 3), [])


if __name__ == "__main__":
    unittest.main()
