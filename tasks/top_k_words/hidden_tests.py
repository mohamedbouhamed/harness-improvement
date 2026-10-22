import unittest

from candidate import top_k_words


class HiddenTests(unittest.TestCase):
    # Public cases are repeated so that hidden success implies public success.
    def test_distinct_counts(self):
        self.assertEqual(top_k_words(["a", "b", "a", "c", "a", "b"], 2), ["a", "b"])

    def test_ties_alphabetical(self):
        self.assertEqual(top_k_words(["pear", "apple", "pear", "apple", "fig"], 2), ["apple", "pear"])

    def test_k_zero(self):
        self.assertEqual(top_k_words(["a", "b"], 0), [])

    def test_k_exceeds_distinct(self):
        self.assertEqual(top_k_words(["b", "a"], 5), ["a", "b"])

    def test_case_sensitive(self):
        self.assertEqual(top_k_words(["b", "B", "b", "B", "a"], 3), ["B", "b", "a"])

    def test_negative_k(self):
        self.assertEqual(top_k_words(["a", "a"], -1), [])

    def test_empty_words(self):
        self.assertEqual(top_k_words([], 3), [])

    def test_tie_at_cutoff(self):
        self.assertEqual(top_k_words(["d", "c", "b", "a"], 2), ["a", "b"])

    def test_count_dominates_alphabet(self):
        self.assertEqual(top_k_words(["z", "z", "a"], 2), ["z", "a"])


if __name__ == "__main__":
    unittest.main()
