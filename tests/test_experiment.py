"""Study-level checks on the end-to-end mock workflow (synthetic; trusted local runner)."""
import argparse
import json
import tempfile
import unittest
from pathlib import Path

from harness_mvp.contracts import CONDITIONS
from harness_mvp.experiment import Study, StudyError, plan, read_json, read_jsonl
from harness_mvp.benchmark import load_benchmark
from harness_mvp.contracts import AgentLimits
from harness_mvp.study_report import build_study_report


def args(**kw):
    base = dict(mock=True, runner="local-trusted", max_model_calls=None, api_retries=0, retry_wait=10.0,
                min_interval=0.0, omit_prompts=False, benchmark=None)
    return argparse.Namespace(**(base | kw))


def new_study(**kw):
    d = Path(tempfile.mkdtemp()) / "study"
    return Study(args(**kw), d, create=True)


def outcomes(study_dir: Path) -> list:
    eps = read_jsonl(study_dir / "eval-test" / "episodes.jsonl")
    return [(e["episode_id"], e["order_position"], e["hidden_success"], e["fallback_activated"],
             tuple(e["guidance"]["memory_ids"]), e["error_category"]) for e in eps]


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.study = new_study()
        cls.bank = cls.study.build_memory(reps=1)
        cls.study.freeze(test_reps=2)
        cls.eval_dir = cls.study.evaluate("test", None)
        cls.eps = read_jsonl(cls.eval_dir / "episodes.jsonl")

    def test_scenarios_are_exercised(self):
        by = {(e["task_id"], e["condition"], e["repetition"]): e for e in self.eps}
        useful = by[("test_rate_window", "learned_memory", 0)]
        self.assertTrue(useful["guidance"]["memory_ids"] and useful["hidden_success"])
        self.assertFalse(by[("test_rate_window", "baseline", 0)]["hidden_success"])
        irrelevant = by[("test_config_merge", "learned_memory", 0)]
        self.assertTrue(irrelevant["guidance"]["memory_ids"])           # retrieved, but irrelevant
        self.assertFalse(irrelevant["hidden_success"])                  # failed repair
        empty = by[("test_standings", "learned_memory", 0)]
        self.assertEqual(empty["guidance"]["memory_ids"], [])           # empty retrieval
        self.assertTrue(empty["fallback_activated"] and empty["hidden_success"])  # regression + fallback
        self.assertEqual(by[("test_rate_window", "static_guidance", 1)]["error_category"], "api_error")

    def test_every_episode_has_fresh_state_and_records_context(self):
        self.assertEqual(len(self.eps), 3 * 3 * 2)
        self.assertEqual(len({e["episode_id"] for e in self.eps}), len(self.eps))
        for e in self.eps:
            g = e["guidance"]
            self.assertLessEqual(g["estimated_tokens"], g["budget_tokens"])
            if e["condition"] == "learned_memory":
                self.assertEqual(g["source_hash"], self.bank["bank_hash"])
            if e["condition"] == "baseline":
                self.assertEqual(g["text"], "")

    def test_condition_order_is_interleaved(self):
        firsts = {e["condition"] for e in self.eps if e["order_position"] == 0}
        self.assertEqual(firsts, set(CONDITIONS))

    def test_memories_only_from_memory_source(self):
        ms_ids = {e["episode_id"] for e in read_jsonl(self.study.dir / "memory_source" / "episodes.jsonl")}
        for m in self.bank["memories"]:
            self.assertEqual(m["provenance"]["source_split"], "memory_source")
            self.assertTrue(set(m["evidence_episode_ids"]) <= ms_ids)
        inputs = read_jsonl(self.study.dir / "extraction" / "inputs.jsonl")
        self.assertTrue(all("hidden" not in json.dumps(i) for i in inputs))

    def test_second_heldout_evaluation_refused(self):
        with self.assertRaises(StudyError):
            self.study.evaluate("test", None)

    def test_frozen_protocol_written_once_and_selection_blocked(self):
        with self.assertRaises(StudyError):
            self.study.freeze(test_reps=2)
        with self.assertRaises(StudyError):
            self.study.select(reps=1)

    def test_report_labels_synthetic_results(self):
        report = build_study_report(self.study.dir)
        self.assertIn("SYNTHETIC", report)
        self.assertIn("[SYNTHETIC]", report)
        self.assertIn("Memory-construction cost", report)


class FreezeAndIsolationTests(unittest.TestCase):
    def test_heldout_requires_freeze(self):
        study = new_study()
        study.build_memory(reps=1)
        with self.assertRaises(StudyError):
            study.evaluate("test", None)

    def test_tampered_bank_refused(self):
        study = new_study()
        study.build_memory(reps=1)
        study.freeze(test_reps=1)
        path = study.dir / "memory_bank.json"
        bank = json.loads(path.read_text())
        bank["memories"][0]["lesson"] += " Also check everything else."
        path.write_text(json.dumps(bank))
        with self.assertRaises(Exception):
            study.evaluate("test", None)
        self.assertFalse((study.dir / "eval-test").exists())

    def test_stages_and_studies_are_never_overwritten(self):
        study = new_study()
        study.build_memory(reps=1)
        with self.assertRaises(StudyError):
            study.build_memory(reps=1)
        with self.assertRaises(StudyError):
            Study(args(), study.dir, create=True)
        manifest = read_json(study.dir / "memory_source" / "manifest.json")
        self.assertEqual(manifest["status"], "completed")

    def test_mock_bank_refused_in_live_mode_contract(self):
        study = new_study()
        study.build_memory(reps=1)
        study.args.mock = False  # simulate a live evaluation reading a synthetic bank
        with self.assertRaises(StudyError):
            study.bank()

    def test_global_budget_stops_the_run(self):
        study = new_study(max_model_calls=4)
        with self.assertRaises(StudyError):  # memory-source stage aborts: no bank is built
            study.build_memory(reps=1)
        manifest = read_json(study.dir / "memory_source" / "manifest.json")
        self.assertIn("budget_exhausted", manifest["status"])
        self.assertFalse((study.dir / "memory_bank.json").exists())

    def test_development_selection_uses_public_outcomes(self):
        study = new_study()
        study.build_memory(reps=1)
        result = study.select(reps=1)
        self.assertFalse(result["uses_hidden_tests"])
        self.assertIn(result["selected_min_score"], (2, 3, 4))
        self.assertTrue(all("public_pass" in s for s in result["stats"].values()))
        frozen = study.freeze(test_reps=1)
        self.assertEqual(frozen["retrieval_settings"]["min_score"], result["selected_min_score"])

    def test_mock_workflow_is_deterministic(self):
        runs = []
        for _ in range(2):
            study = new_study()
            bank = study.build_memory(reps=1)
            study.freeze(test_reps=1)
            study.evaluate("test", None)
            runs.append((bank["content_hash"], outcomes(study.dir)))  # bank_hash includes timestamps
        self.assertEqual(runs[0], runs[1])

    def test_plan_counts_every_stage(self):
        p = plan(load_benchmark(), AgentLimits(), memory_reps=1, test_reps=2, select=True, select_reps=1)
        self.assertEqual(p["held-out evaluation"], 3 * 3 * 2 * 8)
        self.assertEqual(p["extraction"], 2)
        self.assertEqual(p["total"], sum(v for k, v in p.items() if k != "total"))


if __name__ == "__main__":
    unittest.main()
