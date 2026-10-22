import copy
import unittest

from candidate import install_order


class HiddenTests(unittest.TestCase):
    # Public cases are repeated so that hidden success implies public success.
    def test_chain(self):
        packages = {"app": ["lib"], "lib": ["core"], "core": []}
        self.assertEqual(install_order(packages, ["app"]), ["core", "lib", "app"])

    def test_shared_dependency(self):
        packages = {"app": ["auth", "db"], "auth": ["util"], "db": ["util"], "util": []}
        self.assertEqual(install_order(packages, ["app"]), ["util", "auth", "db", "app"])

    def test_two_package_cycle(self):
        with self.assertRaises(ValueError) as ctx:
            install_order({"a": ["b"], "b": ["a"]}, ["a"])
        self.assertTrue(str(ctx.exception).startswith("cycle"))

    def test_self_dependency_is_cycle(self):
        with self.assertRaises(ValueError) as ctx:
            install_order({"a": ["a"]}, ["a"])
        self.assertTrue(str(ctx.exception).startswith("cycle"))

    def test_long_cycle_behind_valid_prefix(self):
        packages = {"top": ["ok", "x"], "ok": [], "x": ["y"], "y": ["z"], "z": ["x"]}
        with self.assertRaises(ValueError) as ctx:
            install_order(packages, ["top"])
        self.assertTrue(str(ctx.exception).startswith("cycle"))

    def test_missing_dependency(self):
        with self.assertRaises(ValueError) as ctx:
            install_order({"app": ["ghost"]}, ["app"])
        self.assertTrue(str(ctx.exception).startswith("missing"))

    def test_missing_target(self):
        with self.assertRaises(ValueError) as ctx:
            install_order({"app": []}, ["web"])
        self.assertTrue(str(ctx.exception).startswith("missing"))

    def test_dependencies_follow_listed_order(self):
        packages = {"app": ["zlib", "attrs"], "zlib": [], "attrs": []}
        self.assertEqual(install_order(packages, ["app"]), ["zlib", "attrs", "app"])

    def test_multiple_targets_share_dependencies(self):
        packages = {"web": ["http", "log"], "cli": ["log", "args"], "http": ["log"], "log": [], "args": []}
        self.assertEqual(install_order(packages, ["web", "cli"]), ["log", "http", "web", "args", "cli"])

    def test_target_already_installed_as_dependency(self):
        packages = {"app": ["lib"], "lib": []}
        self.assertEqual(install_order(packages, ["app", "lib"]), ["lib", "app"])

    def test_unreachable_packages_excluded_and_input_unchanged(self):
        packages = {"app": ["lib"], "lib": [], "unused": ["lib"]}
        snapshot = copy.deepcopy(packages)
        self.assertEqual(install_order(packages, ["app"]), ["lib", "app"])
        self.assertEqual(packages, snapshot)

    def test_no_targets(self):
        self.assertEqual(install_order({"a": []}, []), [])


if __name__ == "__main__":
    unittest.main()
