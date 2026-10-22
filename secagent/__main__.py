"""CLI: validate | estimate | run | report. Paid calls need --live, confirmed prices and a euro cap."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

from harness_mvp.model import CallBudget, ModelSettings, load_dotenv
from harness_mvp.runner import DockerRunner

from .agent import DetectLimits, read_instructions
from .llm_ext import CHARS_PER_TOKEN_LOWER_BOUND, CostLedger, MeteredModel, load_price
from .report import write_report
from .run import CONDITIONS, MockDetector, estimate_episode_chars, guidance_for, new_run_dir, run_matrix
from .tasks import VARIANTS, load_sec_benchmark
from .validate import validate_labels

RUNS = Path("runs/sec")
LEDGER = RUNS / "cost_ledger.jsonl"


def _tasks(split):
    tasks = load_sec_benchmark()
    return [t for t in tasks if split in (None, "all") or t.split == split]


def cmd_validate(args) -> int:
    runner = DockerRunner(Path(tempfile.mkdtemp(prefix="sec-validate-")))
    runner.ensure_ready(build=False)
    bad = 0
    for t in _tasks(args.split):
        res = validate_labels(t, runner)
        bad += not res["ok"]
        print(f"[{'PASS' if res['ok'] else 'FAIL'}] {t.task_id} ({t.split}, {t.weakness_class}) {res['outcomes']}")
        for k, v in res["details"].items():
            print(f"    {k}: {v[-600:]}")
    return 1 if bad else 0


def cmd_estimate(args) -> int:
    """Cost estimate from measured prompt sizes and stated assumptions about turns and output tokens."""
    price = load_price(args.model)
    instructions = read_instructions()
    tasks = _tasks(args.split)
    conds = args.conditions.split(",")
    rows = []
    for t in tasks:
        for v in VARIANTS:
            for c in conds:
                rows.append(estimate_episode_chars(t, v, instructions, guidance_for(c)))
    base = sum(r["base_chars"] for r in rows) / len(rows)
    files = sum(r["file_chars"] for r in rows) / len(rows)
    episodes = len(rows) * args.reps
    print(f"{len(tasks)} pairs x {len(VARIANTS)} variants x {len(conds)} conditions x {args.reps} reps = {episodes} episodes")
    print(f"measured: system+first message+tool schema ≈ {base:.0f} chars; program files ≈ {files:.0f} chars")
    print(f"price {args.model}: ${price.input_usd_per_m}/M in, ${price.output_usd_per_m}/M out (confirmed={price.confirmed_by_user})")
    for name, turns, out_per_turn, cpt in (("typical", 3, 600, 3.5), ("high", 5, 2000, 3.0),
                                           ("worst (every limit hit)", DetectLimits().max_model_calls, args.max_tokens,
                                            CHARS_PER_TOKEN_LOWER_BOUND)):
        # Conversation grows: turn 1 = base; later turns carry all files plus earlier replies.
        in_tok = sum(base + (files if k > 1 else 0) + 1500 * (k - 1) for k in range(1, turns + 1)) / cpt
        out_tok = turns * out_per_turn
        usd = price.usd(in_tok, out_tok) * episodes
        print(f"  {name:>24}: {turns} turns, {in_tok:,.0f} in + {out_tok:,} out tokens/episode -> "
              f"${usd:.2f} (≈ {usd * args.usd_to_eur:.2f} EUR at {args.usd_to_eur} EUR/USD)")
    return 0


def _model(args, run_id):
    if args.mock:
        return MockDetector(load_sec_benchmark())
    load_dotenv()
    settings = ModelSettings.from_env()
    if settings.problems():
        sys.exit("; ".join(settings.problems()))
    if args.temperature is not None:
        settings.temperature = args.temperature
    if args.max_tokens:
        settings.max_tokens = args.max_tokens
    ledger = CostLedger(LEDGER, args.cap_eur, args.usd_to_eur)
    return MeteredModel(settings, load_price(settings.model), ledger, run_id=run_id, reasoning_effort=args.reasoning_effort)


def cmd_run(args) -> int:
    if not (args.mock or args.live):
        sys.exit("choose --mock or --live")
    if args.live and args.cap_eur is None:
        sys.exit("--live needs --cap-eur")
    out = new_run_dir(RUNS, f"{args.split}-{'live' if args.live else 'mock'}")
    model = _model(args, out.name)
    conds = args.conditions.split(",")
    unknown = set(conds) - set(CONDITIONS)
    if unknown:
        sys.exit(f"unknown conditions {sorted(unknown)}")
    budget = CallBudget(args.max_model_calls, args.min_interval)
    tasks = [t for t in _tasks(args.split) if not args.tasks or t.task_id in args.tasks.split(",")]
    run_matrix(tasks, conds, args.reps, model, budget, out, seed=args.seed, variants=tuple(args.variants.split(",")),
               api_retries=args.api_retries, manifest_extra={"cli": vars(args) | {"func": None}})
    print(f"report: {write_report(out, f'{args.split} run ({out.name})')}")
    if args.live:
        print(f"ledger total: {CostLedger(LEDGER, args.cap_eur, args.usd_to_eur).spent_eur:.4f} EUR")
    return 0


def cmd_report(args) -> int:
    print(write_report(Path(args.run_dir)).read_text())
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m secagent")
    sub = p.add_subparsers(required=True)
    v = sub.add_parser("validate", help="check every label in Docker (no model calls)")
    v.add_argument("--split", default="all")
    v.set_defaults(func=cmd_validate)
    e = sub.add_parser("estimate", help="cost estimate (no model calls)")
    e.add_argument("--split", default="calibration")
    e.add_argument("--conditions", default="none")
    e.add_argument("--reps", type=int, default=2)
    e.add_argument("--model", default="gemini-3.5-flash-lite")
    e.add_argument("--max-tokens", type=int, default=4096)
    e.add_argument("--usd-to-eur", type=float, default=1.0)
    e.set_defaults(func=cmd_estimate)
    r = sub.add_parser("run", help="run episodes")
    r.add_argument("--mock", action="store_true")
    r.add_argument("--live", action="store_true")
    r.add_argument("--split", default="calibration")
    r.add_argument("--conditions", default="none")
    r.add_argument("--reps", type=int, default=2)
    r.add_argument("--tasks", default="", help="comma-separated task ids (default: all in the split)")
    r.add_argument("--variants", default="vulnerable,patched")
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--temperature", type=float, default=None)
    r.add_argument("--max-tokens", type=int, default=None)
    r.add_argument("--reasoning-effort", default=None, choices=[None, "low", "medium", "high"])
    r.add_argument("--cap-eur", type=float, default=None, help="total cap across all runs (shared ledger)")
    r.add_argument("--usd-to-eur", type=float, default=1.0, help="1.0 over-states EUR cost, which is conservative")
    r.add_argument("--max-model-calls", type=int, default=400)
    r.add_argument("--min-interval", type=float, default=0.0)
    r.add_argument("--api-retries", type=int, default=2)
    r.set_defaults(func=cmd_run)
    rep = sub.add_parser("report")
    rep.add_argument("run_dir")
    rep.set_defaults(func=cmd_report)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
