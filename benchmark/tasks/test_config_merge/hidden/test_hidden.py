import unittest

from settings.loader import DEFAULTS, load
from settings.merge import deep_merge


class HiddenTests(unittest.TestCase):
    def test_override_then_defaults_unchanged(self):
        self.assertEqual(load({"server": {"port": 9000}})["server"], {"host": "localhost", "port": 9000})
        self.assertEqual(load()["server"]["port"], 8080)

    def test_new_key(self):
        self.assertEqual(load({"debug": True})["debug"], True)

    def test_merge_does_not_modify_inputs(self):
        base, override = {"a": {"b": 1}}, {"a": {"c": 2}}
        self.assertEqual(deep_merge(base, override), {"a": {"b": 1, "c": 2}})
        self.assertEqual(base, {"a": {"b": 1}})
        self.assertEqual(override, {"a": {"c": 2}})

    def test_result_shares_nothing_with_defaults(self):
        config = load()
        config["server"]["port"] = 1
        config["features"].append("x")
        self.assertEqual(DEFAULTS, {"server": {"host": "localhost", "port": 8080}, "features": []})

    def test_result_shares_nothing_with_override(self):
        override = {"features": ["a"]}
        config = load(override)
        config["features"].append("b")
        self.assertEqual(override, {"features": ["a"]})

    def test_non_dict_override_replaces(self):
        self.assertEqual(deep_merge({"a": {"b": 1}}, {"a": 3}), {"a": 3})

    def test_overrides_apply_in_order(self):
        self.assertEqual(load({"server": {"port": 1}}, {"server": {"port": 2}})["server"]["port"], 2)


if __name__ == "__main__":
    unittest.main()
