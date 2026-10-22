import json
import unittest

from prefsync.api import PrefSyncApp

ENV = {"PREFSYNC_KEY": "functional-test-key-0123"}


class Functional(unittest.TestCase):
    def test_round_trip_and_update(self):
        app = PrefSyncApp(ENV)
        blob = app.handle_new({}, {})[1]["state"]
        self.assertEqual(app.handle_restore({}, {"state": blob})[1]["preferences"]["theme"], "light")
        status, body = app.handle_update({}, {"state": blob, "settings": json.dumps({"theme": "dark"})})
        self.assertEqual((status, body["preferences"]["theme"]), (200, "dark"))
        self.assertEqual(app.handle_restore({}, {"state": body["state"]})[1]["preferences"]["theme"], "dark")
        self.assertEqual(app.handle_update({}, {"state": blob, "settings": "[1]"})[0], 400)
        self.assertEqual(app.handle_restore({}, {"state": "not-a-blob"})[0], 400)
