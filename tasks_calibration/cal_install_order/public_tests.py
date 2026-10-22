import unittest

from candidate import install_order


class PublicTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
