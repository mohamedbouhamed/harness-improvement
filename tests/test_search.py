import unittest

from harness_mvp.search import BASELINE, HarnessConfig, sample_alternatives, search_space, select_winner


def ep(config_id, task, solved, tokens, phase="optimization", split="optimization"):
    return {"episode_id": f"{config_id}-{task}", "phase": phase, "split": split, "config_id": config_id,
            "task_id": task, "hidden_success": solved, "first_attempt_public_pass": solved,
            "llm_calls": 1, "total_tokens": tokens}


A = HarnessConfig("structured", "full")
B = HarnessConfig("structured", "compact")


class SamplingTests(unittest.TestCase):
    def test_space_has_four_configs(self):
        self.assertEqual(len(search_space()), 4)

    def test_sampling_is_stable_distinct_and_excludes_baseline(self):
        for seed in range(50):
            first = sample_alternatives(seed, 2)
            self.assertEqual(first, sample_alternatives(seed, 2))
            self.assertEqual(len(set(first)), 2)
            self.assertNotIn(BASELINE, first)

    def test_sampling_bounds(self):
        self.assertEqual(len(sample_alternatives(0, 3)), 3)
        with self.assertRaises(ValueError):
            sample_alternatives(0, 4)


class SelectionTests(unittest.TestCase):
    def test_most_solved_wins(self):
        eps = [ep(BASELINE.config_id, "t1", True, 10), ep(BASELINE.config_id, "t2", False, 10),
               ep(A.config_id, "t1", True, 999), ep(A.config_id, "t2", True, 999)]
        sel = select_winner([BASELINE, A], eps)
        self.assertEqual(sel["selected_config_id"], A.config_id)
        self.assertEqual(sel["decided_by"], "solved")

    def test_tokens_break_ties(self):
        eps = [ep(A.config_id, "t1", True, 300), ep(B.config_id, "t1", True, 200)]
        sel = select_winner([A, B], eps)
        self.assertEqual(sel["selected_config_id"], B.config_id)
        self.assertEqual(sel["decided_by"], "total_tokens")

    def test_missing_tokens_skip_tiebreak_for_everyone(self):
        # B has fewer known tokens but A has a missing value -> tokens ignored, config_id decides.
        eps = [ep(A.config_id, "t1", True, None), ep(B.config_id, "t1", True, 1)]
        sel = select_winner([B, A], eps)
        self.assertFalse(sel["token_tiebreak_available"])
        self.assertEqual(sel["decided_by"], "config_id")
        self.assertEqual(sel["selected_config_id"], min(A.config_id, B.config_id))
        ranked = {r["config_id"]: r["total_tokens"] for r in sel["ranking"]}
        self.assertIsNone(ranked[A.config_id])  # never replaced by zero

    def test_infra_errors_mark_selection_unreliable(self):
        good = [ep(A.config_id, "t1", True, 1), ep(B.config_id, "t1", True, 1)]
        self.assertTrue(select_winner([A, B], good)["reliable"])
        bad = dict(ep(B.config_id, "t1", False, None), error_category="api_error")
        sel = select_winner([A, B], [good[0], bad])
        self.assertFalse(sel["reliable"])
        self.assertEqual(sel["infra_error_episodes"], [bad["episode_id"]])

    def test_rejects_heldout_data(self):
        eps = [ep(A.config_id, "t1", True, 1), ep(A.config_id, "h1", True, 1, phase="heldout", split="test")]
        with self.assertRaises(ValueError):
            select_winner([A], eps)


if __name__ == "__main__":
    unittest.main()
