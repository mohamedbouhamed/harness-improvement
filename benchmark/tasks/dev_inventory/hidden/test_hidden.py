import unittest

from inventory.orders import OrderBook
from inventory.stock import Stock


class HiddenTests(unittest.TestCase):
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

    def test_failed_order_reserves_nothing(self):
        stock = Stock({"pen": 5, "ink": 1})
        book = OrderBook(stock)
        with self.assertRaises(ValueError):
            book.place("o1", [("pen", 2), ("ink", 2)])
        self.assertEqual(stock.available("pen"), 5)
        self.assertEqual(stock.available("ink"), 1)
        self.assertNotIn("o1", book.orders)

    def test_over_reservation_rejected(self):
        with self.assertRaises(ValueError):
            Stock({"pen": 1}).reserve("pen", 2)

    def test_unknown_sku_has_no_stock(self):
        with self.assertRaises(ValueError):
            Stock({}).reserve("pen", 1)

    def test_duplicate_and_unknown_orders(self):
        book = OrderBook(Stock({"pen": 5}))
        book.place("o1", [("pen", 1)])
        with self.assertRaises(ValueError):
            book.place("o1", [("pen", 1)])
        with self.assertRaises(KeyError):
            book.cancel("missing")

    def test_non_positive_quantity(self):
        with self.assertRaises(ValueError):
            Stock({"pen": 5}).reserve("pen", 0)


if __name__ == "__main__":
    unittest.main()
