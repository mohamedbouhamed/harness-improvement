"""Deterministic mock backends. Every output is SYNTHETIC: it verifies orchestration, not effectiveness.

MockRepoModel plays a scripted tool-using agent per task `mock_scenario` (maintainer metadata):
  fix                  inspect -> write the reference fix -> run tests -> submit
  partial              writes a partial fix that passes public tests but not hidden ones
  memory_helps         reference fix only if an injected *learned memory* mentions boundaries,
                       otherwise the partial fix (synthetic stand-in for "a useful memory")
  regression_fallback  passing fix + tests, then an untested "refactor" that breaks a public test
  fail                 a wrong fix twice, then submit (failed repair)
`mock_api_error` {"condition", "repetition"} makes the first request of that episode fail (HTTP 503).
"""
from __future__ import annotations

import json

from .model import APIError, ModelResponse


def _calls(*calls) -> str:
    return "```json\n" + json.dumps({"tool_calls": [{"name": n, "args": a} for n, a in calls]}) + "\n```"


class MockRepoModel:
    synthetic = True

    def __init__(self, tasks):
        self.tasks = {t.task_id: t for t in tasks}

    def trusted_sources(self) -> list[str]:
        return [s for t in self.tasks.values() for s in t.all_sources()]

    def _writes(self, task, files: dict[str, str]) -> list[tuple[str, dict]]:
        original = task.original_candidate()
        return [("write_file", {"path": p, "content": c}) for p, c in sorted(files.items()) if c != original[p]]

    def _script(self, task, ctx) -> str:
        turn, scenario = ctx["turn"], task.mock_scenario
        if turn == 1:
            return _calls(("list_files", {}), *[("read_file", {"path": p}) for p in task.allowed_files])
        solution, partial = task.solution_files(), task.mock_variant("partial")
        if scenario == "memory_helps":
            helped = ctx["condition"] == "learned_memory" and "boundar" in ctx["guidance_text"].lower()
            scenario, partial = ("fix", partial) if helped else ("partial", partial)
        if scenario in ("fix", "partial"):
            target = solution if scenario == "fix" else partial
            return _calls(*self._writes(task, target), ("run_public_tests", {})) if turn == 2 else _calls(("submit", {}))
        if scenario == "regression_fallback":
            if turn == 2:
                return _calls(*self._writes(task, solution), ("run_public_tests", {}))
            return _calls(*[("write_file", {"path": p, "content": c})
                            for p, c in sorted(task.mock_variant("regressed").items()) if c != solution[p]],
                          ("submit", {}))
        if scenario == "fail":
            wrong = task.mock_variant("wrong")
            if turn in (2, 3):
                return _calls(*self._writes(task, wrong), ("run_public_tests", {}))
            return _calls(("submit", {}))
        return _calls(("submit", {}))

    def complete(self, messages: list[dict], context: dict | None = None) -> ModelResponse:
        ctx = context or {}
        task = self.tasks[ctx["task_id"]]
        err = task.mock_api_error
        if (err and ctx["turn"] == 1 and err.get("condition") == ctx.get("condition")
                and err.get("repetition") == ctx.get("repetition")):
            raise APIError("HTTP 503: mock overload (synthetic)", 503, None, True)
        text = self._script(task, ctx)
        inp = sum(len(m["content"]) for m in messages) // 4 + 1  # synthetic estimate, not real usage
        out = len(text) // 4 + 1
        return ModelResponse(text, inp, out, inp + out, 0.0, "stop", "mock")
