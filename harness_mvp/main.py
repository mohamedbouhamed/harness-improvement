"""CLI: smoke | run | calibrate | freeze-benchmark | report | build-image."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import agent, revision
from .metrics import revision_metrics
from .model import (TIMEOUT_CANDIDATE, CallBudget, MockModel, ModelSettings, OpenAICompatibleModel,
                    load_dotenv)
from .report import write_report
from .revision import BASELINE_V2, RevisionConfig, run_revision_episode, space_v2
from .runner import (DEFAULT_IMAGE, DockerRunner, RunnerUnavailable, TrustedLocalRunner, build_image,
                     docker_status)
from .search import BASELINE, HarnessConfig, sample_alternatives, search_space, select_winner
from .tasks import (BENCHMARK_V2_DIR, CALIBRATION_DIR, REPO_ROOT, BenchmarkError, freeze_benchmark,
                    load_tasks, split_tasks, verify_frozen)

# v2 presets: (optimization repetitions, held-out repetitions). 6+6 tasks -> 168 / 72 max calls.
V2_PRESETS = {"full": (2, 3), "pilot": (1, 1)}


class RunAborted(Exception):
    pass


def code_version() -> str:
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT,
                             capture_output=True, text=True, timeout=5)
        if rev.returncode == 0:
            dirty = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_ROOT,
                                   capture_output=True, text=True, timeout=5).stdout.strip()
            return "git:" + rev.stdout.strip() + ("-dirty" if dirty else "")
    except (OSError, subprocess.TimeoutExpired):
        pass
    h = hashlib.sha256()
    for p in sorted((REPO_ROOT / "harness_mvp").glob("*.py")):
        h.update(p.name.encode() + p.read_bytes())
    return "sources-sha256:" + h.hexdigest()[:16]


def make_runner(kind: str, mock: bool, work_root: Path, timeout_s: int, trusted: list[str], build: bool = True):
    if kind == "local-trusted" and not mock:
        raise SystemExit("error: --runner local-trusted is only allowed with --mock")
    if kind in ("docker", "auto"):
        ok, detail = docker_status()
        if ok:
            runner = DockerRunner(work_root, timeout_s=timeout_s)
            runner.ensure_ready(build=build)
            return runner
        if kind == "docker" or not mock:
            raise SystemExit(f"error: Docker is required to execute model-generated code ({detail}).\n"
                             "Start Docker and retry, or use --mock for an offline synthetic run.")
        print(f"[runner] Docker unavailable ({detail.splitlines()[0]}); mock mode falls back to the "
              "trusted local runner (allowlisted repo-authored code only).", flush=True)
    return TrustedLocalRunner(work_root, trusted, timeout_s=timeout_s)


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def unique_run_dir(requested: str | None, default_name: str) -> Path:
    """Never reuse a non-empty run directory: append -2, -3, ... instead."""
    base = Path(requested) if requested else Path("runs") / default_name
    if not base.exists() or not any(base.iterdir()):
        return base
    for i in range(2, 10_000):
        candidate = base.with_name(f"{base.name}-{i}")
        if not candidate.exists():
            print(f"[run] {base} already contains a run; writing to {candidate} instead "
                  "(run directories are never overwritten)", flush=True)
            return candidate
    raise SystemExit(f"error: could not find a free run directory next to {base}")


def setup_model(args, tasks):
    """Returns (model, public model info, trusted sources for the local runner)."""
    if args.mock:
        model = MockModel(tasks)
        info = {"backend": "mock", "model": "mock (synthetic)", "base_url": "n/a",
                "temperature": None, "max_tokens": None}
        return model, info, model.trusted_sources()
    settings = ModelSettings.from_env(getattr(args, "model", None))
    problems = settings.problems()
    if problems:
        raise SystemExit("error: " + "; ".join(problems) + " (see .env.example)")
    return OpenAICompatibleModel(settings), settings.public_dict(), []


class Session:
    """One run directory: manifest, episodes.jsonl, transcripts.jsonl, candidates/, budget."""

    def __init__(self, args, out: Path, tasks, experiment: str):
        self.args, self.out = args, out
        self.model, self.model_info, trusted = setup_model(args, tasks)
        self.runner = make_runner(args.runner, args.mock, out / "_exec", args.exec_timeout, trusted)
        out.mkdir(parents=True, exist_ok=True)  # unique_run_dir guarantees it is new or empty
        self.log = None if args.mock else (lambda msg: print(f"      {msg}", flush=True))
        self.budget = None
        self.experiment = experiment
        self.ep_file = (out / "episodes.jsonl").open("a", encoding="utf-8")
        self.tr_file = None if args.omit_prompts else (out / "transcripts.jsonl").open("a", encoding="utf-8")
        self.episodes: list[dict] = []

    def base_manifest(self, ts: str, tasks) -> dict:
        a = self.args
        return {
            "run_id": f"{ts}-{uuid.uuid4().hex[:6]}",
            "experiment": self.experiment,
            "mode": "mock" if a.mock else "live",
            "synthetic": bool(a.mock),
            "status": "running",
            "started_at": agent.now_iso(),
            "model": self.model_info,
            "max_llm_calls_per_episode": agent.MAX_ATTEMPTS,
            "api_retries": a.api_retries,
            "retry_wait_s": a.retry_wait,
            "min_interval_s": a.min_interval,
            "task_hashes": {t.task_id: {"split": t.split, "family": t.family, **t.file_hashes()} for t in tasks},
            "runner": self.runner.describe(),
            "store_transcripts": not a.omit_prompts,
            "code_version": code_version(),
            "python": platform.python_version(),
            "command": " ".join(sys.argv),
        }

    def write(self, rec: dict, transcript: list[dict], candidates: dict | None = None) -> None:
        self.ep_file.write(json.dumps(rec) + "\n")
        self.ep_file.flush()
        if self.tr_file:
            for t in transcript:
                self.tr_file.write(json.dumps(t) + "\n")
            self.tr_file.flush()
        for n, code in (candidates or {}).items():  # every attempt's code, kept for analysis
            d = self.out / "candidates" / rec["episode_id"]
            d.mkdir(parents=True, exist_ok=True)
            (d / f"attempt_{n}.py").write_text(code, encoding="utf-8")
        self.episodes.append(rec)
        extra = ""
        if rec.get("experiment") == "v2":
            extra = (f" first_hidden={rec['first_hidden_success']!s:<5} revision={rec['revision_reason'] or '-':<6}")
        print(f"  {rec['episode_id']:<62} calls={rec['llm_calls']}{extra} hidden_pass={rec['hidden_success']!s:<5} "
              f"[{rec['error_category']}]", flush=True)
        if rec["fatal"]:
            raise RunAborted(rec["fatal"])

    def v2_episode(self, task, cfg: RevisionConfig, phase: str, rep: int, pos: int | None) -> dict:
        a = self.args
        eid = f"{phase}-{cfg.config_id}-{task.task_id}-r{rep}"
        rec, transcript, cands = run_revision_episode(
            task, cfg, self.model, self.runner, self.budget, phase=phase, repetition=rep, episode_id=eid,
            api_retries=a.api_retries, retry_wait_s=a.retry_wait, order_position=pos, log=self.log)
        self.write(rec, transcript, cands)
        return rec

    def finish(self, manifest: dict, status: str) -> int:
        self.ep_file.close()
        if self.tr_file:
            self.tr_file.close()
        manifest["status"] = status
        manifest["finished_at"] = agent.now_iso()
        manifest["model_calls_used"] = self.budget.used if self.budget else 0
        _write_json(self.out / "manifest.json", manifest)
        shutil.rmtree(self.out / "_exec", ignore_errors=True)
        report = write_report(self.out)
        print(f"[run] status={status} model_calls_used={manifest['model_calls_used']}  report: {report}", flush=True)
        return 0 if status == "completed" else 2


def run_guarded(session: Session, manifest: dict, body) -> int:
    status = "completed"
    try:
        body()
    except RunAborted as exc:
        status = f"aborted: {exc}"
        print(f"[run] ABORTED: {exc}", flush=True)
    except RunnerUnavailable as exc:
        status = f"aborted: runner unavailable: {exc}"
        print(f"[run] ABORTED: {exc}", flush=True)
    except KeyboardInterrupt:
        status = "interrupted"
        print("[run] INTERRUPTED (Ctrl+C); partial results are kept", flush=True)
    finally:
        code = session.finish(manifest, status)
    return code


def rotated(configs: list, k: int) -> list:
    k %= len(configs)
    return configs[k:] + configs[:k]


def write_metrics(out: Path, episodes: list[dict]) -> None:
    by = {}
    for phase in sorted({e["phase"] for e in episodes}):
        eps = [e for e in episodes if e["phase"] == phase]
        by[phase] = {cid: revision_metrics([e for e in eps if e["config_id"] == cid])
                     for cid in sorted({e["config_id"] for e in eps})}
    _write_json(out / "metrics.json", by)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ---------------------------------------------------------------------------
# run (v1: original experiment; v2: revision policy x repair context)
# ---------------------------------------------------------------------------

def cmd_run(args) -> int:
    load_dotenv()
    return run_v2(args) if args.experiment == "v2" else run_v1(args)


def run_v1(args) -> int:
    tasks = load_tasks()
    opt_tasks, test_tasks = split_tasks(tasks)
    ts = _timestamp()
    reps = args.reps if args.reps is not None else 2
    out = unique_run_dir(args.output, f"{'mock' if args.mock else 'live'}-{ts}")
    s = Session(args, out, tasks, "v1")

    if args.enumerate:
        alternatives = [c for c in search_space() if c != BASELINE]
    else:
        alternatives = sample_alternatives(args.seed, args.alternatives)
    candidates = [BASELINE] + alternatives
    max_calls = args.max_model_calls if args.max_model_calls is not None else 34
    s.budget = CallBudget(max_calls, min_interval_s=args.min_interval, log=s.log)
    planned_eps = len(candidates) * len(opt_tasks) + 2 * len(test_tasks) * reps

    manifest = s.base_manifest(ts, tasks) | {
        "seed": args.seed,
        "max_model_calls": max_calls,
        "heldout_repetitions": reps,
        "enumerate": args.enumerate,
        "baseline": BASELINE.to_dict(),
        "candidates": [c.to_dict() for c in candidates],
        "planned_max_episodes": planned_eps,
        "planned_max_model_calls": planned_eps * agent.MAX_ATTEMPTS,
        "optimization_tasks": [t.task_id for t in opt_tasks],
        "test_tasks": [t.task_id for t in test_tasks],
        "prompt_templates": {"system": agent.SYSTEM_PROMPTS, "initial_user": agent.INITIAL_USER_TEMPLATE},
    }
    _write_json(out / "manifest.json", manifest)
    print(f"[run] v1 {manifest['mode']} run {manifest['run_id']} -> {out}  (runner: {s.runner.name}, "
          f"candidates: {', '.join(c.config_id for c in candidates)}, budget: {max_calls} calls)", flush=True)

    def episode(task, cfg, phase, rep, pos=None):
        eid = f"{phase}-{cfg.config_id}-{task.task_id}-r{rep}"
        rec, transcript = agent.run_episode(task, cfg, s.model, s.runner, s.budget, phase=phase, repetition=rep,
                                            episode_id=eid, api_retries=args.api_retries,
                                            retry_wait_s=args.retry_wait, order_position=pos, log=s.log)
        s.write(rec, transcript)
        return rec

    def body():
        print("[optimization]", flush=True)
        opt_eps = [episode(t, c, "optimization", 0) for c in candidates for t in opt_tasks]
        selection = select_winner(candidates, opt_eps)
        selection["frozen_at"] = agent.now_iso()
        _write_json(out / "selection.json", selection)  # frozen before any held-out episode runs
        selected = HarnessConfig.from_id(selection["selected_config_id"])
        print(f"[selection] {selected.config_id} (decided by {selection['decided_by']})", flush=True)
        if not selection["reliable"]:
            print(f"[selection] WARNING: {len(selection['infra_error_episodes'])} optimization episode(s) ended "
                  "in API/runner errors; this selection is not reliable (see report).", flush=True)
        print("[held-out]", flush=True)
        configs = [BASELINE] if selected == BASELINE else [BASELINE, selected]
        if selected == BASELINE:
            print("  baseline selected: no alternative; held-out observations are reused", flush=True)
        for rep in range(reps):
            for i, task in enumerate(test_tasks):
                order = configs if (rep + i) % 2 == 0 else configs[::-1]
                for pos, cfg in enumerate(order):
                    episode(task, cfg, "heldout", rep, pos)

    return run_guarded(s, manifest, body)


def run_v2(args) -> int:
    bench_dir = Path(args.benchmark_dir) if args.benchmark_dir else BENCHMARK_V2_DIR
    try:
        tasks, frozen = verify_frozen(bench_dir)
    except BenchmarkError as exc:
        raise SystemExit(f"error: {exc}")
    opt_tasks, test_tasks = split_tasks(tasks)
    opt_reps, ho_reps = V2_PRESETS[args.preset]
    opt_reps = args.opt_reps if args.opt_reps is not None else opt_reps
    ho_reps = args.reps if args.reps is not None else ho_reps
    configs = space_v2()
    planned_eps = len(configs) * len(opt_tasks) * opt_reps + 2 * len(test_tasks) * ho_reps
    planned_calls = planned_eps * agent.MAX_ATTEMPTS
    max_calls = args.max_model_calls if args.max_model_calls is not None else planned_calls

    ts = _timestamp()
    out = unique_run_dir(args.output, f"v2-{args.preset}-{'mock' if args.mock else 'live'}-{ts}")
    s = Session(args, out, tasks, "v2")
    s.budget = CallBudget(max_calls, min_interval_s=args.min_interval, log=s.log)
    manifest = s.base_manifest(ts, tasks) | {
        "preset": args.preset,
        "optimization_repetitions": opt_reps,
        "heldout_repetitions": ho_reps,
        "max_model_calls": max_calls,
        "baseline": BASELINE_V2.to_dict(),
        "candidates": [c.to_dict() for c in configs],
        "planned_max_episodes": planned_eps,
        "planned_max_model_calls": planned_calls,
        "benchmark_dir": str(bench_dir),
        "benchmark_frozen_at": frozen.get("frozen_at"),
        "optimization_tasks": [t.task_id for t in opt_tasks],
        "test_tasks": [t.task_id for t in test_tasks],
        "prompt_templates": revision.prompt_templates(),
    }
    _write_json(out / "manifest.json", manifest)
    print(f"[run] v2 {manifest['mode']} run {manifest['run_id']} -> {out}  (preset {args.preset}: "
          f"{planned_eps} episodes, max {planned_calls} calls before API retries; budget {max_calls})", flush=True)

    def body():
        print("[optimization]", flush=True)
        opt_eps = []
        for rep in range(opt_reps):
            for i, task in enumerate(opt_tasks):
                for pos, cfg in enumerate(rotated(configs, rep * len(opt_tasks) + i)):
                    opt_eps.append(s.v2_episode(task, cfg, "optimization", rep, pos))
        selection = select_winner(configs, opt_eps, baseline=BASELINE_V2)
        selection["frozen_at"] = agent.now_iso()
        _write_json(out / "selection.json", selection)  # frozen before any held-out episode runs
        selected = RevisionConfig.from_id(selection["selected_config_id"])
        print(f"[selection] {selected.config_id} (decided by {selection['decided_by']})", flush=True)
        if not selection["reliable"]:
            print(f"[selection] WARNING: {len(selection['infra_error_episodes'])} optimization episode(s) ended "
                  "in API/runner errors; this selection is not reliable (see report).", flush=True)
        print("[held-out]", flush=True)
        held = [BASELINE_V2] if selected == BASELINE_V2 else [BASELINE_V2, selected]
        if selected == BASELINE_V2:
            print("  baseline selected: no alternative; held-out observations are reused", flush=True)
        for rep in range(ho_reps):
            for i, task in enumerate(test_tasks):
                for pos, cfg in enumerate(held if (rep + i) % 2 == 0 else held[::-1]):
                    s.v2_episode(task, cfg, "heldout", rep, pos)

    try:
        return run_guarded(s, manifest, body)
    finally:
        write_metrics(out, s.episodes)


def cmd_calibrate(args) -> int:
    load_dotenv()
    tasks = load_tasks(CALIBRATION_DIR)
    configs = [BASELINE_V2] if args.configs == "baseline" else space_v2()
    planned_eps = len(configs) * len(tasks) * args.reps
    planned_calls = planned_eps * agent.MAX_ATTEMPTS
    max_calls = args.max_model_calls if args.max_model_calls is not None else planned_calls
    ts = _timestamp()
    out = unique_run_dir(args.output, f"calibration-{'mock' if args.mock else 'live'}-{ts}")
    s = Session(args, out, tasks, "v2-calibration")
    s.budget = CallBudget(max_calls, min_interval_s=args.min_interval, log=s.log)
    manifest = s.base_manifest(ts, tasks) | {
        "configs_option": args.configs,
        "repetitions": args.reps,
        "max_model_calls": max_calls,
        "baseline": BASELINE_V2.to_dict(),
        "candidates": [c.to_dict() for c in configs],
        "planned_max_episodes": planned_eps,
        "planned_max_model_calls": planned_calls,
        "calibration_tasks": [t.task_id for t in tasks],
        "prompt_templates": revision.prompt_templates(),
    }
    _write_json(out / "manifest.json", manifest)
    print(f"[calibrate] {manifest['mode']} run {manifest['run_id']} -> {out}  ({len(tasks)} tasks x "
          f"{len(configs)} config(s) x {args.reps} rep(s) = {planned_eps} episodes, max {planned_calls} "
          f"calls before API retries; budget {max_calls})", flush=True)

    def body():
        for rep in range(args.reps):
            for i, task in enumerate(tasks):
                for pos, cfg in enumerate(rotated(configs, rep * len(tasks) + i)):
                    s.v2_episode(task, cfg, "calibration", rep, pos)

    try:
        return run_guarded(s, manifest, body)
    finally:
        write_metrics(out, s.episodes)


def cmd_freeze(args) -> int:
    bench_dir = Path(args.benchmark_dir) if args.benchmark_dir else BENCHMARK_V2_DIR
    try:
        path = freeze_benchmark(bench_dir, now=agent.now_iso())
    except (BenchmarkError, FileNotFoundError) as exc:
        raise SystemExit(f"error: {exc}")
    print(f"[freeze] wrote {path}")
    return 0


# ---------------------------------------------------------------------------
# smoke
# ---------------------------------------------------------------------------

def _check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""), flush=True)
    return ok


def cmd_smoke(args) -> int:
    load_dotenv()
    tasks = load_tasks()
    cal_tasks = load_tasks(CALIBRATION_DIR)
    results = []
    work = Path(args.workdir).resolve() if args.workdir else REPO_ROOT / "runs" / "_smoke"
    if args.mock:
        print("[smoke] offline checks (mock backend; no API calls)")
        mock = MockModel(tasks + cal_tasks)
        runner = make_runner(args.runner, True, work, args.exec_timeout, mock.trusted_sources())
    else:
        print("[smoke] live preflight (no API calls are made)")
        settings = ModelSettings.from_env()
        problems = settings.problems()
        results.append(_check("credentials configured", not problems, "; ".join(problems) or settings.public_dict()["base_url"]))
        try:
            runner = make_runner("docker", False, work, args.exec_timeout, [])
        except SystemExit as exc:
            _check("docker available", False, str(exc))
            return 1
    print(f"[smoke] runner: {runner.name}")

    print("[smoke] task suites (v1 benchmark + v2 calibration)")
    for t in tasks + cal_tasks:
        ref_pub = runner.run(t.reference_source, t, "public")
        ref_hid = runner.run(t.reference_source, t, "hidden")
        bug_pub = runner.run(t.buggy_source, t, "public")
        results.append(_check(f"{t.task_id} ({t.split}): reference passes public+hidden, buggy fails public",
                              ref_pub.outcome == "passed" and ref_hid.outcome == "passed" and bug_pub.outcome == "failed",
                              f"ref={ref_pub.outcome}/{ref_hid.outcome} buggy={bug_pub.outcome} failed={bug_pub.failed_tests}"))
        if args.mock:
            for name in ("partial", "regressed"):
                variant = t.mock_variant(name)
                if variant:
                    vp, vh = runner.run(variant, t, "public"), runner.run(variant, t, "hidden")
                    results.append(_check(f"{t.task_id} mock_{name}: passes public, fails hidden",
                                          vp.outcome == "passed" and vh.outcome == "failed", f"{vp.outcome}/{vh.outcome}"))

    print("[smoke] runner outcomes")
    mi = next(t for t in tasks if t.task_id == "merge_intervals")
    r = runner.run(mi.reference_source, mi, "public")
    results.append(_check("known-correct candidate -> passed", r.outcome == "passed", f"{r.duration_s:.2f}s"))
    r = runner.run(mi.buggy_source, mi, "public")
    results.append(_check("known-broken candidate -> failed", r.outcome == "failed", ", ".join(r.failed_tests)))
    r = runner.run(TIMEOUT_CANDIDATE, mi, "public")
    results.append(_check("infinite-loop candidate -> timeout", r.outcome == "timeout", f"{r.duration_s:.2f}s"))

    if args.mock:
        print("[smoke] mock episodes (SYNTHETIC)")
        rec, _ = agent.run_episode(mi, BASELINE, mock, runner, CallBudget(2), phase="optimization",
                                   repetition=0, episode_id="smoke")
        results.append(_check("v1: retry repairs merge_intervals in 2 calls",
                              rec["hidden_success"] and rec["llm_calls"] == 2 and not rec["first_attempt_public_pass"],
                              " -> ".join(a["outcome"] for a in rec["attempts"])))
        rs = next(t for t in cal_tasks if t.mock_scenario == "review_benefit")
        rec, _, _ = run_revision_episode(rs, RevisionConfig("always_review", "full"), mock, runner, CallBudget(2),
                                         phase="calibration", repetition=0, episode_id="smoke-v2")
        results.append(_check("v2: always_review+full repairs a public-pass/hidden-fail candidate",
                              rec["first_attempt_public_pass"] and not rec["first_hidden_success"]
                              and rec["revision_reason"] == "review" and rec["hidden_success"],
                              " -> ".join(a["outcome"] for a in rec["attempts"])))
    shutil.rmtree(work, ignore_errors=True)
    ok = all(results)
    print(f"[smoke] {'ALL PASSED' if ok else 'FAILURES'} ({sum(results)}/{len(results)})")
    return 0 if ok else 1


def cmd_report(args) -> int:
    path = write_report(Path(args.run_dir))
    print(path.read_text(encoding="utf-8"))
    print(f"\n[report] written to {path}")
    return 0


def cmd_build_image(args) -> int:
    ok, detail = docker_status()
    if not ok:
        raise SystemExit(f"error: Docker is unavailable: {detail}")
    build_image(args.image)
    return 0


def _add_model_options(p) -> None:
    p.add_argument("--mock", action="store_true", help="use the synthetic mock model (no API calls)")
    p.add_argument("--output", default=None, help="run directory; never overwritten (a suffix is added)")
    p.add_argument("--max-model-calls", type=int, default=None,
                   help="global cap on model requests INCLUDING API re-sends, checked before every request "
                        "(default: v1 34; v2/calibrate: the planned maximum)")
    p.add_argument("--api-retries", type=int, default=0,
                   help="re-sends after transient API errors (429/5xx/network) per request; default 0")
    p.add_argument("--retry-wait", type=float, default=10.0,
                   help="base backoff seconds before a re-send, doubled each time (max 120s)")
    p.add_argument("--min-interval", type=float, default=0.0,
                   help="pause (seconds) after each model request finishes before the next one starts")
    p.add_argument("--model", default=None, help="override HARNESS_MODEL")
    p.add_argument("--runner", choices=["auto", "docker", "local-trusted"], default="auto")
    p.add_argument("--exec-timeout", type=int, default=10, help="seconds per test execution")
    p.add_argument("--omit-prompts", action="store_true", help="do not write transcripts.jsonl")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m harness_mvp",
                                description="Black-box harness-configuration search for a tiny code-repair agent.")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("smoke", help="offline checks (--mock) or live preflight without API calls")
    s.add_argument("--mock", action="store_true", help="offline synthetic checks")
    s.add_argument("--runner", choices=["auto", "docker", "local-trusted"], default="auto")
    s.add_argument("--exec-timeout", type=int, default=5)
    s.add_argument("--workdir", default=None)
    s.set_defaults(func=cmd_smoke)

    r = sub.add_parser("run", help="optimize, freeze the winner, evaluate held-out tasks, report")
    _add_model_options(r)
    r.add_argument("--experiment", choices=["v1", "v2"], default="v1",
                   help="v1: instruction style x retry feedback (original, 5 tasks); "
                        "v2: revision policy x repair context (frozen tasks_v2 benchmark)")
    r.add_argument("--seed", type=int, default=42, help="v1: seed for sampling alternative configs")
    r.add_argument("--alternatives", type=int, default=2, help="v1: sampled non-baseline configs (max 3)")
    r.add_argument("--enumerate", action="store_true", help="v1: evaluate all 3 alternatives instead of sampling")
    r.add_argument("--preset", choices=sorted(V2_PRESETS), default="full",
                   help="v2: full = 2 optimization reps + 3 held-out reps; pilot = 1 + 1")
    r.add_argument("--opt-reps", type=int, default=None, help="v2: override optimization repetitions")
    r.add_argument("--reps", type=int, default=None, help="held-out repetitions (v1 default 2; v2 from preset)")
    r.add_argument("--benchmark-dir", default=None, help="v2: frozen benchmark directory (default tasks_v2)")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("calibrate", help="v2 development-only calibration on tasks_calibration (no selection)")
    _add_model_options(c)
    c.add_argument("--configs", choices=["baseline", "all"], default="all",
                   help="baseline = on_failure+minimal only (cheapest); all = the four v2 configs")
    c.add_argument("--reps", type=int, default=1, help="repetitions per task and config")
    c.set_defaults(func=cmd_calibrate)

    f = sub.add_parser("freeze-benchmark", help="validate and freeze the v2 benchmark (ids, splits, hashes)")
    f.add_argument("--benchmark-dir", default=None)
    f.set_defaults(func=cmd_freeze)

    rp = sub.add_parser("report", help="regenerate report.md for a run directory")
    rp.add_argument("run_dir")
    rp.set_defaults(func=cmd_report)

    from .experiment import register
    register(sub)

    b = sub.add_parser("build-image", help="build the Docker runner image")
    b.add_argument("--image", default=DEFAULT_IMAGE)
    b.set_defaults(func=cmd_build_image)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
