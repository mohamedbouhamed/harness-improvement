import unittest

from candidate import stable_dedupe


class PublicTests(unittest.TestCase):
    def test_integers_keep_first_order(self):
        self.assertEqual(stable_dedupe([3, 1, 3, 2, 1]), [3, 1, 2])

    def test_empty(self):
        self.assertEqual(stable_dedupe([]), [])

    def test_key_keeps_first_element(self):
        words = ["Apple", "apple", "Banana", "BANANA"]
        self.assertEqual(stable_dedupe(words, key=str.lower), ["Apple", "Banana"])


if __name__ == "__main__":
    unittest.main()
