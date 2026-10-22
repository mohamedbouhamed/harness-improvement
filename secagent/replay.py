"""Offline replay check: feed recorded model replies back through the current agent loop and verify that
every request's messages are identical to the recorded ones (no model calls)."""
from __future__ import annotations

import json
from pathlib import Path

from harness_mvp.model import CallBudget, ModelResponse

from .agent import read_instructions, read_verification, run_episode


class _Replayer:
    synthetic = True

    def __init__(self, turns: list[dict]):
        self.turns, self.mismatches, self.i = turns, [], 0

    def complete(self, messages, context=None):
        rec = self.turns[self.i]
        if messages != rec["messages"]:
            self.mismatches.append(rec["turn"])
        self.i += 1
        return ModelResponse(rec["response"] or "", 0, 0, 0, 0.0, "stop", "replay")


def _read_text(path: Path) -> str:
    import gzip
    if path.is_file():
        return path.read_text()
    return gzip.decompress(Path(str(path) + ".gz").read_bytes()).decode("utf-8")


def replay_run(run_dir: Path, tasks: dict, task_ids: set, condition: str = "none",
               instructions: str | None = None, verification: str | None = None) -> list[dict]:
    """instructions/verification default to the current harness/ files; pass a variant's texts explicitly."""
    from .run import guidance_for
    instructions = read_instructions() if instructions is None else instructions
    verification = read_verification() if verification is None else verification
    by = {}
    for line in _read_text(Path(run_dir) / "transcripts.jsonl").splitlines():
        t = json.loads(line)
        by.setdefault(t["episode_id"], []).append(t)
    out = []
    for line in (Path(run_dir) / "episodes.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["task_id"] not in task_ids or r["condition"] != condition or r["end_reason"] == "budget_exhausted":
            continue
        turns = sorted(by[r["episode_id"]], key=lambda t: t["turn"])
        m = _Replayer(turns)
        rec, _ = run_episode(tasks[r["task_id"]], r["variant"], m, CallBudget(100), episode_id=r["episode_id"],
                             condition=condition, repetition=r["repetition"], instructions=instructions,
                             guidance=guidance_for(condition), verification=verification)
        out.append({"episode": r["episode_id"], "turns": len(turns), "mismatched_turns": m.mismatches,
                    "same_findings": rec["findings"] == r["findings"], "same_end": rec["end_reason"] == r["end_reason"]})
    return out
