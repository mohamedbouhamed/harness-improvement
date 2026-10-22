"""Study orchestration: memory construction -> (optional) development selection -> freeze -> evaluation.

A study directory holds every stage; nothing is ever overwritten:
    study.json              mode, model settings, limits, benchmark hashes
    memory_source/          memory-source repair episodes (baseline condition)
    extraction/             whitelisted extractor inputs, raw outputs, candidate lessons
    memory_bank.json        validated, deduplicated, hashed (written once)
    selection/              optional development-set retrieval-threshold selection (public outcomes only)
    frozen_protocol.json    bank/static/retrieval/limits/model/test-task hashes (written once)
    eval-<split>[-N]/       condition comparison episodes
    report.md               study report
"""
from __future__ import annotations

import json
import platform
import shutil
import sys
from dataclasses import asdict
from pathlib import Path

from . import llm
from .benchmark import BENCHMARK_DIR, RepoTask, by_split, load_benchmark
from .contracts import (CONDITIONS, PROTOCOL_VERSION, AgentLimits, ContractError, RetrievalSettings,
                        canonical_hash)
from .guidance import build_guidance, static_hash
from .memory import (LLMExtractor, MockExtractor, build_bank, candidate_lessons, extraction_input, load_bank,
                     parse_lessons, write_bank)
from .mock import MockRepoModel
from .model import BudgetExhausted, CallBudget, ModelSettings, OpenAICompatibleModel, load_dotenv
from .repo_agent import run_episode, system_prompt
from .runner import DockerRunner, RunnerUnavailable, TrustedLocalRunner, docker_status

SELECTION_THRESHOLDS = (2, 3, 4)
EXTRACTION_CALLS_PER_EPISODE = 1


class StudyError(Exception):
    pass


class Aborted(Exception):
    pass


def write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def unique_dir(base: Path) -> Path:
    """A new directory path: `base` if unused, otherwise base-2, base-3, ... (never overwrite)."""
    if not base.exists() or not any(base.iterdir()):
        return base
    for i in range(2, 10_000):
        cand = base.with_name(f"{base.name}-{i}")
        if not cand.exists():
            return cand
    raise StudyError(f"no free directory next to {base}")


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

def make_runner(kind: str, mock: bool, work_root: Path, timeout_s: int, trusted: list[str]):
    if kind == "local-trusted" and not mock:
        raise StudyError("--runner local-trusted is only allowed in mock mode")
    if kind in ("docker", "auto"):
        ok, detail = docker_status()
        if ok:
            runner = DockerRunner(work_root, timeout_s=timeout_s)
            runner.ensure_ready(build=True)
            return runner
        if kind == "docker" or not mock:
            raise StudyError(f"Docker is required to execute model-generated code ({detail.splitlines()[0]}). "
                             "Start Docker, or use --mock for an offline synthetic run.")
        print(f"[runner] Docker unavailable; mock mode uses the trusted local runner "
              "(allowlisted repo-authored files only)", flush=True)
    return TrustedLocalRunner(work_root, trusted, timeout_s=timeout_s)


class Backends:
    def __init__(self, mock: bool, tasks: list[RepoTask], runner_kind: str, work_root: Path, limits: AgentLimits):
        self.mock = mock
        if mock:
            self.model = MockRepoModel(tasks)
            self.extractor = MockExtractor()
            self.model_info = {"backend": "mock", "model": "mock (synthetic)", "base_url": "n/a",
                               "temperature": None, "max_tokens": None}
            trusted = self.model.trusted_sources()
        else:
            load_dotenv()
            settings = ModelSettings.from_env()
            if settings.problems():
                raise StudyError("; ".join(settings.problems()) + " (see .env.example)")
            self.model = OpenAICompatibleModel(settings)
            self.model_info = settings.public_dict()
            self.extractor = LLMExtractor(self.model, self.model_info)
            trusted = []
        self.runner = make_runner(runner_kind, mock, work_root, limits.exec_timeout_s, trusted)


# ---------------------------------------------------------------------------
# Stage: a directory with a manifest, episodes, transcripts and candidate snapshots
# ---------------------------------------------------------------------------

