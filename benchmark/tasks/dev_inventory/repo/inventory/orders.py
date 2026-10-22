"""Orders reserve stock for all of their lines, or for none of them."""


class OrderBook:
    def __init__(self, stock):
        self.stock = stock
        self.orders = {}

    def place(self, order_id, lines):
        """Reserve every (sku, qty) line. If any line fails, nothing stays reserved and ValueError is raised."""
        if order_id in self.orders:
            raise ValueError(f"duplicate order {order_id}")
        for sku, qty in lines:
            self.stock.reserve(sku, qty)
        self.orders[order_id] = list(lines)

    def cancel(self, order_id):
        """Release the stock of a placed order; raises KeyError for unknown orders."""
        for sku, qty in self.orders.pop(order_id):
            self.stock.release(sku, qty)
