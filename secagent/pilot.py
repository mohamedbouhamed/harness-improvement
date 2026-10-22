"""One-proposal adaptation pilot (docs/adaptation-pilot-plan.md). Run: python -m secagent.pilot --live

Steps: budget check -> one proposal -> validate -> freeze (commit) -> training-set check ->
decision (commit) -> exploratory transfer -> revert if rejected. Every step is logged to the
pilot folder; nothing is retried or corrected by hand.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from harness_mvp.contracts import sha256_text
from harness_mvp.model import CallBudget, ModelSettings, load_dotenv

from .agent import HARNESS_DIR
from .llm_ext import CostLedger, MeteredModel, estimate_input_tokens, load_price
from .propose import EDITABLE, REPO_ROOT, apply_proposal, build_prompt, changed_files, mine_patterns, propose_once, validate_proposal
from .report import write_report
from .run import new_run_dir, run_matrix
from .tasks import load_sec_benchmark

TRAIN = ("cal_docshare", "cal_miniwiki", "cal_teamnotes", "cal_mediakit")
TRANSFER = ("cal_invoicer", "cal_pricer")
REFERENCE_SOLVED = {"cal_teamnotes", "cal_mediakit"}
TRAIN_SOURCES = ("runs/sec/calibration-live-20261022T203726Z-03d1e6", "runs/sec/calibration-live-20261022T204455Z-898a0a",
                 "runs/sec/calibration-live-20261022T212341Z-d31dba")
GUARD_EUR, EPISODE_WORST_EUR = 4.95, 0.103
LEDGER = Path("runs/sec/cost_ledger.jsonl")


def harness_hashes() -> dict:
    return {f: sha256_text((REPO_ROOT / f).read_text(encoding="utf-8")) for f in EDITABLE}


def git(*args) -> str:
    out = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)
    if out.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def commit(paths: list[str], message: str) -> str:
    git("add", *paths)
    git("commit", "-q", "-m", message)
    return git("rev-parse", "--short", "HEAD")


def solved_pairs(records: list[dict]) -> dict[str, dict]:
    by = {}
    for r in records:
        by.setdefault(r["task_id"], {})[r["variant"]] = r
    out = {}
    for tid, v in sorted(by.items()):
        vul, pat = v.get("vulnerable"), v.get("patched")
        det = bool(vul and vul["score"]["detected"])
        fp = None if pat is None else (pat["score"]["n_findings"] if pat["score"]["submitted"] else None)
        out[tid] = {"vulnerable_detected": det if vul else None,
                    "vulnerable_end": vul["end_reason"] if vul else "not run",
                    "twin_findings": fp, "twin_end": pat["end_reason"] if pat else "not run",
                    "solved": bool(vul and pat and det and pat["score"]["clean"]),
                    "complete": bool(vul and pat)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", required=True)
    args = ap.parse_args(argv)
    tasks = {t.task_id: t for t in load_sec_benchmark()}
    if changed_files():
        sys.exit(f"working tree not clean: {changed_files()}")
    out = new_run_dir(Path("runs/sec"), "pilot")
    log_path = out / "pilot_log.json"
    log = {"original_hashes": harness_hashes(), "original_commit": git("rev-parse", "--short", "HEAD")}

    def save():
        log_path.write_text(json.dumps(log, indent=2), encoding="utf-8")

    # ---- Conservative budget check (before any generation) ----
    train_records = [json.loads(l) for d in TRAIN_SOURCES for l in (Path(d) / "episodes.jsonl").read_text().splitlines()
                     if l.strip() and json.loads(l)["task_id"] in TRAIN]
    patterns = mine_patterns(train_records, tasks)
    prompt = build_prompt(patterns, (HARNESS_DIR / "instructions.md").read_text().strip(),
                          (HARNESS_DIR / "memory.md").read_text().strip())
    price = load_price("gemini-3.5-flash-lite")
    proposer_worst = 3 * price.usd(estimate_input_tokens({"messages": [{"role": "user", "content": prompt}]}), 4096)
    ledger = CostLedger(LEDGER, GUARD_EUR, 1.0)
    allowance = GUARD_EUR - ledger.bound_eur
    worst = proposer_worst + (2 * len(TRAIN) + 2 * len(TRANSFER)) * EPISODE_WORST_EUR
    log["budget_check"] = {"bound_so_far": round(ledger.bound_eur, 4), "allowance": round(allowance, 4),
                           "proposer_worst": round(proposer_worst, 4), "total_worst": round(worst, 4)}
    save()
    if worst > allowance:
        sys.exit(f"conservative worst case {worst:.3f} exceeds allowance {allowance:.3f}; not starting")
    print(f"budget check ok: worst {worst:.3f} <= allowance {allowance:.3f}")

    load_dotenv()
    settings = ModelSettings.from_env()
    settings.temperature = 0.0
    model = MeteredModel(settings, price, ledger, run_id=out.name)
    budget = CallBudget(200, 1.0)

    # ---- One proposal ----
    prop = propose_once(model, budget, patterns, log=print)
    log["proposer"] = {k: prop[k] for k in ("prompt", "response", "returned_model", "error", "proposal")} | {
        "train_records": len(train_records), "requests": prop["requests"]}
    problems = [prop["error"]] if prop["error"] else validate_proposal(prop["proposal"], [tasks[t] for t in TRAIN])
    log["admissible"], log["problems"] = not problems, problems
    save()
    if problems:
        print(f"proposal rejected as inadmissible: {problems}")
        return 0

    # ---- Freeze: apply, diff check, commit before running ----
    p = prop["proposal"]
    changed = apply_proposal(p)
    log["patch"] = {"target": p["target"], "changed_files": changed, "hashes": harness_hashes()}
    log["patch_commit"] = commit([p["target"]], f"harness: proposed edit to {p['target']} (frozen before evaluation)\n\n"
                                 f"Pattern: {p['pattern']}\nRationale: {p['rationale']}\n"
                                 f"Hashes: {json.dumps(log['patch']['hashes'])}\nPilot: {out.name}")
    save()
    print(f"patch frozen at {log['patch_commit']}")

    # ---- Training-set improvement check ----
    train_dir = out / "train"
    train_dir.mkdir()
    recs = run_matrix([tasks[t] for t in TRAIN], ["proposed"], 1, model, budget, train_dir, seed=11)
    write_report(train_dir, "Pilot: training-set check (proposed harness)")
    pairs = solved_pairs(recs)
    n_solved = sum(v["solved"] for v in pairs.values())
    accepted = all(v["complete"] for v in pairs.values()) and n_solved >= 3 and all(pairs[t]["solved"] for t in REFERENCE_SOLVED)
    log["training_check"] = {"pairs": pairs, "solved": n_solved, "reference_solved": sorted(REFERENCE_SOLVED),
                             "accepted": accepted}
    save()
    decision = (f"pilot decision: {'ACCEPT' if accepted else 'REJECT'} proposed harness (training-set improvement check)\n\n"
                f"Solved training pairs: {n_solved}/4 (reference 2/4: {sorted(REFERENCE_SOLVED)}).\n"
                + "\n".join(f"{t}: {json.dumps(v)}" for t, v in pairs.items())
                + "\nRecorded before any transfer episode runs. Not an independent validation.")
    dec_file = Path("docs/adaptation-pilot-decision.md")
    dec_file.write_text("# " + decision.replace("\n\n", "\n\n", 1) + "\n", encoding="utf-8")
    log["decision_commit"] = commit([str(dec_file)], decision)
    save()
    print(decision)

    # ---- Exploratory transfer (run either way; decision unchanged) ----
    transfer_dir = out / "transfer"
    transfer_dir.mkdir()
    trecs = run_matrix([tasks[t] for t in TRANSFER], ["proposed"], 1, model, budget, transfer_dir, seed=12)
    write_report(transfer_dir, "Pilot: exploratory transfer (proposed harness)")
    log["transfer"] = {"pairs": solved_pairs(trecs), "complete": len(trecs) == 2 * len(TRANSFER)}
    save()

    # ---- Restore the original harness if rejected ----
    if not accepted:
        git("revert", "--no-edit", log["patch_commit"])
        log["reverted"] = git("rev-parse", "--short", "HEAD")
        log["restored_hashes"] = harness_hashes()
        save()
    print(json.dumps({k: log[k] for k in ("training_check", "transfer")}, indent=1))
    print(f"ledger: reported {ledger.spent_eur:.4f}, upper bound {ledger.bound_eur:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
