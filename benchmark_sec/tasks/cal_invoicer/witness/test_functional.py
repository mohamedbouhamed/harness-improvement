import unittest

from invoicer.api import InvoicerApp

ALICE = {"name": "alice", "role": "member"}
ADMIN = {"name": "root", "role": "admin"}


class Functional(unittest.TestCase):
    def test_owner_flow(self):
        app = InvoicerApp()
        inv = app.handle_create(ALICE, {"customer": "Acme", "lines": [("design", 2, 5000)]})[1]["id"]
        self.assertEqual(app.handle_view(ALICE, {"id": str(inv)})[0], 200)
        self.assertIn("Acme", app.handle_export(ALICE, {"id": str(inv)})[1])
        self.assertIn("Total: 100.00", app.handle_export(ALICE, {"id": str(inv), "format": "text"})[1])
        self.assertEqual(app.handle_export(ADMIN, {"id": str(inv)})[0], 200)
        self.assertEqual(app.handle_set_status(ALICE, {"id": str(inv), "status": "void"})[0], 403)
        self.assertEqual(app.handle_set_status(ALICE, {"id": str(inv), "status": "paid"})[0], 200)
        self.assertEqual(app.handle_export(ALICE, {"id": "999"})[0], 404)
