import unittest

from inventory.orders import OrderBook
from inventory.stock import Stock


class PublicTests(unittest.TestCase):
    def test_reserve_everything_available(self):
        stock = Stock({"pen": 3})
        OrderBook(stock).place("o1", [("pen", 3)])
        self.assertEqual(stock.available("pen"), 0)

    def test_cancel_releases(self):
        stock = Stock({"pen": 5})
        book = OrderBook(stock)
        book.place("o1", [("pen", 2)])
        book.cancel("o1")
        self.assertEqual(stock.available("pen"), 5)


if __name__ == "__main__":
    unittest.main()
