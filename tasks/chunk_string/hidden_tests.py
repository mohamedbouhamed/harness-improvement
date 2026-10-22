import unittest

from candidate import chunk_string


class HiddenTests(unittest.TestCase):
    # Public cases are repeated so that hidden success implies public success.
    def test_remainder_chunk(self):
        self.assertEqual(chunk_string("abcdefg", 3), ["abc", "def", "g"])

    def test_empty(self):
        self.assertEqual(chunk_string("", 4), [])

    def test_zero_size_raises(self):
        with self.assertRaises(ValueError):
            chunk_string("abc", 0)

    def test_exact_multiple(self):
        self.assertEqual(chunk_string("abcdef", 2), ["ab", "cd", "ef"])

    def test_single_full_chunk(self):
        self.assertEqual(chunk_string("abc", 3), ["abc"])

    def test_size_larger_than_text(self):
        self.assertEqual(chunk_string("ab", 5), ["ab"])

    def test_size_one(self):
        self.assertEqual(chunk_string("abc", 1), ["a", "b", "c"])

    def test_negative_size_raises(self):
        with self.assertRaises(ValueError):
            chunk_string("abc", -2)

    def test_join_roundtrip(self):
        for text in ["", "x", "hello world", "0123456789"]:
            for size in range(1, 12):
                self.assertEqual("".join(chunk_string(text, size)), text)


if __name__ == "__main__":
    unittest.main()