class Stage:
    def __init__(self, study: "Study", name: str, budget: CallBudget, info: dict, unique: bool = False):
        base = study.dir / name
        self.dir = unique_dir(base) if unique else base
        if self.dir.exists() and any(self.dir.iterdir()):
            raise StudyError(f"{self.dir} already exists; stages are never overwritten")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.study, self.budget, self.episodes = study, budget, []
        self.calls_at_start = budget.used  # budgets may be shared across stages
        self.manifest = {"stage": name, "stage_dir": self.dir.name, "status": "running",
                         "started_at": llm.now_iso(), "max_model_calls": budget.max_calls, **info}
        write_json(self.dir / "manifest.json", self.manifest)
        self.ep_file = (self.dir / "episodes.jsonl").open("a", encoding="utf-8")
        self.tr_file = (None if study.args.omit_prompts
                        else (self.dir / "transcripts.jsonl").open("a", encoding="utf-8"))

    def episode(self, task: RepoTask, condition: str, rep: int, pos: int, guidance: dict, phase: str) -> tuple[dict, dict]:
        a, st = self.study.args, self.study
        eid = f"{phase}-{condition}-{task.task_id}-r{rep}"
        ws = self.dir / "_workspaces" / eid
        rec, transcript, art = run_episode(
            task, st.backends.model, st.backends.runner, self.budget, ws, episode_id=eid, phase=phase,
            condition=condition, repetition=rep, guidance=guidance, limits=st.limits,
            api_retries=a.api_retries, retry_wait_s=a.retry_wait, order_position=pos, log=st.log)
        shutil.rmtree(ws, ignore_errors=True)
        self.ep_file.write(json.dumps(rec) + "\n")
        self.ep_file.flush()
        if self.tr_file:
            for t in transcript:
                self.tr_file.write(json.dumps(t) + "\n")
            self.tr_file.flush()
        cdir = self.dir / "candidates" / eid
        for label, files in [("final", art["final"]), ("submitted", art["submitted"])] + sorted(art["snapshots"].items()):
            for rel, content in files.items():
                path = cdir / label / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
        self.episodes.append(rec)
        mids = ",".join(rec["guidance"]["memory_ids"]) or "-"
        print(f"  {eid:<52} calls={rec['model_calls']:<2} tools={rec['tool_calls']:<2} "
              f"bad_replies={rec['parse_errors']:<2} edits={rec['edits']:<2} memories={mids:<26} "
              f"fallback={rec['fallback_activated']!s:<5} hidden_pass={rec['hidden_success']!s:<5} "
              f"[{rec['error_category']}]", flush=True)
        if rec["fatal"]:
            raise Aborted(rec["fatal"])
        return rec, art

    def close(self, status: str, extra: dict | None = None) -> None:
        self.ep_file.close()
        if self.tr_file:
            self.tr_file.close()
        shutil.rmtree(self.dir / "_workspaces", ignore_errors=True)
        shutil.rmtree(self.study.dir / "_exec", ignore_errors=True)  # per-execution temp dirs (recreated on demand)
        self.manifest.update(status=status, finished_at=llm.now_iso(),
                             model_calls_used=self.budget.used - self.calls_at_start,
                             global_budget_used=self.budget.used, **(extra or {}))
        write_json(self.dir / "manifest.json", self.manifest)


def guarded(stage: Stage, body) -> str:
    status = "completed"
    try:
        body()
    except Aborted as exc:
        status = f"aborted: {exc}"
    except BudgetExhausted as exc:
        status = f"aborted: {exc}"
    except RunnerUnavailable as exc:
        status = f"aborted: runner unavailable: {exc}"
    except KeyboardInterrupt:
        status = "interrupted"
    finally:
        stage.close(status)
    print(f"[{stage.manifest['stage']}] status={status} model_calls_used={stage.manifest['model_calls_used']} "
          f"-> {stage.dir}", flush=True)
    return status


def rotated(items: list, k: int) -> list:
    k %= len(items)
    return items[k:] + items[:k]


# ---------------------------------------------------------------------------
# Study
# ---------------------------------------------------------------------------

