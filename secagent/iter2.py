"""Iteration 2: one proposal with feedback from the rejected edit (docs/iteration2-plan.md).

python -m secagent.iter2 --mock      offline dry run: mock model, no commits, harness restored at the end
python -m secagent.iter2 --live      paid run; refuses to start unless the predeclared tier fits the allowance

Steps: budget tier -> one proposal -> validate -> freeze (commit) -> training check (fresh or historical
baseline, per tier) -> decision (commit) -> transfer if accepted (per tier) -> revert if rejected.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from harness_mvp.contracts import sha256_text
from harness_mvp.model import CallBudget, ModelResponse, ModelSettings, load_dotenv

from .agent import DetectLimits, read_instructions
from .llm_ext import CostLedger, MeteredModel, estimate_input_tokens, load_price
from .pilot import commit, git, solved_pairs
from .propose import (ITER2_EDITABLE, REPO_ROOT, apply_files, build_feedback_prompt, changed_files,
                      parse_feedback_proposal, validate_feedback_proposal)
from .report import write_report
from .run import MockDetector, new_run_dir, run_matrix
from .tasks import load_sec_benchmark

TRAIN = ("cal_docshare", "cal_miniwiki", "cal_teamnotes", "cal_mediakit")
TRANSFER_ALL = ("cal_invoicer", "cal_pricer", "cal_prefsync_v2", "cal_ssohub")  # every pair excluded from proposer inputs
TRANSFER_HISTORICAL = ("cal_invoicer", "cal_pricer", "cal_ssohub")  # those with a historical T=0 baseline for both variants
BASELINE_RUN = "runs/sec/calibration-live-20261022T204455Z-898a0a"  # none, T=0; repetition 1 is the historical baseline
PREVIOUS = {"line": "- Carefully verify all security-critical assumptions and sanitization routines: check whether prefix "
                    "checks, string containment checks, escaping functions (e.g., handling of quotes or special characters), "
                    "or character blocklists can be bypassed or fail to fully neutralize malicious input.",
            "commit": "0d31a7c"}
GUARD_EUR, EPISODE_WORST_EUR, PROPOSER_ATTEMPTS = 5.00, 0.104, 3
LEDGER = Path("runs/sec/cost_ledger.jsonl")

# (name, fresh baseline on training?, transfer pairs if accepted, fresh baseline on transfer?) in order of preference
TIERS = (("A", True, TRANSFER_ALL, True), ("B", True, TRANSFER_HISTORICAL, False),
         ("C", False, TRANSFER_HISTORICAL, False), ("D", False, (), False))


def tier_episodes(tier) -> int:
    _, fresh_train, transfer, fresh_transfer = tier
    return 2 * len(TRAIN) * (2 if fresh_train else 1) + 2 * len(transfer) * (2 if fresh_transfer else 1)


def accept(base: dict, cand: dict) -> tuple[bool, dict]:
    """Acceptance rule fixed in docs/iteration2-plan.md (training-set improvement, not validation)."""
    def totals(pairs):
        return {"solved": sum(v["solved"] for v in pairs.values()),
                "twin_findings": sum(v["twin_findings"] or 0 for v in pairs.values()),
                "non_submissions": sum(v["vulnerable_end"] not in ("submitted", "submitted_draft") for v in pairs.values())
                                   + sum(v["twin_end"] not in ("submitted", "submitted_draft") for v in pairs.values())}
    b, c = totals(base), totals(cand)
    checks = {"one_more_pair_solved": c["solved"] >= b["solved"] + 1,
              "no_solved_pair_lost": all(cand[t]["solved"] for t, v in base.items() if v["solved"]),
              "twin_findings_not_increased": c["twin_findings"] <= b["twin_findings"],
              "non_submissions_not_increased": c["non_submissions"] <= b["non_submissions"],
              "complete": all(v["complete"] for v in cand.values())}
    return all(checks.values()), {"baseline": b, "candidate": c, "checks": checks}


class _ScriptedProposer:
    synthetic = True

    def complete(self, messages, context=None):
        body = {"cause": "mock", "change": {"harness/instructions.md": None,
                                            "harness/verification.md": "Re-check each draft finding against the code you read."},
                "predicted_improvement": "mock", "predicted_risks": "mock"}
        return ModelResponse(json.dumps(body), 1, 1, 2, 0.0, "stop", "mock")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--live", action="store_true")
    ap.add_argument("--guard-eur", type=float, default=GUARD_EUR, help="cumulative upper-bound guard (user-authorized)")
    ap.add_argument("--tier", choices=[t[0] for t in TIERS], default=None, help="run exactly this tier (must fit)")
    args = ap.parse_args(argv)
    if changed_files():
        sys.exit(f"working tree not clean: {changed_files()}")
    tasks = {t.task_id: t for t in load_sec_benchmark()}
    out = new_run_dir(Path("runs/sec"), f"iter2-{'live' if args.live else 'mock'}")
    log = {"mode": "live" if args.live else "mock", "start_commit": git("rev-parse", "--short", "HEAD"),
           "original_hashes": {f: sha256_text((REPO_ROOT / f).read_text()) for f in ITER2_EDITABLE}}
    original = {f: (REPO_ROOT / f).read_text() for f in ITER2_EDITABLE}

    def save():
        (out / "iter2_log.json").write_text(json.dumps(log, indent=2), encoding="utf-8")

    # ---- Inputs (training only) and conservative budget tier ----
    base_hist = [json.loads(l) for l in Path(BASELINE_RUN, "episodes.jsonl").read_text().splitlines()
                 if json.loads(l)["repetition"] == 1]
    pilot = sorted(Path("runs/sec").glob("pilot-2*"))[-1]
    cand_prev = [json.loads(l) for l in (pilot / "train" / "episodes.jsonl").read_text().splitlines()]
    previous = PREVIOUS | {"pattern": json.loads((pilot / "pilot_log.json").read_text())["proposer"]["proposal"]["pattern"]}
    prompt = build_feedback_prompt([r for r in base_hist if r["task_id"] in TRAIN], cand_prev, tasks, previous,
                                   read_instructions(), DetectLimits())
    for tid in TRANSFER_ALL:
        assert tasks[tid].family not in prompt, f"transfer program {tid} leaked into the proposer prompt"
    price = load_price("gemini-3.5-flash-lite")
    proposer_worst = PROPOSER_ATTEMPTS * price.usd(estimate_input_tokens({"messages": [{"role": "user", "content": prompt}]}), 4096)
    ledger = CostLedger(LEDGER, args.guard_eur, 1.0)
    allowance = args.guard_eur - ledger.bound_eur
    costs = {t[0]: round(proposer_worst + tier_episodes(t) * EPISODE_WORST_EUR, 3) for t in TIERS}
    candidates = [t for t in TIERS if args.tier in (None, t[0])]
    tier = next((t for t in candidates if costs[t[0]] <= allowance), None)
    log["budget"] = {"guard_eur": args.guard_eur, "requested_tier": args.tier, "bound_so_far": round(ledger.bound_eur, 4), "allowance": round(allowance, 4),
                     "proposer_worst": round(proposer_worst, 4), "tier_worst": costs, "tier": tier[0] if tier else None}
    save()
    print(json.dumps(log["budget"]))
    if args.live and tier is None:
        print("BLOCKED: no predeclared tier fits the remaining allowance; nothing was sent.")
        return 2
    tier = tier or TIERS[0]  # the mock dry run exercises the most complete tier

    # ---- Model ----
    if args.live:
        load_dotenv()
        settings = ModelSettings.from_env()
        settings.temperature = 0.0
        model = MeteredModel(settings, price, ledger, run_id=out.name)
        proposer = model
    else:
        model, proposer = MockDetector(list(tasks.values()), detect=True), _ScriptedProposer()
    budget = CallBudget(400, 1.0 if args.live else 0.0)

    def run_set(task_ids, label, condition, seed):
        d = out / label
        d.mkdir()
        recs = run_matrix([tasks[t] for t in task_ids], [condition], 1, model, budget, d, seed=seed)
        write_report(d, f"Iteration 2: {label}")
        return recs

    try:
        # ---- Fresh baseline with the original harness (before any change), if the tier includes it ----
        _, fresh_train, transfer, fresh_transfer = tier
        base_train = (run_set(TRAIN, "train-baseline", "none", 21) if fresh_train
                      else [r for r in base_hist if r["task_id"] in TRAIN])
        log["baseline_kind"] = "fresh" if fresh_train else f"historical ({BASELINE_RUN}, repetition 1)"

        # ---- One proposal ----
        from harness_mvp import llm
        resp, requests, fatal, _ = llm.request(proposer, [{"role": "user", "content": prompt}], budget, {"label": "iter2-proposer"},
                                               api_retries=PROPOSER_ATTEMPTS - 1, retry_wait_s=10.0, log=print, label="iter2-proposer")
        log["proposer"] = {"prompt": prompt, "response": resp.text if resp else None, "requests": requests,
                           "returned_model": getattr(resp, "model", None) if resp else None}
        p, err = parse_feedback_proposal(resp.text) if resp else (None, fatal or "api_error")
        problems = [err] if err else validate_feedback_proposal(p, [tasks[t] for t in TRAIN], original)
        log.update(proposal=p, admissible=not problems, problems=problems)
        save()
        if problems:
            print(f"proposal inadmissible: {problems}")
            return 0

        # ---- Freeze ----
        changed = apply_files(p["change"])
        log["patch"] = {"changed_files": changed, "hashes": {f: sha256_text((REPO_ROOT / f).read_text()) for f in ITER2_EDITABLE},
                        "diff": git("diff")}
        msg = (f"harness: iteration-2 proposal (frozen before evaluation)\n\nCause: {p['cause']}\n"
               f"Predicted improvement: {p['predicted_improvement']}\nPredicted risks: {p['predicted_risks']}\n"
               f"Hashes: {json.dumps(log['patch']['hashes'])}\nRun: {out.name}")
        log["patch_commit"] = commit(changed, msg) if args.live else "(mock: not committed)"
        save()

        # ---- Training-set check ----
        cand_train = run_set(TRAIN, "train-candidate", "candidate", 22)
        ok, detail = accept(solved_pairs(base_train), solved_pairs(cand_train))
        log["training_check"] = {"baseline_pairs": solved_pairs(base_train), "candidate_pairs": solved_pairs(cand_train),
                                 "accepted": ok} | detail
        save()
        decision = (f"iteration 2 decision: {'ACCEPT' if ok else 'REJECT'} (training-set improvement check)\n\n"
                    f"{json.dumps(detail, indent=1)}\nBaseline: {log['baseline_kind']}. Recorded before any transfer episode.")
        if args.live:
            Path("docs/iteration2-decision.md").write_text("# " + decision + "\n", encoding="utf-8")
            log["decision_commit"] = commit(["docs/iteration2-decision.md"], decision)
        save()
        print(decision)

        # ---- Transfer (only if accepted) ----
        if ok and transfer:
            log["transfer_candidate"] = solved_pairs(run_set(transfer, "transfer-candidate", "candidate", 24))
            if fresh_transfer:
                # The original harness is needed for a fresh transfer baseline: restore it temporarily.
                for f in ITER2_EDITABLE:
                    (REPO_ROOT / f).write_text(original[f])
                log["transfer_baseline"] = solved_pairs(run_set(transfer, "transfer-baseline", "none", 23))
                for f, text in p["change"].items():
                    if text is not None:
                        (REPO_ROOT / f).write_text(text.strip() + "\n")
            save()
        if not ok and args.live:
            git("revert", "--no-edit", log["patch_commit"])
            log["reverted"] = git("rev-parse", "--short", "HEAD")
    finally:
        if not args.live:  # dry run: leave no trace in the repository
            for f in ITER2_EDITABLE:
                (REPO_ROOT / f).write_text(original[f])
        log["final_hashes"] = {f: sha256_text((REPO_ROOT / f).read_text()) for f in ITER2_EDITABLE}
        save()
    print(f"ledger: reported {ledger.spent_eur:.4f}, upper bound {ledger.bound_eur:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
