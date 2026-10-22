"""Experiment v2: revision behavior, hidden-test isolation, metric denominators, budget, benchmark freezing."""
import contextlib
import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness_mvp import agent, main
from harness_mvp.metrics import revision_metrics
from harness_mvp.model import APIError, CallBudget, ModelResponse
from harness_mvp.revision import BASELINE_V2, RevisionConfig, run_revision_episode, space_v2
from harness_mvp.runner import ExecResult
from harness_mvp.tasks import (CALIBRATION_DIR, BenchmarkError, freeze_benchmark, load_tasks, sha256_text,
                               verify_frozen)

SENTINEL = "HIDDEN_SENTINEL_91c2"
TASK = next(t for t in load_tasks(CALIBRATION_DIR) if t.task_id == "cal_dedupe_events")
GOOD, BAD = "def dedupe_events(events):\n    return []\n", "def dedupe_events(events):\n    return None\n"


def fence(code):
    return f"```python\n{code}```"


class ScriptedModel:
    synthetic = True

    def __init__(self, replies, events=None):
        self.replies, self.seen, self.contexts = list(replies), [], []
        self.events = events if events is not None else []

    def complete(self, messages, context=None):
        self.events.append("model")
        self.seen.append([dict(m) for m in messages])
        self.contexts.append(dict(context or {}))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ModelResponse(reply, 10, 5, 15, 0.01)


class FakeRunner:
    """Public outcome per source; hidden outcome per source. Hidden output carries a sentinel."""
    timeout_s = 1

    def __init__(self, public, hidden, events=None):
        self.public, self.hidden = public, hidden
        self.events = events if events is not None else []
        self.calls = []

    def run(self, source, task, which):
        self.calls.append((which, sha256_text(source)))
        self.events.append(which)
        outcome = (self.public if which == "public" else self.hidden)[source]
        text = (f"{SENTINEL} hidden detail" if which == "hidden" else
                "FAIL: test_x (public_tests.PublicTests.test_x)\nAssertionError: public detail\nRan 3 tests")
        return ExecResult(outcome, 0 if outcome == "passed" else 1, "", text, 0.0, 3, 3,
                          [] if outcome == "passed" else [f"{which}_tests.T.test_{SENTINEL if which == 'hidden' else 'x'}"])


def run(config, replies, public, hidden, budget=None, **kw):
    model, runner = ScriptedModel(replies), FakeRunner(public, hidden)
    rec, transcript, cands = run_revision_episode(TASK, config, model, runner, budget or CallBudget(10),
                                                  phase="calibration", repetition=0, episode_id="e", **kw)
    return rec, model, runner, cands


ALWAYS_FULL = RevisionConfig("always_review", "full")
ALWAYS_MIN = RevisionConfig("always_review", "minimal")
FAIL_FULL = RevisionConfig("on_failure", "full")


class RevisionPolicyTests(unittest.TestCase):
    def test_space_and_baseline(self):
        self.assertEqual([c.config_id for c in space_v2()],
                         ["on_failure+minimal", "always_review+full", "always_review+minimal", "on_failure+full"])
        self.assertEqual(BASELINE_V2.config_id, "on_failure+minimal")

    def test_on_failure_stops_after_public_pass(self):
        rec, model, _, _ = run(BASELINE_V2, [fence(GOOD)], {GOOD: "passed"}, {GOOD: "failed"})
        self.assertEqual(rec["llm_calls"], 1)
        self.assertFalse(rec["revision_triggered"])
        self.assertFalse(rec["revised"])

    def test_on_failure_revises_after_public_failure(self):
        rec, _, _, _ = run(BASELINE_V2, [fence(BAD), fence(GOOD)], {BAD: "failed", GOOD: "passed"},
                           {BAD: "failed", GOOD: "passed"})
        self.assertEqual(rec["revision_reason"], "fix")
        self.assertEqual((rec["first_hidden_success"], rec["hidden_success"]), (False, True))

    def test_always_review_revises_after_pass_and_scores_regressed_final(self):
        rec, _, _, cands = run(ALWAYS_MIN, [fence(GOOD), fence(BAD)], {GOOD: "passed", BAD: "passed"},
                               {GOOD: "passed", BAD: "failed"})
        self.assertEqual(rec["revision_reason"], "review")
        self.assertEqual(rec["final_candidate_attempt"], 2)  # submitted even though it regressed
        self.assertTrue(rec["first_hidden_success"])
        self.assertFalse(rec["hidden_success"])
        self.assertTrue(rec["candidate_changed"])
        self.assertEqual(cands, {1: GOOD, 2: BAD})  # both candidates preserved

    def test_review_api_error_keeps_first_candidate(self):
        rec, _, _, _ = run(ALWAYS_FULL, [fence(GOOD), APIError("HTTP 400", 400, None, False)],
                           {GOOD: "passed"}, {GOOD: "passed"})
        self.assertTrue(rec["revision_triggered"])
        self.assertFalse(rec["revised"])
        self.assertEqual(rec["final_candidate_attempt"], 1)
        self.assertTrue(rec["hidden_success"])
        self.assertEqual(rec["api_errors"], 1)

    def test_unchanged_candidate_hidden_scored_once(self):
        rec, _, runner, _ = run(ALWAYS_FULL, [fence(GOOD), fence(GOOD)], {GOOD: "passed"}, {GOOD: "passed"})
        self.assertEqual([c for c, _ in runner.calls].count("hidden"), 1)
        self.assertFalse(rec["candidate_changed"])


