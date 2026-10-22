import unittest

from candidate import plan_retries


class PublicTests(unittest.TestCase):
    def test_first_attempt_succeeds(self):
        self.assertEqual(plan_retries(["ok"], 3, 1, 10), (1, [], "ok"))

    def test_succeeds_on_third_attempt(self):
        self.assertEqual(plan_retries(["timeout", "unavailable", "ok"], 5, 1, 10), (3, [1, 2], "ok"))

    def test_permanent_error_on_second_attempt(self):
        self.assertEqual(plan_retries(["timeout", "bad_request", "ok"], 5, 1, 10), (2, [1], "bad_request"))


if __name__ == "__main__":
    unittest.main()
