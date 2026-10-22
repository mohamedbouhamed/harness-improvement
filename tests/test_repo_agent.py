import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness_mvp import llm
from harness_mvp.benchmark import HIDDEN_TEST_PATH, load_benchmark
from harness_mvp.contracts import AgentLimits
from harness_mvp.model import APIError, CallBudget, ModelResponse
from harness_mvp.repo_agent import run_episode
from harness_mvp.runner import ExecResult
from harness_mvp.tools import parse_tool_calls
from harness_mvp.workspace import Workspace, WorkspaceError, execution_files

TASKS = {t.task_id: t for t in load_benchmark()}
TASK = TASKS["test_standings"]
SENTINEL = "HIDDEN_SENTINEL_31c9"
GOOD_MARK, BAD_MARK = "# good", "# bad"


def calls(*cs):
    return json.dumps({"actions": [{"action": n, **a} for n, a in cs]})


def write(mark):
    return ("write_file", {"path": "league/table.py", "content": f"{mark}\n"})


class ScriptedModel:
    synthetic = True

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def complete(self, messages, context=None):
        self.seen.append(json.dumps(messages))
        if not self.replies:
            return ModelResponse(calls(("list_files", {})), 5, 5, 10, 0.0)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return ModelResponse(reply, 5, 5, 10, 0.0)


class SpyRunner:
    """Public outcome depends on GOOD/BAD marks; hidden runs emit a sentinel and record their timing."""
    timeout_s = 1

    def __init__(self, model=None, hidden_outcome="failed"):
        self.events, self.model, self.hidden_outcome = [], model, hidden_outcome

    def run_files(self, files, test_rel, label="x"):
        which = "hidden" if test_rel == HIDDEN_TEST_PATH else "public"
        self.events.append((which, len(self.model.seen) if self.model else None, dict(files)))
        if which == "hidden":
            assert TASK.public_tests not in files
            return ExecResult(self.hidden_outcome, 1, SENTINEL, SENTINEL, 0.0, 6, 6, [f"x.{SENTINEL}"])
        ok = GOOD_MARK in files["league/table.py"]
        return ExecResult("passed" if ok else "failed", 0 if ok else 1, "", "FAIL: test_x (public)", 0.0, 2, 2,
                          [] if ok else ["tests.test_public.PublicTests.test_x"])


def episode(model, runner=None, limits=AgentLimits(), budget=None, **kw):
    runner = runner or SpyRunner(model)
    root = Path(tempfile.mkdtemp()) / "ws"
    rec, transcript, art = run_episode(TASK, model, runner, budget or CallBudget(100), root, episode_id="e",
                                       phase="test", condition="baseline", repetition=0, guidance={},
                                       limits=limits, **kw)
    return rec, transcript, art, runner


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp()) / "ws"
        self.ws = Workspace(self.root, TASK)

    def test_hidden_tests_and_solutions_are_not_in_the_workspace(self):
        files = self.ws.list_files()
        self.assertNotIn("tests/test_hidden.py", files)
        self.assertFalse(any("hidden" in f or "solution" in f or "mock" in f for f in files))
        contents = "".join(self.ws.read(f) for f in files)
        self.assertNotIn(TASK.hidden_test_source(), contents)

    def test_path_traversal_rejected(self):
        for bad in ("../manifest.json", "/etc/passwd", "league/../../x", "~/x", "a\\b", "", "a\x00b"):
            with self.assertRaises(WorkspaceError, msg=bad):
                self.ws.read(bad)

    def test_symlink_escape_rejected(self):
        outside = Path(tempfile.mkdtemp()) / "secret.txt"
        outside.write_text("secret")
        os.symlink(outside, self.root / "league" / "link.py")
        with self.assertRaises(WorkspaceError):
            self.ws.read("league/link.py")
        os.symlink(outside.parent, self.root / "linkdir")
        with self.assertRaises(WorkspaceError):
            self.ws.read("linkdir/secret.txt")
        self.assertNotIn("league/link.py", self.ws.list_files())

    def test_only_allowed_files_are_writable(self):
        for bad in ("tests/test_public.py", "league/__init__.py", "league/new.py"):
            with self.assertRaises(WorkspaceError):
                self.ws.write(bad, "x")
        self.ws.write("league/table.py", "x = 1\n")
        self.assertEqual(self.ws.read("league/table.py"), "x = 1\n")

    def test_executions_use_pristine_tests(self):
        (self.root / "tests" / "test_public.py").write_text("tampered")  # e.g. a bypass outside the tools
        files, rel = execution_files(TASK, self.ws.candidate(), "public")
        self.assertEqual(files[rel], TASK.repo_files()[TASK.public_tests])
        hidden_files, hrel = execution_files(TASK, self.ws.candidate(), "hidden")
        self.assertEqual(hrel, HIDDEN_TEST_PATH)
        self.assertNotIn(TASK.public_tests, hidden_files)
        with self.assertRaises(WorkspaceError):
            execution_files(TASK, {"tests/test_public.py": "x"}, "public")


