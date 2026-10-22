import unittest

from ratelimit.limiter import RateLimiter
from ratelimit.window import count_in_window


class HiddenTests(unittest.TestCase):
    def test_limit_reached(self):
        limiter = RateLimiter(limit=2, size=10)
        self.assertEqual([limiter.allow(t) for t in (0, 1, 2)], [True, True, False])

    def test_requests_far_apart(self):
        limiter = RateLimiter(limit=1, size=5)
        self.assertEqual([limiter.allow(t) for t in (0, 20)], [True, True])

    def test_window_start_is_excluded(self):
        self.assertEqual(count_in_window([0, 5, 10], 10, 10), 2)

    def test_request_exactly_one_window_later(self):
        limiter = RateLimiter(limit=1, size=10)
        self.assertEqual([limiter.allow(t) for t in (0, 10)], [True, True])

    def test_rejected_requests_do_not_count(self):
        limiter = RateLimiter(limit=1, size=10)
        self.assertEqual([limiter.allow(t) for t in (0, 5, 10, 11)], [True, False, True, False])


if __name__ == "__main__":
    unittest.main()
