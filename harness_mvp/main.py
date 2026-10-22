"""CLI. Workflow: build-memory -> [select] -> freeze -> evaluate (or `pipeline` for all of it).

Other commands: smoke (offline checks or live preflight, no model calls), plan (request budgets),
report (regenerate a study report), build-image (Docker runner image).
"""
from __future__ import annotations

import argparse
import shutil
import tempfile
from pathlib import Path

from .benchmark import BENCHMARK_DIR, load_benchmark
from .experiment import StudyError, make_runner, register
from .mock import MockRepoModel
from .model import CallBudget, ModelSettings, load_dotenv
from .repo_agent import run_episode
from .runner import DEFAULT_IMAGE, build_image, docker_status
from .study_report import write_study_report
from .workspace import execution_files


def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def cmd_smoke(args) -> int:
    tasks = load_benchmark(Path(args.benchmark) if args.benchmark else BENCHMARK_DIR)
    results = []
    work = Path(tempfile.mkdtemp(prefix="harness-smoke-"))
    mock = MockRepoModel(tasks)
    if args.mock:
        print("[smoke] offline checks (no model calls)")
        kind = args.runner
    else:
        print("[smoke] live preflight (no model calls are made)")
        load_dotenv()
        settings = ModelSettings.from_env()
        results.append(_check("credentials configured", not settings.problems(),
                              "; ".join(settings.problems()) or f"{settings.model} @ {settings.public_dict()['base_url']}"))
        kind = "docker"
    try:
        runner = make_runner(kind, args.mock, work / "exec", 5, mock.trusted_sources())
    except StudyError as exc:
        _check("runner available", False, str(exc))
        return 1
    print(f"[smoke] runner: {runner.name}")

    print("[smoke] benchmark tasks: buggy fails public tests, reference passes public + hidden")
    for t in tasks:
        out = {}
        for name, cand in (("buggy", t.original_candidate()), ("reference", t.solution_files())):
            for which in ("public", "hidden"):
                files, rel = execution_files(t, cand, which)
                out[f"{name}/{which}"] = runner.run_files(files, rel).outcome
        results.append(_check(f"{t.task_id} ({t.split})", out["buggy/public"] == "failed"
                              and out["reference/public"] == out["reference/hidden"] == "passed",
                              " ".join(f"{k}={v}" for k, v in out.items())))

    print("[smoke] runner outcomes")
    cal = next(t for t in tasks if t.split == "calibration")
    for label, cand, want in (("known-correct candidate", cal.solution_files(), "passed"),
                              ("known-broken candidate", cal.original_candidate(), "failed"),
                              ("infinite-loop candidate", cal.mock_variant("timeout"), "timeout")):
        files, rel = execution_files(cal, cand, "public")
        r = runner.run_files(files, rel)
        results.append(_check(f"{label} -> {want}", r.outcome == want, f"{r.outcome} in {r.duration_s:.2f}s"))

    if args.mock:
        print("[smoke] mock episode (SYNTHETIC): regression -> public-test fallback")
        task = next(t for t in tasks if t.mock_scenario == "regression_fallback")
        rec, _, _ = run_episode(task, mock, runner, CallBudget(10), work / "ws", episode_id="smoke",
                                phase="smoke", condition="baseline", repetition=0, guidance={})
        results.append(_check("fallback submits the latest public-passing candidate",
                              rec["fallback_activated"] and rec["submitted_public_pass"] and not rec["final_public_pass"],
                              f"end={rec['end_reason']} hidden_pass={rec['hidden_success']}"))
    shutil.rmtree(work, ignore_errors=True)
    ok = all(results)
    print(f"[smoke] {'ALL PASSED' if ok else 'FAILURES'} ({sum(results)}/{len(results)})")
    return 0 if ok else 1


def cmd_report(args) -> int:
    path = write_study_report(Path(args.study))
    print(path.read_text(encoding="utf-8"))
    print(f"\n[report] written to {path}")
    return 0


def cmd_build_image(args) -> int:
    ok, detail = docker_status()
    if not ok:
        raise SystemExit(f"error: Docker is unavailable: {detail}")
    build_image(args.image)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m harness_mvp",
                                description="Memory-guided repository repair: baseline vs static guidance vs learned memory.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("smoke", help="offline checks (--mock) or live preflight; never calls the model")
    s.add_argument("--mock", action="store_true", help="offline checks with the mock agent")
    s.add_argument("--runner", choices=["auto", "docker", "local-trusted"], default="auto")
    s.add_argument("--benchmark", default=None)
    s.set_defaults(func=cmd_smoke)

    register(sub)

    r = sub.add_parser("report", help="regenerate report.md for a study directory")
    r.add_argument("study")
    r.set_defaults(func=cmd_report)

    b = sub.add_parser("build-image", help="build the Docker runner image")
    b.add_argument("--image", default=DEFAULT_IMAGE)
    b.set_defaults(func=cmd_build_image)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