class PromptTests(unittest.TestCase):
    def test_first_attempt_identical_across_configs(self):
        firsts = []
        for cfg in space_v2():
            _, model, _, _ = run(cfg, [fence(BAD), fence(BAD)], {BAD: "failed"}, {BAD: "failed"})
            firsts.append(model.seen[0])
        self.assertTrue(all(f == firsts[0] for f in firsts))

    def test_minimal_and_full_revision_context(self):
        _, m_min, _, _ = run(BASELINE_V2, [fence(BAD), fence(BAD)], {BAD: "failed"}, {BAD: "failed"})
        _, m_full, _, _ = run(FAIL_FULL, [fence(BAD), fence(BAD)], {BAD: "failed"}, {BAD: "failed"})
        minimal, full = m_min.seen[1][-1]["content"], m_full.seen[1][-1]["content"]
        for text in (minimal, full):  # shared: interface, current candidate, latest public feedback
            self.assertIn(TASK.signature, text)
            self.assertIn(BAD, text)
            self.assertIn("public detail", text)
        self.assertNotIn(TASK.statement, minimal)
        self.assertNotIn("## Previous attempts", minimal)
        self.assertNotIn(TASK.public_tests_source, minimal)
        self.assertIn(TASK.statement, full)
        self.assertIn("## Previous attempts", full)
        self.assertIn("Attempt 1: reply=fenced; result=public_fail", full)
        self.assertEqual(len(m_full.seen[1]), 2)  # fresh single-turn request, not a continuation


class HiddenIsolationTests(unittest.TestCase):
    def test_hidden_never_in_prompts_and_scored_after_last_call(self):
        for cfg in space_v2():
            events = []
            model = ScriptedModel([fence(BAD), fence(GOOD)], events)
            runner = FakeRunner({BAD: "passed", GOOD: "passed"}, {BAD: "failed", GOOD: "failed"}, events)
            run_revision_episode(TASK, cfg, model, runner, CallBudget(10), phase="calibration",
                                 repetition=0, episode_id="e")
            prompts = json.dumps(model.seen) + json.dumps(model.contexts)
            self.assertNotIn(SENTINEL, prompts)
            self.assertNotIn("hidden_tests", prompts)
            last_model = max(i for i, e in enumerate(events) if e == "model")
            self.assertTrue(all(i > last_model for i, e in enumerate(events) if e == "hidden"), cfg)

    def test_hidden_outcomes_do_not_change_decisions(self):
        for cfg in space_v2():
            outs = []
            for hidden_result in ("passed", "failed"):
                rec, model, _, _ = run(cfg, [fence(BAD), fence(GOOD)], {BAD: "passed", GOOD: "passed"},
                                       {BAD: hidden_result, GOOD: hidden_result})
                outs.append((model.seen, rec["revision_triggered"], rec["final_candidate_hash"]))
            self.assertEqual(outs[0], outs[1], cfg)


class BudgetTests(unittest.TestCase):
    def test_retries_count_against_budget_and_block_revision(self):
        replies = [APIError("HTTP 503", 503, None, True), fence(GOOD), fence(GOOD)]
        with mock.patch.object(agent, "sleep"):
            rec, model, _, _ = run(ALWAYS_FULL, replies, {GOOD: "passed"}, {GOOD: "passed"},
                                   budget=CallBudget(2), api_retries=1)
        self.assertEqual(rec["llm_calls"], 2)  # the 503 and its re-send
        self.assertEqual(len(model.seen), 2)
        self.assertEqual(rec["fatal"], "budget_exhausted")
        self.assertTrue(rec["hidden_success"])  # first candidate still scored

    def test_zero_budget_sends_nothing(self):
        rec, model, _, _ = run(BASELINE_V2, [fence(GOOD)], {GOOD: "passed"}, {GOOD: "passed"}, budget=CallBudget(0))
        self.assertEqual(model.seen, [])
        self.assertEqual(rec["error_category"], "budget_exhausted")


def ep(first_pub, first_hid, revised, final, calls=1, tokens=10, category="none"):
    return {"task_id": "t", "first_attempt_public_pass": first_pub, "first_hidden_success": first_hid,
            "revision_triggered": revised, "revised": revised, "hidden_success": final, "llm_calls": calls,
            "input_tokens": tokens, "output_tokens": tokens, "total_tokens": tokens, "model_latency_s": 0.1,
            "episode_latency_s": 1.0, "wait_s": 0, "api_errors": 0, "error_category": category, "timeouts": 0}


