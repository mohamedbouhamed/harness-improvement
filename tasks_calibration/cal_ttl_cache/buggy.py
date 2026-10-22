from collections import OrderedDict


class TTLCache:
    """Fixed-capacity cache with per-entry time-to-live and LRU eviction."""

    def __init__(self, capacity, ttl):
        self.capacity = capacity
        self.ttl = ttl
        self._data = OrderedDict()  # key -> (value, expires_at); oldest (LRU) first

    def get(self, key, now):
        item = self._data.get(key)
        if item is None:
            return None
        value, expires_at = item
        if now > expires_at:
            del self._data[key]
            return None
        self._data.move_to_end(key)
        return value

    def put(self, key, value, now):
        if key not in self._data and len(self._data) >= self.capacity:
            self._data.popitem(last=False)
        self._data[key] = (value, now + self.ttl)

    def size(self, now):
        return sum(1 for _, expires_at in self._data.values() if now < expires_at)
