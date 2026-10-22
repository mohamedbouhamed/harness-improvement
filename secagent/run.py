"""Run directories, the mock backend, cost estimation and the calibration/Phase 1 runner."""
from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timezone
from pathlib import Path

from harness_mvp.contracts import canonical_hash, sha256_text
from harness_mvp.model import CallBudget, ModelResponse

from .agent import DetectLimits, read_instructions, run_episode, system_prompt, first_message, function_declarations
from .tasks import VARIANTS, SecTask

CONTROLS = Path(__file__).resolve().parent / "controls"
CONDITIONS = ("none", "checklist", "lessons")


def guidance_for(condition: str) -> str:
    if condition == "none":
        return ""
    if condition in ("checklist", "lessons"):
        return (CONTROLS / f"{condition}.md").read_text(encoding="utf-8").strip()
    raise ValueError(f"unknown condition {condition!r}")


class MockDetector:
    """Deterministic SYNTHETIC backend for offline pipeline tests: reads every file, then submits.

    It peeks at the answer key through the episode context, so its scores mean nothing; it exists
    to exercise orchestration, scoring and reporting without paid calls.
    """

    synthetic = True

    def __init__(self, tasks: list[SecTask], detect: bool = True, false_alarm: bool = False):
        self.tasks = {t.task_id: t for t in tasks}
        self.detect, self.false_alarm = detect, false_alarm

    def complete(self, messages, context=None):
        task = self.tasks[context["task_id"]]
        files = sorted(task.variant_files(context["variant"]))
        if context["turn"] == 1:
            text = json.dumps({"actions": [{"action": "read_file", "path": f} for f in files[:6]]})
        else:
            ans = task.answer
            hit = {"file": ans["file"], "function": ans["functions"][0], "lines": [1, 2], "cwe": ans["cwe"],
                   "source": "mock", "path": ans["path"], "reason": "mock"}
            findings = []
            if context["variant"] == "vulnerable" and self.detect:
                findings.append(hit)
            if context["variant"] == "patched" and self.false_alarm:
                findings.append(hit)
            text = json.dumps({"actions": [{"action": "submit_findings", "findings": findings}]})
        chars = sum(len(m["content"]) for m in messages)
        return ModelResponse(text, chars // 4, len(text) // 4, chars // 4 + len(text) // 4, 0.0, "stop", "mock")


def estimate_episode_chars(task: SecTask, variant: str, instructions: str, guidance: str,
                           limits: DetectLimits = DetectLimits()) -> dict:
    """Prompt sizes for an agent that reads every file in turn 1 and submits in turn 2."""
    files = task.variant_files(variant)
    base = (len(system_prompt(instructions, limits)) + len(first_message(files, guidance))
            + len(json.dumps(function_declarations())))
    all_files = sum(len(v) + 6 * (v.count("\n") + 1) for v in files.values())  # + line numbers
    return {"base_chars": base, "file_chars": all_files}


def new_run_dir(root: Path, kind: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = Path(root) / f"{kind}-{stamp}-{uuid.uuid4().hex[:6]}"
    path.mkdir(parents=True, exist_ok=False)
    return path


def run_matrix(tasks: list[SecTask], conditions: list[str], reps: int, model, budget: CallBudget, out_dir: Path, *,
               seed: int = 0, limits: DetectLimits = DetectLimits(), instructions: str | None = None,
               api_retries: int = 2, retry_wait_s: float = 10.0, log=print, manifest_extra: dict | None = None) -> list[dict]:
    """Every task x variant x condition x repetition, in a seeded shuffled order. Writes JSONL as it goes."""
    instructions = read_instructions() if instructions is None else instructions
    plan = [(t, v, c, r) for t in tasks for v in VARIANTS for c in conditions for r in range(1, reps + 1)]
    random.Random(seed).shuffle(plan)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "seed": seed,
                "synthetic": bool(getattr(model, "synthetic", False)), "tasks": [t.task_id for t in tasks],
                "conditions": conditions, "reps": reps, "limits": limits.to_dict(),
                "instructions_sha256": sha256_text(instructions),
                "guidance_sha256": {c: sha256_text(guidance_for(c)) for c in conditions},
                "task_hashes": {t.task_id: t.hashes() for t in tasks}, "episodes_planned": len(plan),
                "model": model.describe() if hasattr(model, "describe") else {"backend": "mock"},
                "max_model_calls": budget.max_calls} | (manifest_extra or {})
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    records = []
    with (out_dir / "episodes.jsonl").open("a", encoding="utf-8") as ep, \
            (out_dir / "transcripts.jsonl").open("a", encoding="utf-8") as tr:
        for i, (task, variant, cond, rep) in enumerate(plan, 1):
            eid = f"{task.task_id}.{variant}.{cond}.r{rep}"
            log(f"[{i}/{len(plan)}] {eid}")
            rec, transcript = run_episode(task, variant, model, budget, episode_id=eid, condition=cond, repetition=rep,
                                          instructions=instructions, guidance=guidance_for(cond), limits=limits,
                                          api_retries=api_retries, retry_wait_s=retry_wait_s, log=log)
            rec["order_position"] = i
            ep.write(json.dumps(rec) + "\n")
            ep.flush()
            for entry in transcript:
                tr.write(json.dumps(entry) + "\n")
            records.append(rec)
            if rec["fatal"] in ("auth_error", "budget_exhausted"):
                log(f"stopping: {rec['fatal']}")
                break
    manifest["episodes_completed"] = len(records)
    manifest["records_sha256"] = canonical_hash([r["episode_id"] for r in records])
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return records
