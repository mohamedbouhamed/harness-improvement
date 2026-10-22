import unittest

from candidate import chunk_string


class PublicTests(unittest.TestCase):
    def test_remainder_chunk(self):
        self.assertEqual(chunk_string("abcdefg", 3), ["abc", "def", "g"])

    def test_empty(self):
        self.assertEqual(chunk_string("", 4), [])

    def test_zero_size_raises(self):
        with self.assertRaises(ValueError):
            chunk_string("abc", 0)


if __name__ == "__main__":
    unittest.main()
