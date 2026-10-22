import copy
import json
import tempfile
import unittest
from pathlib import Path

from harness_mvp.benchmark import load_benchmark
from harness_mvp.contracts import ContractError
from harness_mvp.memory import (MockExtractor, build_bank, candidate_lessons, extraction_input, load_bank,
                                parse_lessons, write_bank)
from harness_mvp.mock import MockRepoModel
from harness_mvp.model import CallBudget
from harness_mvp.repo_agent import run_episode
from harness_mvp.runner import TrustedLocalRunner

TASKS = {t.task_id: t for t in load_benchmark()}
SENTINEL = "HIDDEN_SENTINEL_e8a1"
MODEL = MockRepoModel(list(TASKS.values()))
RUNNER = TrustedLocalRunner(Path(tempfile.mkdtemp()), MODEL.trusted_sources())


def ms_episode(task_id, rep=0):
    t = TASKS[task_id]
    rec, _, art = run_episode(t, MODEL, RUNNER, CallBudget(50), Path(tempfile.mkdtemp()) / "ws",
                              episode_id=f"ms-{task_id}-r{rep}", phase="memory_source", condition="baseline",
                              repetition=rep, guidance={})
    return t, rec, art


EPISODES = [ms_episode("ms_booking", 0), ms_episode("ms_booking", 1), ms_episode("ms_leaderboard", 0)]


def candidates(created_at="2026-01-01T00:00:00+00:00"):
    out = []
    for t, rec, art in EPISODES:
        raw, err = parse_lessons(MockExtractor().extract(extraction_input(t, rec, art["submitted"]))[0])
        out += candidate_lessons(raw, t, rec, art["submitted"], MockExtractor().config(), created_at, True)
    return out


class ExtractionInputTests(unittest.TestCase):
    def test_hidden_information_is_excluded(self):
        t, rec, art = EPISODES[0]
        poisoned = copy.deepcopy(rec)
        poisoned["hidden"] = {"outcome": "failed", "stdout": SENTINEL, "failed_tests": [SENTINEL]}
        poisoned["pre_fallback_hidden"] = {"stdout": SENTINEL}
        poisoned["hidden_success"] = SENTINEL
        poisoned["error_category"] = SENTINEL  # derived from hidden scores
        poisoned["principles"] = [SENTINEL]   # maintainer label
        payload = json.dumps(extraction_input(t, poisoned, art["submitted"]))
        self.assertNotIn(SENTINEL, payload)
        self.assertNotIn(t.hidden_test_source(), payload)
        self.assertNotIn("bug_note", payload)

    def test_input_is_a_fixed_whitelist(self):
        t, rec, art = EPISODES[0]
        self.assertEqual(sorted(extraction_input(t, rec, art["submitted"])),
                         ["agent_actions", "end_reason", "episode_id", "public_fallback_activated",
                          "public_test_feedback", "source_changes", "submitted_public_pass", "task"])

    def test_only_memory_source_episodes(self):
        t, rec, art = EPISODES[0]
        with self.assertRaises(ContractError):
            extraction_input(t, rec | {"split": "test"}, art["submitted"])

    def test_malformed_extractor_output(self):
        for text in ("no json", '{"lessons": "x"}', "{broken"):
            self.assertIsNone(parse_lessons(text)[0])


class BankTests(unittest.TestCase):
    def test_validation_flagging_and_dedup(self):
        cands = candidates()
        statuses = [c["status"] for c in cands]
        self.assertIn("rejected", statuses)
        self.assertIn("flagged", statuses)
        bank = build_bank(cands, "now")
        self.assertEqual(len(bank["memories"]), 2)  # boundary lesson seen twice -> merged
        boundary = next(m for m in bank["memories"] if "boundary" in m["scope"])
        self.assertEqual(boundary["evidence_episode_ids"], ["ms-ms_booking-r0", "ms-ms_booking-r1"])
        self.assertEqual(len(boundary["provenance"]["merged_candidates"]), 2)
        for m in bank["memories"]:
            self.assertEqual(m["provenance"]["source_split"], "memory_source")
            self.assertIn("unverified", m["status"])
            self.assertTrue(m["provenance"]["source_hashes"])
        flagged = [c for c in cands if c["status"] == "flagged"]
        bank_text = json.dumps(bank["memories"])
        self.assertTrue(all(c["lesson"] not in bank_text for c in flagged))

    def test_bank_is_deterministic_and_tamper_evident(self):
        a, b = build_bank(candidates(), "now"), build_bank(candidates(), "now")
        self.assertEqual(a["bank_hash"], b["bank_hash"])
        path = Path(tempfile.mkdtemp()) / "memory_bank.json"
        write_bank(path, a)
        with self.assertRaises(FileExistsError):
            write_bank(path, a)
        self.assertEqual(load_bank(path, a["bank_hash"])["bank_hash"], a["bank_hash"])
        tampered = json.loads(path.read_text())
        tampered["memories"][0]["lesson"] += " (edited)"
        path.write_text(json.dumps(tampered))
        with self.assertRaises(ContractError):
            load_bank(path)

    def test_non_memory_source_provenance_rejected(self):
        cands = candidates()
        cands[0]["provenance"]["source_split"] = "test"
        with self.assertRaises(ContractError):
            build_bank(cands, "now")


if __name__ == "__main__":
    unittest.main()
