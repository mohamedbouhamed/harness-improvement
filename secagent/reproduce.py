"""Reproduce the historical decisions from committed artifacts (results/live), with no model calls.

python -m secagent.reproduce

Checks: harness tags; scorer-v1 scores recomputed from stored findings; the baseline loop replayed
from recorded replies with variant A; both proposer prompts rebuilt byte-for-byte from the training
episodes; both proposals re-validated; both acceptance decisions recomputed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from harness_mvp.contracts import sha256_text

from .agent import DetectLimits
from .iter2 import PREVIOUS, TRAIN, accept
from .pilot import REFERENCE_SOLVED, solved_pairs
from .propose import build_feedback_prompt, build_prompt, mine_patterns, validate_feedback_proposal, validate_proposal
from .replay import replay_run
from .scoring import score_episode
from .tasks import load_sec_benchmark
from .variants import load_variant

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "results" / "live"
RUNS = {"cal0": "calibration-live-20261022T203726Z-03d1e6", "cal": "calibration-live-20261022T204455Z-898a0a",
        "guid": "calibration-live-20261022T212341Z-d31dba", "pilot": "pilot-20261022T220809Z-93365a",
        "iter2": "iter2-live-20261022T222908Z-f01c21"}


def episodes(rel: str) -> list[dict]:
    return [json.loads(l) for l in (LIVE / rel / "episodes.jsonl").read_text().splitlines() if l.strip()]


def main() -> int:
    tasks = {t.task_id: t for t in load_sec_benchmark()}
    a = load_variant("A")
    results = {}

    def check(name, ok, detail=""):
        results[name] = bool(ok)
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))

    # 1. Tags
    tag = lambda t, f: subprocess.run(["git", "show", f"{t}:{f}"], cwd=ROOT, capture_output=True, text=True).stdout
    check("harness-v0 instructions hash", sha256_text(tag("harness-v0", "harness/instructions.md")).startswith("63aae5104e83"))
    check("harness-v1 instructions/verification hashes",
          sha256_text(tag("harness-v1", "harness/instructions.md")).startswith("3685c9715d9f")
          and sha256_text(tag("harness-v1", "harness/verification.md")).startswith("ebc388e2b200"))

    # 2. Scorer v1 determinism on every stored live episode
    all_eps = [r for p in LIVE.glob("**/episodes.jsonl") for r in episodes(str(p.parent.relative_to(LIVE)))]
    same = sum(score_episode(tasks[r["task_id"]], r["variant"], r["findings"]) == r["score"] for r in all_eps)
    check("v1 scores recomputed from stored findings", same == len(all_eps), f"{same}/{len(all_eps)}")

    # 3. Baseline loop replay with variant A (original harness)
    ids = set(tasks) - {"cal_prefsync"}
    reps = [x for k in ("cal0", "cal") for x in replay_run(LIVE / RUNS[k], tasks, ids, "none", a["instructions"], a["verification"])]
    bad = [r for r in reps if r["mismatched_turns"] or not r["same_findings"] or not r["same_end"]]
    check("baseline replay identical (variant A)", reps and not bad, f"{len(reps)} episodes, {len(bad)} mismatches")

    # 4. Pilot 1: prompt, admissibility, decision
    plog = json.loads((LIVE / RUNS["pilot"] / "pilot_log.json").read_text())
    train_recs = [r for k in ("cal0", "cal", "guid") for r in episodes(RUNS[k]) if r["task_id"] in TRAIN]
    prompt1 = build_prompt(mine_patterns(train_recs, tasks), a["instructions"], "")
    check("pilot-1 proposer prompt rebuilt byte-for-byte", prompt1 == plog["proposer"]["prompt"])
    check("pilot-1 proposal admissible", validate_proposal(plog["proposer"]["proposal"], [tasks[t] for t in TRAIN]) == [])
    pairs = solved_pairs(episodes(RUNS["pilot"] + "/train"))
    n = sum(v["solved"] for v in pairs.values())
    rejected = not (n >= 3 and all(pairs[t]["solved"] for t in REFERENCE_SOLVED))
    check("pilot-1 decision recomputed: REJECT", rejected and plog["training_check"]["accepted"] is False, f"solved {n}/4")

    # 5. Iteration 2: prompt, admissibility, decision
    ilog = json.loads((LIVE / RUNS["iter2"] / "iter2_log.json").read_text())
    base_hist = [r for r in episodes(RUNS["cal"]) if r["repetition"] == 1 and r["task_id"] in TRAIN]
    previous = PREVIOUS | {"pattern": plog["proposer"]["proposal"]["pattern"]}
    prompt2 = build_feedback_prompt(base_hist, episodes(RUNS["pilot"] + "/train"), tasks, previous, a["instructions"], DetectLimits())
    check("iteration-2 proposer prompt rebuilt byte-for-byte", prompt2 == ilog["proposer"]["prompt"])
    original = {"harness/instructions.md": a["instructions"] + "\n", "harness/verification.md": ""}
    check("iteration-2 proposal admissible", validate_feedback_proposal(ilog["proposal"], [tasks[t] for t in TRAIN], original) == [])
    ok, detail = accept(solved_pairs(episodes(RUNS["iter2"] + "/train-baseline")), solved_pairs(episodes(RUNS["iter2"] + "/train-candidate")))
    check("iteration-2 decision recomputed: ACCEPT", ok and ilog["training_check"]["accepted"] is True,
          f"solved {detail['baseline']['solved']} -> {detail['candidate']['solved']}")

    print(f"{sum(results.values())}/{len(results)} checks passed")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