class ToolParsingTests(unittest.TestCase):
    def test_accepted_forms(self):
        self.assertEqual(parse_tool_calls('```json\n{"name": "list_files"}\n```', 4)[0][0]["name"], "list_files")
        self.assertEqual(len(parse_tool_calls(calls(("list_files", {}), ("submit", {})), 4)[0]), 2)
        self.assertEqual(parse_tool_calls('[{"name": "submit", "args": {}}]', 4)[0][0]["name"], "submit")

    def test_pilot1_reply_shapes_now_parse(self):
        # Exact shape Gemini produced in pilot 1: arguments next to "name" inside "tool_calls".
        text = ('{"tool_calls": [{"name": "list_files", "args": {}}, {"name": "read_file", "path": "ratelimit/window.py"}, '
                '{"name": "read_file", "path": "ratelimit/limiter.py"}, {"name": "read_file", "path": "tests/test_public.py"}]}')
        out, err = parse_tool_calls(text, 4)
        self.assertIsNone(err)
        self.assertEqual([c["args"] for c in out[1:]], [{"path": "ratelimit/window.py"}, {"path": "ratelimit/limiter.py"},
                                                        {"path": "tests/test_public.py"}])

    def test_canonical_and_nested_forms_are_equivalent(self):
        flat = parse_tool_calls('{"actions": [{"action": "read_file", "path": "a.py"}]}', 4)[0]
        nested = parse_tool_calls('{"actions": [{"tool": "read_file", "args": {"path": "a.py"}}]}', 4)[0]
        self.assertEqual(flat, nested)
        self.assertEqual(flat, [{"name": "read_file", "args": {"path": "a.py"}}])

    def test_errors_show_the_expected_format(self):
        for text in ("", '{"actions": [{"action": "read_file"}]}', "not json"):
            self.assertIn('"actions"', parse_tool_calls(text, 4)[1])

    def test_system_prompt_avoids_native_function_calling_words(self):
        from harness_mvp.contracts import AgentLimits
        from harness_mvp.repo_agent import system_prompt
        text = system_prompt(AgentLimits()).lower()
        self.assertNotIn("tool_calls", text)
        self.assertNotIn("function", text)

    def test_rejected_forms(self):
        for text in ("no json here", '{"name": "rm_rf"}', '{"name": "read_file", "args": {}}',
                     calls(("submit", {}), ("list_files", {})), calls(*[("list_files", {})] * 5)):
            calls_, err = parse_tool_calls(text, 4)
            self.assertIsNone(calls_)
            self.assertTrue(err)


