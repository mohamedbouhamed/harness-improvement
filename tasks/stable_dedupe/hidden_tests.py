import unittest

from candidate import stable_dedupe


class HiddenTests(unittest.TestCase):
    # Public cases are repeated so that hidden success implies public success.
    def test_integers_keep_first_order(self):
        self.assertEqual(stable_dedupe([3, 1, 3, 2, 1]), [3, 1, 2])

    def test_empty(self):
        self.assertEqual(stable_dedupe([]), [])

    def test_key_keeps_first_element(self):
        words = ["Apple", "apple", "Banana", "BANANA"]
        self.assertEqual(stable_dedupe(words, key=str.lower), ["Apple", "Banana"])

    def test_unhashable_items_with_key(self):
        rows = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}, {"id": 1, "v": "c"}]
        self.assertEqual(stable_dedupe(rows, key=lambda r: r["id"]), [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}])

    def test_key_abs_order(self):
        self.assertEqual(stable_dedupe([-1, 1, 2, -2, 3], key=abs), [-1, 2, 3])

    def test_all_identical(self):
        self.assertEqual(stable_dedupe([7, 7, 7]), [7])

    def test_returns_new_list(self):
        data = [1, 2, 3]
        result = stable_dedupe(data)
        self.assertEqual(result, [1, 2, 3])
        self.assertIsNot(result, data)

    def test_input_not_modified(self):
        data = ["b", "a", "b"]
        stable_dedupe(data)
        self.assertEqual(data, ["b", "a", "b"])


if __name__ == "__main__":
    unittest.main()
