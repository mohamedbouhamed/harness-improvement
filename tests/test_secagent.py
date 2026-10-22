"""Offline checks for the detection study: matching rule, leakage, parser, cost cap, model-id recording."""
import io
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from harness_mvp.model import BudgetExhausted, CallBudget, ModelResponse, ModelSettings
from secagent.agent import first_message, parse_actions, run_episode, system_prompt, DetectLimits
from secagent.llm_ext import CostLedger, MeteredModel, Price, billable_output
from secagent.run import MockDetector, guidance_for
from secagent.scoring import score_episode
from secagent.tasks import load_sec_benchmark

TASKS = {t.task_id: t for t in load_sec_benchmark()}
DOC = TASKS["cal_docshare"]


def finding(**kw):
    base = {"file": "docshare/storage.py", "function": "_resolve", "lines": [14, 20], "cwe": "CWE-22",
            "source": "x", "path": ["handle_download", "read", "_resolve"], "reason": "x"}
    return base | kw


class Matching(unittest.TestCase):
    def test_exact_and_sink_function_match(self):
        self.assertTrue(score_episode(DOC, "vulnerable", [finding()])["detected"])
        self.assertTrue(score_episode(DOC, "vulnerable", [finding(function="DocumentStore.read", lines=None)])["detected"])

    def test_equivalent_cwe_and_line_overlap(self):
        s = score_episode(DOC, "vulnerable", [finding(function="unknown", cwe="CWE-23", lines=[17, 17])])
        self.assertTrue(s["detected"])

    def test_wrong_type_is_misclassified_not_detected(self):
        s = score_episode(DOC, "vulnerable", [finding(cwe="CWE-78")])
        self.assertEqual((s["detected"], s["misclassified"], s["alarms"]), (False, 1, 1))

    def test_wrong_file_is_an_alarm(self):
        s = score_episode(DOC, "vulnerable", [finding(file="docshare/uploads.py", function="save"), finding()])
        self.assertEqual((s["detected"], s["alarms"], s["matched_index"]), (True, 1, 1))

    def test_twin_clean_requires_explicit_empty_submission(self):
        self.assertTrue(score_episode(DOC, "patched", [])["clean"])
        self.assertFalse(score_episode(DOC, "patched", None)["clean"])
        self.assertEqual(score_episode(DOC, "patched", [finding()])["alarms"], 1)
        self.assertFalse(score_episode(DOC, "vulnerable", None)["detected"])


class NoLeakage(unittest.TestCase):
    def test_agent_prompt_has_no_task_metadata(self):
        for t in TASKS.values():
            for variant in ("vulnerable", "patched"):
                text = system_prompt("instr", DetectLimits()) + first_message(t.variant_files(variant), guidance_for("lessons"))
                for secret in (t.task_id, t.split, t.answer["summary"][:40], "witness", "patched", "vulnerable"):
                    self.assertNotIn(secret, text, f"{t.task_id}/{variant} leaks {secret!r}")

    def test_witness_and_answer_never_in_agent_files(self):
        for t in TASKS.values():
            for variant in ("vulnerable", "patched"):
                self.assertFalse([f for f in t.variant_files(variant) if "witness" in f or "answer" in f])

    def test_pairs_differ_only_in_overlay(self):
        for t in TASKS.values():
            v, p = t.variant_files("vulnerable"), t.variant_files("patched")
            self.assertEqual(set(v), set(p))
            self.assertGreaterEqual(sum(v[k] != p[k] for k in v), 1)


class Parser(unittest.TestCase):
    def test_text_and_stringified_findings(self):
        calls, err = parse_actions('{"actions": [{"action": "submit_findings", "findings": "[]"}]}', 6)
        self.assertEqual((err, calls[0]["args"]["findings"]), (None, []))

    def test_submit_must_be_last(self):
        _, err = parse_actions('{"actions": [{"action": "submit_findings", "findings": []}, {"action": "list_files"}]}', 6)
        self.assertIn("last", err)


class Episode(unittest.TestCase):
    def test_mock_episode_and_no_submit(self):
        tasks = list(TASKS.values())
        rec, _ = run_episode(DOC, "vulnerable", MockDetector(tasks), CallBudget(10), episode_id="e", condition="none",
                             repetition=1, instructions="i", guidance="")
        self.assertTrue(rec["score"]["detected"])
        rec, _ = run_episode(DOC, "patched", MockDetector(tasks), CallBudget(1), episode_id="e", condition="none",
                             repetition=1, instructions="i", guidance="")
        self.assertEqual((rec["end_reason"], rec["score"]["clean"]), ("budget_exhausted", False))


def http_response(body: dict):
    resp = mock.MagicMock()
    resp.read.return_value = json.dumps(body).encode()
    resp.__enter__.return_value = resp
    return resp


class Cost(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.settings = ModelSettings("https://api.test/v1", "m1", "k", 0.0, 1000)
        self.price = Price("m1", 1.0, 10.0, "test", "today", True)

    def model(self, cap):
        return MeteredModel(self.settings, self.price, CostLedger(self.dir / "l.jsonl", cap, 1.0), run_id="r")

    def test_thinking_tokens_are_billed(self):
        self.assertEqual(billable_output({"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 300}), 200)

    def test_records_models_usage_and_cost(self):
        body = {"model": "m1-07-2026", "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100}}
        m = self.model(1.0)
        with mock.patch("urllib.request.urlopen", return_value=http_response(body)):
            r = m.complete([{"role": "user", "content": "x"}])
        entry = m.ledger.entries()[0]
        self.assertEqual((entry["requested_model"], entry["returned_model"], r.model), ("m1", "m1-07-2026", "m1-07-2026"))
        self.assertAlmostEqual(entry["cost_eur"], (1000 * 1.0 + 100 * 10.0) / 1e6)

    def test_cap_blocks_before_sending(self):
        m = self.model(0.005)  # worst case: 1000 output tokens * $10/M = $0.01 > cap
        with mock.patch("urllib.request.urlopen") as sent:
            with self.assertRaises(BudgetExhausted):
                m.complete([{"role": "user", "content": "x"}])
        sent.assert_not_called()

    def test_unconfirmed_prices_refused(self):
        with self.assertRaises(RuntimeError):
            MeteredModel(self.settings, Price("m1", 1, 1, "s", "d", False), CostLedger(self.dir / "x", 1, 1), run_id="r")

    def test_rejected_request_not_billed_but_logged(self):
        m = self.model(1.0)
        err = urllib.error.HTTPError("u", 429, "busy", {}, io.BytesIO(b"{}"))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(Exception):
                m.complete([{"role": "user", "content": "x"}])
        self.assertEqual([(e["status"], e["cost_eur"]) for e in m.ledger.entries()], [("http_429", 0.0)])


if __name__ == "__main__":
    unittest.main()
