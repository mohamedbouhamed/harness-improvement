import unittest

from ssohub.api import SsoApp


class Functional(unittest.TestCase):
    def test_login_flow(self):
        app = SsoApp({"alice": "correct horse"})
        self.assertEqual(app.handle_login({"username": "alice", "password": "nope"})[0], 401)
        status, headers = app.handle_login({"username": "alice", "password": "correct horse", "next": "/wiki/page?x=1"})
        self.assertEqual((status, headers["Location"]), (302, "/wiki/page?x=1"))
        self.assertEqual(app.handle_login({"username": "alice", "password": "correct horse",
                                           "next": "https://elsewhere.example/"})[1]["Location"], "/home")
        self.assertEqual(app.handle_login({"username": "alice", "password": "correct horse"})[1]["Location"], "/home")
        self.assertTrue(app.handle_protected("bad", {"path": "/x"})[1]["Location"].startswith("/login?next="))
