"""Infrastructure checks kept from earlier experiments: request budget, pacing, retries, adapter, runner parsing."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from harness_mvp import llm
from harness_mvp.model import (APIError, AuthError, CallBudget, ModelResponse, ModelSettings,
                               OpenAICompatibleModel, sanitize_url)
from harness_mvp.runner import classify, parse_unittest_output


class Scripted:
    synthetic = True

    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0

    def complete(self, messages, context=None):
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return ModelResponse(r, 1, 1, 2, 0.0)


def send(model, budget, retries=0):
    with mock.patch.object(llm, "sleep") as fake_sleep:
        out = llm.request(model, [], budget, {}, api_retries=retries, retry_wait_s=10, log=lambda m: None, label="t")
    return out, fake_sleep


class RequestTests(unittest.TestCase):
    def test_transient_error_is_retried_with_backoff(self):
        (resp, recs, fatal, waited), slept = send(Scripted([APIError("503", 503, None, True), "ok"]), CallBudget(5), 2)
        self.assertEqual((resp.text, len(recs), fatal, waited), ("ok", 2, None, 10))
        slept.assert_called_once_with(10)

    def test_server_retry_hint_and_cap(self):
        self.assertEqual(llm.backoff_delay(1, 10, 37.0), 37.0)
        self.assertEqual(llm.backoff_delay(3, 10, None), 40)
        self.assertEqual(llm.backoff_delay(9, 10, None), llm.MAX_BACKOFF_S)

    def test_non_retryable_error_is_not_resent(self):
        model = Scripted([APIError("400", 400, None, False), "ok"])
        (resp, recs, fatal, _), slept = send(model, CallBudget(5), 3)
        self.assertIsNone(resp)
        self.assertEqual(model.calls, 1)
        slept.assert_not_called()

    def test_retries_count_against_the_global_budget(self):
        model = Scripted([APIError("429", 429, None, True)] * 5)
        (resp, recs, fatal, _), _ = send(model, CallBudget(2), 4)
        self.assertEqual((model.calls, fatal), (2, "budget_exhausted"))

    def test_zero_budget_sends_nothing(self):
        model = Scripted(["ok"])
        (resp, recs, fatal, _), _ = send(model, CallBudget(0))
        self.assertEqual((model.calls, fatal, recs), (0, "budget_exhausted", []))

    def test_auth_error_is_fatal(self):
        (resp, recs, fatal, _), _ = send(Scripted([AuthError("401")]), CallBudget(5), 3)
        self.assertEqual(fatal, "auth_error")

    def test_pause_counts_from_end_of_previous_request(self):
        now, slept = [100.0], []
        budget = CallBudget(5, min_interval_s=6, clock=lambda: now[0],
                            sleep=lambda d: (slept.append(d), now.__setitem__(0, now[0] + d)))
        budget.reserve()
        now[0] += 10
        budget.release()
        now[0] += 2
        budget.reserve()
        self.assertEqual(slept, [4])


class AdapterTests(unittest.TestCase):
    """OpenAI-compatible adapter against a local fake server (no network, no credentials)."""

    @classmethod
    def setUpClass(cls):
        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                cls.seen = (body, self.headers["Authorization"])
                code, payload, headers = {
                    "ok": (200, {"choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                                 "usage": {"prompt_tokens": 3, "completion_tokens": 2}}, {}),
                    "nousage": (200, {"choices": [{"message": {"content": "hi"}}]}, {}),
                    "native": (200, {"choices": [{"message": {"content": None, "tool_calls": [
                        {"id": "c1", "type": "function",
                         "function": {"name": "read_file", "arguments": "{\"path\": \"a.py\"}"}}]},
                        "finish_reason": "tool_calls"}], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}, {}),
                    "auth": (401, {"error": {"message": "bad key"}}, {}),
                    "gemini-auth": (400, [{"error": {"message": "API key not valid."}}], {}),
                    "quota": (429, [{"error": {"message": "quota. Please retry in 37.5s."}}], {}),
                    "busy": (503, {"error": {"message": "busy"}}, {"Retry-After": "12"}),
                    "bad": (400, {"error": {"message": "invalid model"}}, {}),
                }[body["model"]]
                self.send_response(code)
                for k, v in headers.items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(json.dumps(payload).encode())

        cls.server = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def model(self, name):
        return OpenAICompatibleModel(ModelSettings(f"http://127.0.0.1:{self.server.server_port}/v1", name,
                                                   "sk-test", 0.0, 64))

    def test_native_function_calls_round_trip(self):
        from harness_mvp.tools import function_declarations, parse_tool_calls
        r = self.model("native").complete([{"role": "user", "content": "x"}], context={"tools": function_declarations()})
        self.assertEqual(self.seen[0]["tools"], function_declarations())  # declarations were sent
        self.assertEqual(r.native_tool_calls, 1)
        self.assertEqual(parse_tool_calls(r.text, 4)[0], [{"name": "read_file", "args": {"path": "a.py"}}])
        self.model("ok").complete([{"role": "user", "content": "x"}])
        self.assertNotIn("tools", self.seen[0])  # only sent when the caller asks (agent turns)

    def test_success_and_usage(self):
        r = self.model("ok").complete([{"role": "user", "content": "x"}])
        self.assertEqual((r.text, r.input_tokens, r.output_tokens, r.total_tokens), ("hi", 3, 2, 5))
        body, auth = self.seen
        self.assertEqual((body["max_tokens"], body["temperature"], auth), (64, 0.0, "Bearer sk-test"))

    def test_missing_usage_stays_none(self):
        r = self.model("nousage").complete([])
        self.assertIsNone(r.total_tokens)

    def test_error_classification(self):
        for name in ("auth", "gemini-auth"):
            with self.assertRaises(AuthError):
                self.model(name).complete([])
        for name, retryable, hint in (("quota", True, 37.5), ("busy", True, 12.0), ("bad", False, None)):
            with self.assertRaises(APIError) as ctx:
                self.model(name).complete([])
            self.assertEqual((ctx.exception.retryable, ctx.exception.retry_after), (retryable, hint))

    def test_credentials_never_persisted(self):
        self.assertEqual(sanitize_url("https://u:p@host:8443/v1?key=x#f"), "https://host:8443/v1")
        self.assertNotIn("sk-test", repr(ModelSettings("u", "m", "sk-test")))
        self.assertNotIn("sk-test", json.dumps(ModelSettings("u", "m", "sk-test").public_dict()))


class RunnerParsingTests(unittest.TestCase):
    def test_failures_and_forged_exit(self):
        out = "FAIL: test_a (tests.test_public.PublicTests.test_a)\nRan 3 tests in 0.0s\nFAILED (failures=1)"
        self.assertEqual(parse_unittest_output(out), (3, ["tests.test_public.PublicTests.test_a"]))
        self.assertEqual(classify(0, "Ran 2 tests in 0s\n\nOK", 2, False)[0], "passed")
        self.assertEqual(classify(0, "", 2, False)[0], "failed")  # exit 0 without the unittest summary
        self.assertEqual(classify(0, "Ran 1 tests in 0s\n\nOK", 2, False)[0], "failed")  # fewer tests than expected
        self.assertEqual(classify(None, "", 2, True)[0], "timeout")


if __name__ == "__main__":
    unittest.main()
