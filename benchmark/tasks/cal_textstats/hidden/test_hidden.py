import unittest

from textstats.stats import window_counts
from textstats.tokenize import chunks, words


class HiddenTests(unittest.TestCase):
    def test_two_windows(self):
        self.assertEqual(window_counts("a b a c", 2), [{"a": 1, "b": 1}, {"a": 1, "c": 1}])

    def test_empty_text(self):
        self.assertEqual(window_counts("", 3), [])

    def test_last_window_shorter(self):
        self.assertEqual(window_counts("x y z", 2), [{"x": 1, "y": 1}, {"z": 1}])

    def test_case_insensitive(self):
        self.assertEqual(window_counts("The the THE", 5), [{"the": 3}])

    def test_words_lower_case(self):
        self.assertEqual(words("Don't STOP"), ["don't", "stop"])

    def test_chunks_exact_and_short(self):
        self.assertEqual(chunks([1, 2, 3, 4], 2), [[1, 2], [3, 4]])
        self.assertEqual(chunks([1], 3), [[1]])

    def test_bad_size(self):
        with self.assertRaises(ValueError):
            window_counts("a", 0)


if __name__ == "__main__":
    unittest.main()