class Study:
    def __init__(self, args, study_dir: Path, create: bool):
        self.args, self.dir = args, Path(study_dir)
        self.tasks = load_benchmark(Path(args.benchmark) if getattr(args, "benchmark", None) else BENCHMARK_DIR)
        self.limits = AgentLimits()
        if create:
            if self.dir.exists() and any(self.dir.iterdir()):
                raise StudyError(f"{self.dir} already exists; use a new --study path")
            self.dir.mkdir(parents=True, exist_ok=True)
        elif not (self.dir / "study.json").exists():
            raise StudyError(f"{self.dir} is not a study (missing study.json); create it with build-memory or pipeline")
        else:
            mode = read_json(self.dir / "study.json")["mode"]
            if mode != ("mock" if args.mock else "live"):
                raise StudyError(f"{self.dir} is a {mode} study; pass --{mode}")
        self.log = None if args.mock else (lambda m: print(f"      {m}", flush=True))
        self.backends = Backends(args.mock, self.tasks, args.runner, self.dir / "_exec", self.limits)
        info = self.describe()
        if create:
            write_json(self.dir / "study.json", info | {"created_at": llm.now_iso(), "command": " ".join(sys.argv)})
        else:
            stored = read_json(self.dir / "study.json")
            for key in ("mode", "model", "agent_limits", "protocol_version"):
                if stored[key] != info[key]:
                    raise StudyError(f"study {key} changed since creation: {stored[key]} != {info[key]}")

    def describe(self) -> dict:
        return {"protocol_version": PROTOCOL_VERSION, "mode": "mock" if self.args.mock else "live",
                "synthetic": bool(self.args.mock), "model": self.backends.model_info,
                "agent_limits": self.limits.to_dict(), "system_prompt_hash": canonical_hash(system_prompt(self.limits)),
                "runner": self.backends.runner.describe(), "python": platform.python_version(),
                "benchmark_hashes": {t.task_id: {"split": t.split, **t.hashes()} for t in self.tasks}}

    def budget(self, planned: int) -> CallBudget:
        cap = self.args.max_model_calls if self.args.max_model_calls is not None else planned
        return CallBudget(cap, min_interval_s=self.args.min_interval, log=self.log)

    def split(self, name: str) -> list[RepoTask]:
        return by_split(self.tasks, name)

    def bank(self, expected_hash: str | None = None) -> dict:
        path = self.dir / "memory_bank.json"
        if not path.exists():
            raise StudyError("no memory_bank.json; run build-memory first")
        bank = load_bank(path, expected_hash)
        if bank["synthetic"] and not self.args.mock:
            raise StudyError("a synthetic (mock) memory bank cannot be used in a live evaluation")
        return bank

    # ----- stage 1: memory-source episodes + extraction + bank -----
    def build_memory(self, reps: int) -> dict:
        tasks = self.split("memory_source")
        planned_eps = len(tasks) * reps
        planned = planned_eps * (self.limits.max_model_calls + EXTRACTION_CALLS_PER_EPISODE)
        stage = Stage(self, "memory_source", self.budget(planned),
                      {"repetitions": reps, "tasks": [t.task_id for t in tasks],
                       "planned_max_model_calls": planned_eps * self.limits.max_model_calls})
        print(f"[memory_source] {planned_eps} episodes; worst case {planned} model calls incl. extraction "
              f"(before API re-sends)", flush=True)
        results = []

        def body():
            for rep in range(reps):
                for i, t in enumerate(tasks):
                    g = build_guidance("baseline", t.public_view(), None, RetrievalSettings())
                    results.append((t,) + stage.episode(t, "baseline", rep, 0, g, "memory_source"))
        if guarded(stage, body) != "completed":
            raise StudyError("memory-source stage did not complete; no memory bank was built")

        ext_dir = self.dir / "extraction"
        ext_dir.mkdir()
        created = llm.now_iso()
        cfg = self.backends.extractor.config()
        inputs, outputs, cands, ext_requests = [], [], [], []
        for t, rec, art in results:
            payload = extraction_input(t, rec, art["submitted"])
            inputs.append(payload)
            text, requests, fatal = self.backends.extractor.extract(
                payload, stage.budget, api_retries=self.args.api_retries, retry_wait_s=self.args.retry_wait, log=self.log)
            ext_requests += requests
            raw, err = parse_lessons(text) if text is not None else (None, f"no response ({fatal or 'api_error'})")
            outputs.append({"episode_id": rec["episode_id"], "response": text, "parse_error": err,
                            "requests": requests})
            if fatal in ("auth_error", "budget_exhausted"):
                raise StudyError(f"extraction stopped: {fatal}")
            if raw:
                cands += candidate_lessons(raw, t, rec, art["submitted"], cfg, created, self.args.mock)
        bank = build_bank(cands, created)
        for name, rows in (("inputs.jsonl", inputs), ("outputs.jsonl", outputs), ("candidates.jsonl", cands)):
            (ext_dir / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
        write_json(ext_dir / "manifest.json", {
            "extractor": cfg, "episodes": len(results), "model_calls": len(ext_requests),
            "input_tokens": llm.sum_or_none(r["input_tokens"] for r in ext_requests),
            "output_tokens": llm.sum_or_none(r["output_tokens"] for r in ext_requests),
            "failed_requests": sum(1 for r in ext_requests if not r["ok"]),
            "candidate_counts": bank["candidate_counts"], "created_at": created})
        write_bank(self.dir / "memory_bank.json", bank)
        print(f"[extraction] candidates {bank['candidate_counts']} -> {len(bank['memories'])} memories "
              f"(bank {bank['bank_hash'][:12]})", flush=True)
        return bank

    # ----- optional: development-set retrieval-threshold selection (public outcomes only) -----
    def select(self, reps: int) -> dict:
        if (self.dir / "frozen_protocol.json").exists():
            raise StudyError("protocol already frozen; selection must happen before freezing")
        bank = self.bank()
        tasks = self.split("development")
        base = RetrievalSettings()
        variants, seen = [], {}
        for th in SELECTION_THRESHOLDS:
            s = RetrievalSettings(max_memories=base.max_memories, min_score=th, budget_tokens=base.budget_tokens)
            key = canonical_hash([build_guidance("learned_memory", t.public_view(), bank["memories"], s)["memory_ids"]
                                  for t in tasks])
            variants.append((th, s, seen.get(key)))
            seen.setdefault(key, th)
        to_run = [v for v in variants if v[2] is None]
        planned = len(to_run) * len(tasks) * reps * self.limits.max_model_calls
        stage = Stage(self, "selection", self.budget(planned),
                      {"thresholds": list(SELECTION_THRESHOLDS), "repetitions": reps,
                       "tasks": [t.task_id for t in tasks], "planned_max_model_calls": planned,
                       "bank_hash": bank["bank_hash"],
                       "criterion": "count of development episodes whose submitted candidate passes the public tests; "
                                    "ties: fewer total API tokens if known for all, then the higher threshold"})
        print(f"[selection] thresholds {[v[0] for v in to_run]} need runs (identical retrievals reused); "
              f"worst case {planned} model calls", flush=True)
        stats: dict[int, dict] = {}

        def body():
            for th, s, _ in to_run:
                eps = []
                for rep in range(reps):
                    for t in tasks:
                        g = build_guidance("learned_memory", t.public_view(), bank["memories"], s, bank["bank_hash"])
                        eps.append(stage.episode(t, "learned_memory", rep, 0, g, f"selection-t{th}")[0])
                stats[th] = {"episodes": len(eps), "public_pass": sum(e["submitted_public_pass"] for e in eps),
                             "total_tokens": llm.sum_or_none(e["total_tokens"] for e in eps)}
        status = guarded(stage, body)
        if status != "completed":
            raise StudyError(f"selection did not complete ({status}); nothing selected")
        for th, _, same_as in variants:
            if same_as is not None:
                stats[th] = stats[same_as] | {"reused_from_threshold": same_as}
        tokens_known = all(v["total_tokens"] is not None for v in stats.values())
        best = sorted(stats, key=lambda th: (-stats[th]["public_pass"],
                                             stats[th]["total_tokens"] if tokens_known else 0, -th))[0]
        chosen = RetrievalSettings(base.max_memories, best, base.budget_tokens)
        result = {"selected_min_score": best, "retrieval_settings": chosen.to_dict(), "stats": stats,
                  "token_tiebreak_available": tokens_known, "uses_hidden_tests": False,
                  "selected_at": llm.now_iso()}
        write_json(stage.dir / "selection.json", result)
        print(f"[selection] min_score={best} (public passes {stats[best]['public_pass']}/{stats[best]['episodes']})", flush=True)
        return result

    # ----- freeze -----
    def freeze(self, test_reps: int) -> dict:
        path = self.dir / "frozen_protocol.json"
        if path.exists():
            raise StudyError("frozen_protocol.json already exists; a protocol is frozen once")
        bank = self.bank()
        sel_path = self.dir / "selection" / "selection.json"
        settings = (RetrievalSettings(**read_json(sel_path)["retrieval_settings"]) if sel_path.exists()
                    else RetrievalSettings())
        tests = self.split("test")
        frozen = {"protocol_version": PROTOCOL_VERSION, "frozen_at": llm.now_iso(),
                  "mode": "mock" if self.args.mock else "live", "model": self.backends.model_info,
                  "memory_bank_hash": bank["bank_hash"], "memory_bank_size": len(bank["memories"]),
                  "static_guidance_hash": static_hash(), "retrieval_settings": settings.to_dict(),
                  "retrieval_source": "development selection" if sel_path.exists() else "protocol default",
                  "agent_limits": self.limits.to_dict(), "conditions": list(CONDITIONS),
                  "primary_comparison": "learned_memory vs baseline: final hidden-test success on test tasks",
                  "test_repetitions": test_reps,
                  "test_tasks": {t.task_id: t.hashes() for t in tests}}
        write_json(path, frozen)
        print(f"[freeze] bank {bank['bank_hash'][:12]}, min_score={settings.min_score}, "
              f"{len(tests)} test tasks x {test_reps} reps -> {path}", flush=True)
        return frozen

    # ----- evaluation -----
    def evaluate(self, split: str, reps: int | None) -> Path:
        tasks = self.split(split)
        if split == "test":
            frozen = self._check_frozen(tasks)
            reps = frozen["test_repetitions"]
            settings = RetrievalSettings(**frozen["retrieval_settings"])
            bank = self.bank(frozen["memory_bank_hash"])
            if static_hash() != frozen["static_guidance_hash"]:
                raise StudyError("static checklist changed since freezing")
        elif split == "development":
            bank, settings, frozen, reps = self.bank(), RetrievalSettings(), None, reps or 1
        else:
            raise StudyError("evaluate supports the development and test splits")
        memories = bank["memories"]
        bank_hash = bank["bank_hash"]
        planned = len(CONDITIONS) * len(tasks) * reps * self.limits.max_model_calls
        stage = Stage(self, f"eval-{split}", self.budget(planned),
                      {"split": split, "repetitions": reps, "tasks": [t.task_id for t in tasks],
                       "conditions": list(CONDITIONS), "planned_max_model_calls": planned,
                       "memory_bank_hash": bank_hash, "static_guidance_hash": static_hash(),
                       "retrieval_settings": settings.to_dict(), "frozen": frozen is not None}, unique=True)
        print(f"[eval-{split}] {len(tasks)} tasks x {len(CONDITIONS)} conditions x {reps} reps; "
              f"worst case {planned} model calls (before API re-sends)", flush=True)

        def body():
            for rep in range(reps):
                for i, t in enumerate(tasks):
                    for pos, cond in enumerate(rotated(list(CONDITIONS), rep * len(tasks) + i)):
                        if canonical_hash(memories) != bank_hash:  # memory is read-only during evaluation
                            raise StudyError("memory bank changed during evaluation")
                        g = build_guidance(cond, t.public_view(), json.loads(json.dumps(memories)), settings,
                                           bank_hash if cond == "learned_memory" else None)
                        stage.episode(t, cond, rep, pos, g, f"eval-{split}")
        guarded(stage, body)
        if canonical_hash(load_bank(self.dir / "memory_bank.json")["memories"]) != bank_hash:
            raise StudyError("memory bank file changed during evaluation")
        return stage.dir

    def _check_frozen(self, tasks: list[RepoTask]) -> dict:
        path = self.dir / "frozen_protocol.json"
        if not path.exists():
            raise StudyError("held-out evaluation requires frozen_protocol.json; run freeze first")
        frozen = read_json(path)
        current = {t.task_id: t.hashes() for t in tasks}
        if current != frozen["test_tasks"]:
            raise StudyError("test tasks changed since freezing; a new benchmark version is required")
        if frozen["model"] != self.backends.model_info or frozen["agent_limits"] != self.limits.to_dict():
            raise StudyError("model settings or agent limits differ from the frozen protocol")
        for d in sorted(self.dir.glob("eval-test*")):
            m = d / "manifest.json"
            if m.exists() and read_json(m).get("status") == "completed":
                raise StudyError(f"{d.name} already completed a held-out evaluation; further tuning or "
                                 "re-evaluation needs a new benchmark version")
        return frozen


# ---------------------------------------------------------------------------
# Budget planning and CLI
# ---------------------------------------------------------------------------

def plan(tasks: list[RepoTask], limits: AgentLimits, memory_reps: int, test_reps: int,
         select: bool, select_reps: int) -> dict:
    """Worst-case model requests per stage BEFORE API re-sends (re-sends also count against the cap)."""
    n = {s: len(by_split(tasks, s)) for s in ("memory_source", "development", "test")}
    parts = {"memory_source episodes": n["memory_source"] * memory_reps * limits.max_model_calls,
             "extraction": n["memory_source"] * memory_reps * EXTRACTION_CALLS_PER_EPISODE,
             "development selection": (len(SELECTION_THRESHOLDS) * n["development"] * select_reps
                                       * limits.max_model_calls) if select else 0,
             "held-out evaluation": len(CONDITIONS) * n["test"] * test_reps * limits.max_model_calls}
    return parts | {"total": sum(parts.values())}


def _print_plan(p: dict, api_retries: int) -> None:
    for k, v in p.items():
        print(f"  {k:<24} {v:>5}")
    if api_retries:
        print(f"  with --api-retries {api_retries}, each request may be re-sent up to {api_retries} times; "
              f"re-sends count against --max-model-calls (absolute worst case {p['total'] * (1 + api_retries)})")


def _add_common(p) -> None:
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--mock", action="store_true", help="synthetic mock agent and extractor (no API calls)")
    mode.add_argument("--live", action="store_true", help="confirm live mode: makes PAID model calls")
    p.add_argument("--runner", choices=["auto", "docker", "local-trusted"], default="auto")
    p.add_argument("--max-model-calls", type=int, default=None,
                   help="global request cap incl. API re-sends, checked before every request (default: planned maximum)")
    p.add_argument("--api-retries", type=int, default=0, help="re-sends after transient API errors (default 0)")
    p.add_argument("--retry-wait", type=float, default=10.0, help="base backoff seconds, doubled per re-send (max 120)")
    p.add_argument("--min-interval", type=float, default=0.0, help="pause after each request (rate-limit pacing)")
    p.add_argument("--omit-prompts", action="store_true", help="do not store transcripts")
    p.add_argument("--benchmark", default=None, help="benchmark directory (default benchmark/)")


def _run(fn):
    def wrapper(args):
        try:
            return fn(args)
        except (StudyError, ContractError, FileExistsError) as exc:
            raise SystemExit(f"error: {exc}")
    return wrapper


@_run
def cmd_build_memory(args) -> int:
    study = Study(args, Path(args.study), create=not (Path(args.study) / "study.json").exists())
    study.build_memory(args.reps)
    return 0


@_run
def cmd_select(args) -> int:
    Study(args, Path(args.study), create=False).select(args.reps)
    return 0


@_run
def cmd_freeze(args) -> int:
    Study(args, Path(args.study), create=False).freeze(args.test_reps)
    return 0


@_run
def cmd_evaluate(args) -> int:
    from .study_report import write_study_report
    study = Study(args, Path(args.study), create=False)
    out = study.evaluate(args.split, args.reps)
    print(f"[report] {write_study_report(study.dir)}")
    return 0 if read_json(out / "manifest.json")["status"] == "completed" else 2


@_run
def cmd_pipeline(args) -> int:
    from .study_report import write_study_report
    tasks = load_benchmark(Path(args.benchmark) if args.benchmark else BENCHMARK_DIR)
    p = plan(tasks, AgentLimits(), args.memory_reps, args.test_reps, args.select, args.select_reps)
    print("[plan] worst-case model requests (before API re-sends):")
    _print_plan(p, args.api_retries)
    out = unique_dir(Path(args.output))
    if out != Path(args.output):
        print(f"[pipeline] {args.output} exists; writing to {out} (never overwritten)")
    study = Study(args, out, create=True)
    if args.max_model_calls is None:
        args.max_model_calls = p["total"]  # re-sends count against it; pass a larger cap explicitly
    shared = CallBudget(args.max_model_calls, min_interval_s=args.min_interval, log=study.log)
    study.budget = lambda planned: shared  # one global budget across all stages
    study.build_memory(args.memory_reps)
    if args.select:
        study.select(args.select_reps)
    study.freeze(args.test_reps)
    ev = study.evaluate("test", None)
    report = write_study_report(study.dir)
    print(f"[pipeline] model requests used {shared.used}/{shared.max_calls}; report: {report}")
    return 0 if read_json(ev / "manifest.json")["status"] == "completed" else 2


def cmd_plan(args) -> int:
    tasks = load_benchmark(Path(args.benchmark) if args.benchmark else BENCHMARK_DIR)
    print(f"Worst-case model requests (limits {AgentLimits().to_dict()}), before API re-sends:")
    _print_plan(plan(tasks, AgentLimits(), args.memory_reps, args.test_reps, args.select, args.select_reps),
                args.api_retries)
    return 0


def register(sub) -> None:
    def reps_opts(p):
        p.add_argument("--memory-reps", type=int, default=1, help="memory-source repetitions per task")
        p.add_argument("--test-reps", type=int, default=2, help="held-out repetitions per task and condition")
        p.add_argument("--select", action="store_true", help="run development-set threshold selection")
        p.add_argument("--select-reps", type=int, default=1)

    p = sub.add_parser("pipeline", help="memory source -> extraction -> bank -> [selection] -> freeze -> held-out eval -> report")
    p.add_argument("--output", required=True, help="new study directory (a suffix is added if it exists)")
    reps_opts(p)
    _add_common(p)
    p.set_defaults(func=cmd_pipeline)

    p = sub.add_parser("plan", help="print worst-case request budgets; makes no calls")
    reps_opts(p)
    p.add_argument("--api-retries", type=int, default=0)
    p.add_argument("--benchmark", default=None)
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("build-memory", help="memory-source episodes, lesson extraction and the memory bank")
    p.add_argument("--study", required=True)
    p.add_argument("--reps", type=int, default=1)
    _add_common(p)
    p.set_defaults(func=cmd_build_memory)

    p = sub.add_parser("select", help="optional: choose the retrieval threshold on development tasks (public outcomes)")
    p.add_argument("--study", required=True)
    p.add_argument("--reps", type=int, default=1)
    _add_common(p)
    p.set_defaults(func=cmd_select)

    p = sub.add_parser("freeze", help="freeze memory bank, checklist, retrieval settings and test protocol")
    p.add_argument("--study", required=True)
    p.add_argument("--test-reps", type=int, default=2)
    _add_common(p)
    p.set_defaults(func=cmd_freeze)

    p = sub.add_parser("evaluate", help="compare baseline / static_guidance / learned_memory")
    p.add_argument("--study", required=True)
    p.add_argument("--split", choices=["development", "test"], default="test")
    p.add_argument("--reps", type=int, default=None, help="development only; test uses the frozen value")
    _add_common(p)
    p.set_defaults(func=cmd_evaluate)
