import unittest

from candidate import top_k_words


class PublicTests(unittest.TestCase):
    def test_distinct_counts(self):
        self.assertEqual(top_k_words(["a", "b", "a", "c", "a", "b"], 2), ["a", "b"])

    def test_ties_alphabetical(self):
        self.assertEqual(top_k_words(["pear", "apple", "pear", "apple", "fig"], 2), ["apple", "pear"])

    def test_k_zero(self):
        self.assertEqual(top_k_words(["a", "b"], 0), [])


if __name__ == "__main__":
    unittest.main()
