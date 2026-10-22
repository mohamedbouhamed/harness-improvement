"""A sliding-window rate limiter."""
from .window import count_in_window


class RateLimiter:
    def __init__(self, limit, size):
        self.limit, self.size = limit, size
        self._accepted = []

    def allow(self, now):
        """Accept a request at `now` if fewer than `limit` accepted requests are in the window ending at now."""
        if count_in_window(self._accepted, now, self.size) > self.limit:
            return False
        self._accepted.append(now)
        return True
