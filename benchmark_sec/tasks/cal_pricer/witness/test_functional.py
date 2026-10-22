import unittest

from pricer.api import PricerApp


class Functional(unittest.TestCase):
    def test_quotes_and_previews(self):
        app = PricerApp()
        self.assertEqual(app.handle_quote({}, {"sku": "tee", "quantity": "10"}), (200, {"total": 171.0}))
        self.assertEqual(app.handle_quote({}, {"sku": "mug", "rule": "clearance", "quantity": "2"}), (200, {"total": 8.5}))
        self.assertEqual(app.handle_bundle_preview({}, {"sku": "cap", "quantity": "3", "formula": "price * qty + shipping"}),
                         (200, {"total": 46.9}))
        self.assertEqual(app.handle_bundle_preview({}, {"sku": "cap", "quantity": "3", "formula": "price *"})[0], 400)
        self.assertEqual(app.handle_bundle_preview({}, {"sku": "cap", "quantity": "3", "formula": "discount"})[0], 400)
        self.assertEqual(app.handle_quote({}, {"sku": "hat"})[0], 404)
