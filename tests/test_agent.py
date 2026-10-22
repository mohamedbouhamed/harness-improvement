import unittest
from unittest import mock

from harness_mvp import agent
from harness_mvp.agent import backoff_delay, run_episode
from harness_mvp.model import APIError, AuthError, BudgetExhausted, CallBudget, ModelResponse
from harness_mvp.runner import ExecResult
from harness_mvp.search import BASELINE, HarnessConfig
from harness_mvp.tasks import load_tasks

HIDDEN_SENTINEL = "HIDDEN_SENTINEL_7f3a"
TASK = next(t for t in load_tasks() if t.task_id == "merge_intervals")


class ScriptedModel:
    """Returns scripted replies (str) or raises scripted exceptions; records every request."""
    synthetic = True

    def __init__(self, replies, usage=True):
        self.replies, self.usage, self.seen = list(replies), usage, []

    def complete(self, messages, context=None):
        self.seen.append([dict(m) for m in messages])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        tok = 10 if self.usage else None
        return ModelResponse(reply, tok, tok, (20 if self.usage else None), 0.01)


class FakeRunner:
    """Public runs fail with detailed output; hidden runs emit a sentinel that must never reach the model."""
    timeout_s = 1

    def __init__(self, public_outcomes):
        self.public_outcomes, self.calls = list(public_outcomes), []

    def run(self, source, task, which):
        self.calls.append(which)
        if which == "hidden":
            return ExecResult("failed", 1, HIDDEN_SENTINEL, HIDDEN_SENTINEL, 0.0, 9, 9,
                              [f"hidden_tests.HiddenTests.{HIDDEN_SENTINEL}"])
        outcome = self.public_outcomes.pop(0)
        return ExecResult(outcome, 0 if outcome == "passed" else 1, "",
                          "FAIL: test_touching_endpoints_merge (public_tests.PublicTests.test_touching_endpoints_merge)\n"
                          "AssertionError: [(1, 3), (3, 5)] != [(1, 5)]\nRan 3 tests in 0.0s\nFAILED (failures=1)",
                          0.0, 3, 3, ["public_tests.PublicTests.test_touching_endpoints_merge"])


CODE = "```python\ndef merge_intervals(intervals):\n    return []\n```"


def run(model, runner, budget=None, config=BASELINE, **kw):
    return run_episode(TASK, config, model, runner, budget or CallBudget(34), phase="optimization",
                       repetition=0, episode_id="t", **kw)[0]


class EpisodeTests(unittest.TestCase):
    def test_hidden_results_never_reach_prompts(self):
        for config in (BASELINE, HarnessConfig("structured", "compact")):
            model, runner = ScriptedModel([CODE, CODE]), FakeRunner(["failed", "failed"])
            rec = run(model, runner, config=config)
            self.assertEqual(rec["llm_calls"], 2)
            self.assertEqual(runner.calls, ["public", "public", "hidden"])
            prompts = str(model.seen)
            self.assertIn("test_touching_endpoints_merge", prompts)  # public feedback is shown
            self.assertNotIn(HIDDEN_SENTINEL, prompts)
            self.assertNotIn("hidden_tests", prompts)
            self.assertNotIn(TASK.read("hidden_tests.py"), prompts)
            self.assertNotIn(TASK.bug_note, prompts)

    def test_public_pass_stops_even_if_hidden_fails(self):
        model, runner = ScriptedModel([CODE, CODE]), FakeRunner(["passed"])
        rec = run(model, runner)
        self.assertEqual(rec["llm_calls"], 1)
        self.assertTrue(rec["first_attempt_public_pass"])
        self.assertFalse(rec["hidden_success"])
        self.assertEqual(rec["error_category"], "hidden_test_failure")

    def test_budget_enforced_before_request(self):
        budget = CallBudget(1)
        model, runner = ScriptedModel([CODE, CODE]), FakeRunner(["failed", "failed"])
        rec = run(model, runner, budget)
        self.assertEqual(len(model.seen), 1)  # second request never sent
        self.assertEqual(rec["fatal"], "budget_exhausted")
        self.assertEqual(rec["final_candidate_attempt"], 1)
        with self.assertRaises(BudgetExhausted):
            budget.reserve()

    def test_zero_budget_sends_nothing(self):
        model = ScriptedModel([CODE])
        rec = run(model, FakeRunner([]), CallBudget(0))
        self.assertEqual(model.seen, [])
        self.assertEqual(rec["llm_calls"], 0)
        self.assertIsNone(rec["hidden"])

    def test_api_error_keeps_last_valid_candidate(self):
        model, runner = ScriptedModel([CODE, APIError("HTTP 500")]), FakeRunner(["failed"])
        rec = run(model, runner)
        self.assertEqual(rec["final_candidate_attempt"], 1)
        self.assertIsNotNone(rec["hidden"])
        self.assertEqual(rec["api_errors"], 1)
        self.assertEqual(rec["error_category"], "api_error")
        self.assertIsNone(rec["total_tokens"])  # unknown usage is not zero
        self.assertIsNone(rec["fatal"])

    def test_auth_error_is_fatal(self):
        rec = run(ScriptedModel([AuthError("HTTP 401")]), FakeRunner([]))
        self.assertEqual(rec["fatal"], "auth_error")

    def test_malformed_then_syntax_error(self):
        model = ScriptedModel(["no code here", "```python\ndef f(:\n```"])
        rec = run(model, FakeRunner([]))
        self.assertEqual([a["outcome"] for a in rec["attempts"]], ["no_code", "syntax_error"])
        self.assertIsNone(rec["hidden"])
        self.assertEqual(rec["error_category"], "syntax_error")