class EpisodeTests(unittest.TestCase):
    def test_fallback_to_latest_public_passing_candidate(self):
        model = ScriptedModel([calls(write(GOOD_MARK), ("run_public_tests", {})),
                               calls(write(BAD_MARK), ("submit", {}))])
        rec, _, art, runner = episode(model)
        self.assertTrue(rec["fallback_activated"])
        self.assertFalse(rec["final_public_pass"])
        self.assertTrue(rec["submitted_public_pass"])
        self.assertIn(GOOD_MARK, art["submitted"]["league/table.py"])
        self.assertIn(BAD_MARK, art["final"]["league/table.py"])  # pre-fallback candidate preserved
        self.assertIsNotNone(rec["pre_fallback_hidden"])
        hidden_runs = [e for e in runner.events if e[0] == "hidden"]
        self.assertEqual(len(hidden_runs), 2)

    def test_fallback_ignores_hidden_scores(self):
        model = ScriptedModel([calls(write(GOOD_MARK), ("run_public_tests", {})),
                               calls(write(BAD_MARK), ("submit", {}))])
        for hidden in ("passed", "failed"):  # identical choice whatever the hidden outcome
            rec2, _, art, _ = episode(ScriptedModel(list(model.replies)), SpyRunner(hidden_outcome=hidden))
            self.assertTrue(rec2["fallback_activated"])
            self.assertIn(GOOD_MARK, art["submitted"]["league/table.py"])

    def test_no_fallback_without_an_earlier_public_pass(self):
        model = ScriptedModel([calls(write(BAD_MARK), ("run_public_tests", {})), calls(("submit", {}))])
        rec, _, art, _ = episode(model)
        self.assertFalse(rec["fallback_activated"])
        self.assertIn(BAD_MARK, art["submitted"]["league/table.py"])
        self.assertEqual(rec["error_category"], "public_test_failure")

    def test_untested_final_candidate_is_checked_by_the_harness(self):
        model = ScriptedModel([calls(write(GOOD_MARK), ("submit", {}))])
        rec, *_ = episode(model)
        self.assertEqual([s["source"] for s in rec["snapshots"]], ["harness_final_check"])
        self.assertTrue(rec["submitted_public_pass"])
        self.assertEqual(rec["test_runs"], 0)

    def test_hidden_results_never_reach_the_model_and_run_after_last_call(self):
        model = ScriptedModel([calls(write(GOOD_MARK), ("run_public_tests", {})),
                               calls(write(BAD_MARK), ("run_public_tests", {})), calls(("submit", {}))])
        rec, transcript, _, runner = episode(model)
        self.assertFalse(any(SENTINEL in s for s in model.seen))
        self.assertFalse(any(SENTINEL in json.dumps(t["messages"]) for t in transcript))
        calls_made = len(model.seen)
        for which, calls_before, files in runner.events:
            if which == "hidden":
                self.assertEqual(calls_before, calls_made)  # only after the last model call
            else:
                self.assertNotIn(HIDDEN_TEST_PATH, files)
        self.assertNotIn(TASK.hidden_test_source(), "".join(model.seen))

    def test_model_call_limit(self):
        model = ScriptedModel([])  # never submits
        rec, *_ = episode(model, limits=AgentLimits(max_model_calls=3))
        self.assertEqual(rec["model_calls"], 3)
        self.assertEqual(rec["end_reason"], "model_call_limit")

    def test_tool_call_and_test_run_limits(self):
        model = ScriptedModel([calls(*[("run_public_tests", {})] * 3)] * 3)
        rec, *_ = episode(model, limits=AgentLimits(max_tool_calls=5, max_test_runs=2))
        self.assertEqual(rec["tool_calls"], 5)
        self.assertEqual(rec["test_runs"], 2)
        self.assertEqual(rec["end_reason"], "tool_call_limit")

    def test_parse_error_is_reported_and_counted(self):
        model = ScriptedModel(["I think the bug is in the sort.", calls(("submit", {}))])
        rec, *_ = episode(model)
        self.assertEqual(rec["parse_errors"], 1)
        self.assertIn("Invalid reply", model.seen[1])

    def test_global_budget_counts_retries_and_stops_episode(self):
        errors = [APIError("HTTP 503", 503, None, True)] * 5
        with mock.patch.object(llm, "sleep"):
            rec, *_ = episode(ScriptedModel(errors), budget=CallBudget(2), api_retries=4)
        self.assertEqual(rec["model_calls"], 2)
        self.assertEqual(rec["fatal"], "budget_exhausted")

    def test_api_error_ends_episode_and_scores_workspace(self):
        model = ScriptedModel([calls(write(GOOD_MARK), ("run_public_tests", {})), APIError("HTTP 500", 500)])
        rec, *_ = episode(model)
        self.assertEqual(rec["end_reason"], "api_error")
        self.assertEqual(rec["error_category"], "api_error")
        self.assertTrue(rec["submitted_public_pass"])

    def test_writes_to_tests_are_rejected_through_tools(self):
        model = ScriptedModel([calls(("write_file", {"path": "tests/test_public.py", "content": "x"}),
                                     ("read_file", {"path": "../manifest.json"})), calls(("submit", {}))])
        rec, *_ = episode(model)
        self.assertEqual([c["ok"] for c in rec["turns"][0]["tool_calls"]], [False, False])
        self.assertEqual(rec["edits"], 0)


if __name__ == "__main__":
    unittest.main()
