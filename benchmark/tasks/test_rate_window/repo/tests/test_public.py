import unittest

from ratelimit.limiter import RateLimiter


class PublicTests(unittest.TestCase):
    def test_limit_reached(self):
        limiter = RateLimiter(limit=2, size=10)
        self.assertEqual([limiter.allow(t) for t in (0, 1, 2)], [True, True, False])

    def test_requests_far_apart(self):
        limiter = RateLimiter(limit=1, size=5)
        self.assertEqual([limiter.allow(t) for t in (0, 20)], [True, True])


if __name__ == "__main__":
    unittest.main()
