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


class ScorerV2(unittest.TestCase):
    def setUp(self):
        from secagent.scoring import score_episode_v2
        self.score = lambda fs, v="vulnerable": score_episode_v2(DOC, v, fs)

    def test_broad_ranges_cannot_bypass_localisation(self):
        self.assertFalse(self.score([finding(function="does_not_exist", lines=[1, 9999])])["detected"])
        self.assertFalse(self.score([finding(function="list_folder", lines=[1, 9999])])["detected"])
        self.assertFalse(self.score([finding(function="list_folder", lines=[17, 17])])["detected"])  # wrong real function
        self.assertTrue(self.score([finding(function="does_not_exist", lines=[15, 20])])["detected"])  # narrow, overlapping
        self.assertTrue(self.score([finding(lines=None)])["detected"])  # GT function named

    def test_structure_and_duplicates(self):
        s = self.score([finding(), finding(), finding(function="")])
        self.assertEqual((s["detected"], s["duplicates"], s["malformed"], s["alarms"]), (True, 1, 1, 1))
        self.assertEqual(self.score([finding(), finding()], "patched")["alarms"], 1)

    def test_path_precision_and_recall(self):
        s = self.score([finding(path=["handle_download", "read", "_resolve", "unrelated", "other"])])
        self.assertEqual((s["path_recall"], s["path_precision"]), (1.0, 0.6))


class Variants(unittest.TestCase):
    def test_bundles_match_tags_and_refuse_tampering(self):
        import shutil, subprocess, tempfile
        from secagent.variants import VARIANTS_DIR, load_variant
        tag = lambda t, f: subprocess.run(["git", "show", f"{t}:{f}"], capture_output=True, text=True).stdout.strip()
        a, b, c = (load_variant(n) for n in "ABC")
        self.assertEqual(a["instructions"], tag("harness-v0", "harness/instructions.md"))
        self.assertEqual((a["verification"], b["verification"]), ("", ""))
        self.assertEqual(b["instructions"], tag("harness-v1", "harness/instructions.md"))
        self.assertEqual(c["instructions"], b["instructions"])
        for word in ("patched", "fixed", "twin", "benchmark"):
            self.assertNotIn(word, (c["instructions"] + c["verification"]).lower())
        tmp = Path(tempfile.mkdtemp())
        shutil.copytree(VARIANTS_DIR / "C", tmp / "C")
        (tmp / "C" / "verification.md").write_text("changed")
        with self.assertRaises(ValueError):
            load_variant("C", tmp)


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

    def test_cap_uses_upper_bound_not_reported_cost(self):
        body = {"model": "m1", "choices": [{"message": {"content": "hi"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11}}
        m = self.model(0.015)  # bound per request ~ $0.01 (full 1000-token allowance at $10/M)
        with mock.patch("urllib.request.urlopen", return_value=http_response(body)):
            m.complete([{"role": "user", "content": "x"}])
            self.assertLess(m.ledger.spent_eur, 0.001)
            with self.assertRaises(BudgetExhausted):  # reported cost is tiny, but the bound blocks
                m.complete([{"role": "user", "content": "x"}])

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


class Proposal(unittest.TestCase):
    def test_validation_rejects_identifiers_and_wrong_targets(self):
        from secagent.propose import parse_proposal, validate_proposal
        p, err = parse_proposal('{"pattern": "p", "target": "harness/memory.md", "new_content": "Check _resolve in storage", "rationale": "r"}')
        self.assertIsNone(err)
        self.assertTrue(any("identifiers" in x for x in validate_proposal(p, [DOC])))
        self.assertTrue(validate_proposal(p | {"target": "secagent/agent.py", "new_content": "x"}, [DOC]))
        self.assertEqual(validate_proposal(p | {"new_content": "Check containment by whole path components."}, [DOC]), [])

    def test_mined_patterns_hide_nothing_from_eval_but_include_train_answer(self):
        from secagent.propose import mine_patterns
        rec, _ = run_episode(DOC, "vulnerable", MockDetector(list(TASKS.values()), detect=False), CallBudget(10),
                             episode_id="e", condition="none", repetition=1, instructions="i", guidance="")
        pat = mine_patterns([rec], TASKS)
        self.assertEqual(pat[0]["outcome"], "missed the planted weakness")
        self.assertEqual(pat[0]["planted_weakness"]["file"], "docshare/storage.py")


class Iteration2(unittest.TestCase):
    def test_verification_step_makes_first_submission_a_draft(self):
        tasks = list(TASKS.values())
        rec, tr = run_episode(DOC, "vulnerable", MockDetector(tasks), CallBudget(10), episode_id="e", condition="candidate",
                              repetition=1, instructions="i", guidance="", verification="Re-check each finding.")
        self.assertEqual((rec["model_calls"], rec["end_reason"], rec["verification"]["enabled"]), (3, "submitted", True))
        self.assertIn("Verification step:\nRe-check each finding.", tr[-1]["messages"][-1]["content"])
        self.assertTrue(rec["score"]["detected"])

    def test_draft_counts_when_limits_run_out(self):
        rec, _ = run_episode(DOC, "vulnerable", MockDetector(list(TASKS.values())), CallBudget(2), episode_id="e",
                             condition="candidate", repetition=1, instructions="i", guidance="", verification="v")
        self.assertEqual(rec["end_reason"], "submitted_draft")
        self.assertTrue(rec["score"]["detected"])

    def test_empty_verification_keeps_original_loop(self):
        rec, _ = run_episode(DOC, "vulnerable", MockDetector(list(TASKS.values())), CallBudget(10), episode_id="e",
                             condition="none", repetition=1, instructions="i", guidance="", verification="")
        self.assertEqual((rec["model_calls"], rec["verification"]["draft"]), (2, None))

    def test_feedback_validation(self):
        from secagent.propose import validate_feedback_proposal
        orig = {"harness/instructions.md": "orig", "harness/verification.md": ""}
        ok = {"change": {"harness/instructions.md": None, "harness/verification.md": "Check that each finding has a concrete path."}}
        self.assertEqual(validate_feedback_proposal(ok, [DOC], orig), [])
        copied = {"change": {"harness/verification.md": "Remember: containment check uses a plain string prefix often"}}
        self.assertTrue(any("copies" in x for x in validate_feedback_proposal(copied, [DOC], orig)))
        outside = {"change": {"secagent/agent.py": "x"}}
        self.assertTrue(validate_feedback_proposal(outside, [DOC], orig))

    def test_acceptance_rule(self):
        from secagent.iter2 import accept
        pair = lambda solved, fp=0, end="submitted": {"solved": solved, "twin_findings": fp, "vulnerable_end": end,
                                                     "twin_end": end, "complete": True}
        base = {"a": pair(True), "b": pair(False), "c": pair(False)}
        self.assertTrue(accept(base, {"a": pair(True), "b": pair(True), "c": pair(False)})[0])
        self.assertFalse(accept(base, {"a": pair(False), "b": pair(True), "c": pair(True)})[0])   # lost a solved pair
        self.assertFalse(accept(base, {"a": pair(True), "b": pair(True), "c": pair(False, fp=1)})[0])  # more twin findings
        self.assertFalse(accept(base, {"a": pair(True), "b": pair(False), "c": pair(False)})[0])  # no gain


if __name__ == "__main__":
    unittest.main()