class RetryAndPacingTests(unittest.TestCase):
    def test_transient_error_is_retried_with_backoff(self):
        model = ScriptedModel([APIError("HTTP 503", 503, None, True), CODE])
        with mock.patch.object(agent, "sleep") as fake_sleep:
            rec = run(model, FakeRunner(["passed"]), api_retries=2, retry_wait_s=10)
        fake_sleep.assert_called_once_with(10)
        self.assertEqual(rec["llm_calls"], 2)
        self.assertEqual(rec["api_errors"], 1)
        self.assertTrue(rec["first_attempt_public_pass"])
        self.assertEqual(rec["wait_s"], 10)

    def test_recovered_transient_error_is_not_an_api_failure(self):
        model = ScriptedModel([APIError("HTTP 503", 503, None, True), CODE, CODE])
        with mock.patch.object(agent, "sleep"):
            rec = run(model, FakeRunner(["failed", "failed"]), api_retries=1)
        self.assertEqual(rec["api_errors"], 1)
        self.assertEqual(rec["error_category"], "public_test_failure")

    def test_server_retry_hint_and_cap(self):
        self.assertEqual(backoff_delay(1, 10, 37.0), 37.0)
        self.assertEqual(backoff_delay(3, 10, None), 40)
        self.assertEqual(backoff_delay(9, 10, None), agent.MAX_BACKOFF_S)

    def test_non_retryable_error_is_not_resent(self):
        model = ScriptedModel([APIError("HTTP 400", 400, None, False), CODE])
        with mock.patch.object(agent, "sleep") as fake_sleep:
            rec = run(model, FakeRunner([]), api_retries=3)
        fake_sleep.assert_not_called()
        self.assertEqual(rec["llm_calls"], 1)
        self.assertEqual(rec["error_category"], "api_error")

    def test_retries_respect_global_budget(self):
        errors = [APIError("HTTP 429", 429, None, True) for _ in range(5)]
        with mock.patch.object(agent, "sleep"):
            rec = run(ScriptedModel(errors), FakeRunner([]), CallBudget(2), api_retries=4)
        self.assertEqual(rec["llm_calls"], 2)
        self.assertEqual(rec["fatal"], "budget_exhausted")

    def test_min_interval_counts_from_end_of_previous_request(self):
        now, slept = [100.0], []
        budget = CallBudget(5, min_interval_s=6, clock=lambda: now[0],
                            sleep=lambda d: (slept.append(d), now.__setitem__(0, now[0] + d)))
        budget.reserve()
        now[0] += 10  # a slow request: the pause still applies after it ends
        budget.release()
        now[0] += 2
        budget.reserve()
        budget.release()
        now[0] += 10
        budget.reserve()
        self.assertEqual(slept, [4])
        self.assertEqual(budget.waited_s, 4)

if __name__ == "__main__":
    unittest.main()
