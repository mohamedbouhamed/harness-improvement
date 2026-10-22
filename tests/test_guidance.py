import unittest

from harness_mvp.benchmark import load_benchmark
from harness_mvp.contracts import GUIDANCE_HEADING, RetrievalSettings, estimate_tokens
from harness_mvp.guidance import STATIC_CHECKLIST, build_guidance, retrieve, stem
from harness_mvp.repo_agent import first_user_message

TASKS = {t.task_id: t for t in load_benchmark()}
VIEW = {"title": "Sliding window limiter", "statement": "The window excludes its start; ends are inclusive."}


def mem(mid, trigger="The specification defines inclusive window ends.", lesson="Check both ends of every window.",
        scope="window boundaries"):
    return {"memory_id": mid, "trigger": trigger, "lesson": lesson, "scope": scope}


UNRELATED = mem("m-unrelated", "Payments are rounded to cents.", "Round half to even when totals are summed.",
                "currency arithmetic")


class RetrievalTests(unittest.TestCase):
    def test_threshold_allows_empty_retrieval(self):
        r = retrieve(VIEW, [UNRELATED], RetrievalSettings())
        self.assertEqual((r["memory_ids"], r["text"]), ([], ""))
        r = retrieve(VIEW, [mem("m1")], RetrievalSettings(min_score=99))
        self.assertEqual(r["memory_ids"], [])
        self.assertEqual(retrieve(VIEW, [], RetrievalSettings())["memory_ids"], [])

    def test_stable_order_and_tie_breaking(self):
        mems = [mem("m-b"), mem("m-a"), UNRELATED]
        r1 = retrieve(VIEW, mems, RetrievalSettings())
        r2 = retrieve(VIEW, list(reversed(mems)), RetrievalSettings())
        self.assertEqual(r1["memory_ids"], ["m-a", "m-b"])  # equal scores -> memory_id
        self.assertEqual(r1, r2)

    def test_max_memories_and_budget(self):
        mems = [mem(f"m{i}") for i in range(5)]
        self.assertEqual(len(retrieve(VIEW, mems, RetrievalSettings(max_memories=2))["memory_ids"]), 2)
        tight = retrieve(VIEW, mems, RetrievalSettings(budget_tokens=80))  # one lesson = 65
        self.assertLessEqual(estimate_tokens(tight["text"]), 80)
        self.assertEqual(len(tight["memory_ids"]), 1)
        self.assertEqual(retrieve(VIEW, mems, RetrievalSettings(budget_tokens=5))["memory_ids"], [])

    def test_stemming(self):
        self.assertEqual({stem(w) for w in ("ordered", "ordering", "orders")}, {"order"})
        self.assertEqual(stem("boundaries"), "boundary")
        self.assertEqual(stem("class"), "class")


class GuidanceTests(unittest.TestCase):
    def test_conditions_share_heading_position_and_budget(self):
        task = TASKS["test_rate_window"]
        s = RetrievalSettings()
        base = build_guidance("baseline", task.public_view(), [], s)
        static = build_guidance("static_guidance", task.public_view(), [], s)
        learned = build_guidance("learned_memory", task.public_view(), [mem("m1")], s, bank_hash="h")
        self.assertEqual(base["text"], "")
        self.assertNotIn(GUIDANCE_HEADING, first_user_message(task, base["text"]))
        self.assertEqual(static["text"], STATIC_CHECKLIST)
        for g in (static, learned):
            msg = first_user_message(task, g["text"])
            self.assertIn(GUIDANCE_HEADING + "\n" + g["text"], msg)
            self.assertLessEqual(estimate_tokens(g["text"]), s.budget_tokens)
        self.assertEqual(learned["memory_ids"], ["m1"])
        self.assertEqual(learned["source_hash"], "h")

    def test_empty_retrieval_gives_baseline_prompt(self):
        task = TASKS["test_config_merge"]
        g = build_guidance("learned_memory", task.public_view(), [UNRELATED], RetrievalSettings())
        self.assertEqual(first_user_message(task, g["text"]), first_user_message(task, ""))

    def test_static_checklist_must_fit_budget(self):
        with self.assertRaises(ValueError):
            build_guidance("static_guidance", VIEW, [], RetrievalSettings(budget_tokens=10))

    def test_retrieval_ignores_maintainer_labels(self):
        view = TASKS["test_rate_window"].public_view()
        self.assertNotIn("principles", view)
        self.assertNotIn("bug_note", view)


if __name__ == "__main__":
    unittest.main()
