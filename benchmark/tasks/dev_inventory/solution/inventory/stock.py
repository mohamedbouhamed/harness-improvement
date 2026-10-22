"""Stock levels with reservations."""


class Stock:
    def __init__(self, quantities):
        self._on_hand = dict(quantities)
        self._reserved = {}

    def available(self, sku):
        """Units of `sku` on hand and not reserved."""
        return self._on_hand.get(sku, 0) - self._reserved.get(sku, 0)

    def reserve(self, sku, qty):
        """Reserve `qty` (> 0) units; raises ValueError if fewer than `qty` are available."""
        if qty <= 0:
            raise ValueError("qty must be positive")
        if qty > self.available(sku):
            raise ValueError(f"insufficient stock for {sku}")
        self._reserved[sku] = self._reserved.get(sku, 0) + qty

    def release(self, sku, qty):
        """Return `qty` previously reserved units of `sku` to availability."""
        self._reserved[sku] = self._reserved.get(sku, 0) - qty
