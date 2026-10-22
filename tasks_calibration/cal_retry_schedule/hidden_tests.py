import unittest

from candidate import plan_retries


class HiddenTests(unittest.TestCase):
    # Public cases are repeated so that hidden success implies public success.
    def test_first_attempt_succeeds(self):
        self.assertEqual(plan_retries(["ok"], 3, 1, 10), (1, [], "ok"))

    def test_succeeds_on_third_attempt(self):
        self.assertEqual(plan_retries(["timeout", "unavailable", "ok"], 5, 1, 10), (3, [1, 2], "ok"))

    def test_permanent_error_on_second_attempt(self):
        self.assertEqual(plan_retries(["timeout", "bad_request", "ok"], 5, 1, 10), (2, [1], "bad_request"))

    def test_exhausted_without_trailing_wait(self):
        self.assertEqual(plan_retries(["timeout"] * 3, 3, 1, 10), (3, [1, 2], "exhausted"))

    def test_delay_is_capped(self):
        self.assertEqual(plan_retries(["unavailable"] * 5, 5, 3, 10), (5, [3, 6, 10, 10], "exhausted"))

    def test_permanent_error_on_first_attempt(self):
        self.assertEqual(plan_retries(["auth_failed", "ok"], 3, 1, 10), (1, [], "auth_failed"))

    def test_single_attempt_allowed(self):
        self.assertEqual(plan_retries(["timeout", "ok"], 1, 1, 10), (1, [], "exhausted"))

    def test_rate_limited_is_retryable(self):
        self.assertEqual(plan_retries(["rate_limited", "ok"], 2, 0.5, 10), (2, [0.5], "ok"))

    def test_extra_outcomes_are_ignored(self):
        self.assertEqual(plan_retries(["timeout", "timeout", "ok"], 2, 2, 100), (2, [2], "exhausted"))


if __name__ == "__main__":
    unittest.main()
