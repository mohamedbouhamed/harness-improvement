import unittest

from candidate import TTLCache


class PublicTests(unittest.TestCase):
    def test_put_then_get(self):
        cache = TTLCache(2, 10)
        cache.put("a", 1, now=0)
        self.assertEqual(cache.get("a", now=5), 1)

    def test_missing_key(self):
        self.assertIsNone(TTLCache(2, 10).get("nope", now=0))

    def test_expired_exactly_at_ttl(self):
        cache = TTLCache(2, 10)
        cache.put("a", 1, now=0)
        self.assertEqual(cache.get("a", now=9), 1)
        self.assertIsNone(cache.get("a", now=10))


if __name__ == "__main__":
    unittest.main()
