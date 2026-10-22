import unittest

from settings.loader import load


class PublicTests(unittest.TestCase):
    def test_override_then_defaults_unchanged(self):
        self.assertEqual(load({"server": {"port": 9000}})["server"], {"host": "localhost", "port": 9000})
        self.assertEqual(load()["server"]["port"], 8080)

    def test_new_key(self):
        self.assertEqual(load({"debug": True})["debug"], True)


if __name__ == "__main__":
    unittest.main()
