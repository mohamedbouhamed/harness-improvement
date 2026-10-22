import unittest

from candidate import TTLCache


class HiddenTests(unittest.TestCase):
    # Public cases are repeated so that hidden success implies public success.
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

    def test_update_replaces_value_and_resets_expiry(self):
        cache = TTLCache(2, 10)
        cache.put("a", 1, now=0)
        cache.put("a", 2, now=5)
        self.assertEqual(cache.get("a", now=12), 2)
        self.assertIsNone(cache.get("a", now=15))

    def test_update_refreshes_recency(self):
        cache = TTLCache(2, 100)
        cache.put("a", 1, now=0)
        cache.put("b", 2, now=1)
        cache.put("a", 3, now=2)
        cache.put("c", 4, now=3)  # evicts b, the least recently used
        self.assertEqual(cache.get("a", now=4), 3)
        self.assertIsNone(cache.get("b", now=4))
        self.assertEqual(cache.get("c", now=4), 4)

    def test_get_refreshes_recency(self):
        cache = TTLCache(2, 100)
        cache.put("a", 1, now=0)
        cache.put("b", 2, now=1)
        self.assertEqual(cache.get("a", now=2), 1)
        cache.put("c", 3, now=3)  # evicts b
        self.assertIsNone(cache.get("b", now=4))
        self.assertEqual(cache.get("a", now=4), 1)

    def test_expired_entries_purged_before_lru_eviction(self):
        cache = TTLCache(2, 10)
        cache.put("a", 1, now=0)   # expires at 10
        cache.put("b", 2, now=5)   # expires at 15
        self.assertEqual(cache.get("a", now=6), 1)  # a is now most recently used
        cache.put("c", 3, now=11)  # a has expired: purge it, b must survive
        self.assertEqual(cache.get("b", now=12), 2)
        self.assertEqual(cache.get("c", now=12), 3)

    def test_get_does_not_extend_expiry(self):
        cache = TTLCache(2, 10)
        cache.put("a", 1, now=0)
        self.assertEqual(cache.get("a", now=9), 1)
        self.assertIsNone(cache.get("a", now=10))

    def test_size_counts_live_entries(self):
        cache = TTLCache(3, 10)
        cache.put("a", 1, now=0)
        cache.put("b", 2, now=5)
        self.assertEqual(cache.size(4), 2)
        self.assertEqual(cache.size(10), 1)
        self.assertEqual(cache.size(15), 0)

    def test_capacity_one(self):
        cache = TTLCache(1, 10)
        cache.put("a", 1, now=0)
        cache.put("b", 2, now=1)
        self.assertIsNone(cache.get("a", now=2))
        self.assertEqual(cache.get("b", now=2), 2)


if __name__ == "__main__":
    unittest.main()
