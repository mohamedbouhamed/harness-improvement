import unittest

from harness_mvp.contracts import (ContractError, check_split_families, estimate_tokens, memory_id_for,
                                   validate_lesson, validate_memory_record, validate_task_entry)


def entry(**kw):
    base = {"id": "t1", "split": "test", "family": "f1", "title": "T", "statement": "S",
            "allowed_files": ["pkg/a.py"], "public_tests": "tests/test_public.py", "principles": []}
    return base | kw


GOOD_LESSON = {"trigger": "a contract describes half-open ranges", "lesson":
               "Check both ends of every range against the contract; treat the end as exclusive when stated.",
               "scope": "range and interval logic"}


class TaskContractTests(unittest.TestCase):
    def test_valid_entry(self):
        validate_task_entry(entry())

    def test_rejects_unsafe_paths_and_writable_tests(self):
        for bad in (["../x.py"], ["/etc/passwd"], ["pkg/../../x.py"]):
            with self.assertRaises(ContractError):
                validate_task_entry(entry(allowed_files=bad))
        with self.assertRaises(ContractError):
            validate_task_entry(entry(allowed_files=["tests/test_public.py"]))
        with self.assertRaises(ContractError):
            validate_task_entry(entry(split="holdout"))

    def test_families_cannot_cross_splits(self):
        check_split_families([entry(id="a", family="x"), entry(id="b", family="y", split="development")])
        with self.assertRaises(ContractError):
            check_split_families([entry(id="a", family="x"), entry(id="b", family="x", split="development")])


class LessonContractTests(unittest.TestCase):
    def test_valid_lesson(self):
        self.assertEqual(validate_lesson(GOOD_LESSON), ([], []))

    def test_malformed_lessons_rejected(self):
        self.assertTrue(validate_lesson({"trigger": "x"})[0])
        self.assertTrue(validate_lesson(GOOD_LESSON | {"lesson": "```python\nx = 1\n``` fix it like this"})[0])
        self.assertTrue(validate_lesson(GOOD_LESSON | {"extra": 1})[0])
        self.assertTrue(validate_lesson("not a dict")[0])

    def test_task_specific_content_flagged(self):
        lesson = GOOD_LESSON | {"lesson": "In first_free_slot, use an exclusive end when checking ranges."}
        errors, flags = validate_lesson(lesson, task_terms={"first_free_slot"})
        self.assertEqual(errors, [])
        self.assertTrue(flags)

    def test_plain_words_matching_module_names_are_not_flagged(self):
        self.assertEqual(validate_lesson(GOOD_LESSON | {"scope": "sorting and ranking of intervals"},
                                         task_terms={"ranking", "intervals"})[1], [])
        flags = validate_lesson(GOOD_LESSON | {"lesson": "Check how `ranking()` breaks ties before editing it."},
                                task_terms={"ranking"})[1]
        self.assertTrue(flags)

    def test_memory_id_is_content_derived(self):
        self.assertEqual(memory_id_for("A  b", "C"), memory_id_for("a b", "c"))
        self.assertNotEqual(memory_id_for("a", "b"), memory_id_for("a", "c"))

    def test_memory_provenance_must_be_memory_source(self):
        rec = {"memory_id": "m", **GOOD_LESSON, "evidence_episode_ids": ["e1"],
               "provenance": {"source_split": "test", "source_task_ids": ["t"], "extractor": {},
                              "created_at": "", "source_hashes": {}, "synthetic": True}}
        with self.assertRaises(ContractError):
            validate_memory_record(rec)
        rec["provenance"]["source_split"] = "memory_source"
        validate_memory_record(rec)

    def test_token_estimate_is_conservative(self):
        self.assertEqual(estimate_tokens(""), 0)
        self.assertEqual(estimate_tokens("abcd"), 2)


if __name__ == "__main__":
    unittest.main()