class MetricTests(unittest.TestCase):
    def test_denominators(self):
        eps = [ep(False, False, True, True, 2),    # recovered
               ep(True, False, True, True, 2),     # review benefit (also recovery)
               ep(True, True, True, False, 2),     # regression
               ep(True, True, False, True),        # no revision
               ep(False, False, False, False, 0, None, "api_error")]  # failed episode, still counted
        m = revision_metrics(eps)
        self.assertEqual((m["final_hidden_success"]["k"], m["final_hidden_success"]["n"]), (3, 5))
        self.assertEqual((m["revision_activation"]["k"], m["revision_activation"]["n"]), (3, 5))
        self.assertEqual((m["recovery"]["k"], m["recovery"]["n"]), (2, 2))
        self.assertEqual((m["review_benefit"]["k"], m["review_benefit"]["n"]), (1, 1))
        self.assertEqual((m["regression"]["k"], m["regression"]["n"]), (1, 1))
        self.assertEqual(m["model_calls"]["total"], 7)
        self.assertIsNone(m["total_tokens"]["total"])  # missing usage is never zero
        self.assertEqual(m["infra_error_episodes"], 1)

    def test_no_eligible_episodes_is_none(self):
        m = revision_metrics([ep(True, True, False, True)])
        self.assertIsNone(m["recovery"])
        self.assertIsNone(m["review_benefit"])
        self.assertIsNone(m["regression"])


def make_benchmark(root: Path, splits: dict[str, str], families: dict[str, str] | None = None) -> Path:
    """Copy calibration tasks into a temporary benchmark with the given splits."""
    manifest = json.loads((CALIBRATION_DIR / "manifest.json").read_text())
    entries = []
    for e in manifest["tasks"]:
        if e["id"] in splits:
            shutil.copytree(CALIBRATION_DIR / e["id"], root / e["id"])
            entries.append(dict(e, split=splits[e["id"]], family=(families or {}).get(e["id"], e["family"])))
    (root / "manifest.json").write_text(json.dumps({"tasks": entries}))
    return root


class BenchmarkFreezeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.no_cal = self.tmp / "no_calibration"

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def test_freeze_verify_and_detect_changes(self):
        b = make_benchmark(self.tmp / "b", {"cal_ttl_cache": "optimization", "cal_dedupe_events": "test"})
        freeze_benchmark(b, self.no_cal)
        tasks, _ = verify_frozen(b)
        self.assertEqual(len(tasks), 2)
        with self.assertRaises(BenchmarkError):
            freeze_benchmark(b, self.no_cal)  # never re-frozen in place
        (b / "cal_ttl_cache" / "hidden_tests.py").write_text("# changed\n")
        with self.assertRaises(BenchmarkError):
            verify_frozen(b)

    def test_rejects_family_crossing_splits_and_calibration_overlap(self):
        b = make_benchmark(self.tmp / "b", {"cal_ttl_cache": "optimization", "cal_dedupe_events": "test"},
                           {"cal_ttl_cache": "same", "cal_dedupe_events": "same"})
        with self.assertRaises(BenchmarkError):
            freeze_benchmark(b, self.no_cal)
        c = make_benchmark(self.tmp / "c", {"cal_ttl_cache": "optimization", "cal_dedupe_events": "test"})
        with self.assertRaises(BenchmarkError):
            freeze_benchmark(c)  # overlaps the real calibration set


class EndToEndMockTests(unittest.TestCase):
    def test_v2_mock_run_and_never_overwrite(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            b = make_benchmark(tmp / "bench", {"cal_ttl_cache": "optimization", "cal_retry_schedule": "optimization",
                                               "cal_install_order": "test", "cal_dedupe_events": "test"})
            freeze_benchmark(b, tmp / "no_calibration")
            argv = ["run", "--experiment", "v2", "--mock", "--runner", "local-trusted", "--preset", "pilot",
                    "--benchmark-dir", str(b), "--output", str(tmp / "run")]
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main.main(argv), 0)
                self.assertEqual(main.main(argv), 0)
            out, second = tmp / "run", tmp / "run-2"
            self.assertTrue(second.exists())  # the first run was not overwritten
            manifest = json.loads((out / "manifest.json").read_text())
            self.assertEqual(manifest["experiment"], "v2")
            self.assertIn("revision", manifest["prompt_templates"])
            self.assertEqual(manifest["planned_max_model_calls"], 2 * (4 * 2 * 1 + 2 * 2 * 1))
            eps = [json.loads(l) for l in (out / "episodes.jsonl").read_text().splitlines()]
            self.assertTrue(all(e["split"] == "optimization" for e in eps if e["phase"] == "optimization"))
            self.assertTrue(all(e["split"] == "test" for e in eps if e["phase"] == "heldout"))
            selection = json.loads((out / "selection.json").read_text())
            first_heldout = min(i for i, e in enumerate(eps) if e["phase"] == "heldout")
            self.assertTrue(all(e["phase"] == "optimization" for e in eps[:first_heldout]))
            self.assertIn(selection["selected_config_id"], {c.config_id for c in space_v2()})
            self.assertTrue((out / "metrics.json").exists())
            self.assertTrue(any((out / "candidates").iterdir()))
            self.assertIn("SYNTHETIC", (out / "report.md").read_text())
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
