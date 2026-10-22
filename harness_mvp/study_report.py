"""Study report: memory construction (and its cost) separately from the condition comparison."""
from __future__ import annotations

import json
from pathlib import Path

from .contracts import CONDITIONS

INFRA = ("api_error", "auth_error", "budget_exhausted", "runner_error")
SYNTHETIC = ("> **SYNTHETIC RESULTS — mock backends.** The agent, extractor and token counts are scripted. "
             "These numbers verify orchestration only; they say nothing about memory effectiveness.")
EXPLORATORY = ("**Exploratory pilot.** A handful of tasks and repetitions: no statistical significance or "
               "generalization is claimed. Null and negative results are valid outcomes.")


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def _table(headers, rows) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    return "\n".join(out + ["| " + " | ".join("" if c is None else str(c) for c in r) + " |" for r in rows])


def _k(eps, pred) -> str:
    n = len(eps)
    return "n/a" if not n else f"{sum(1 for e in eps if pred(e))}/{n}"


def _sum(eps, field) -> str:
    vals = [e.get(field) for e in eps]
    missing = sum(v is None for v in vals)
    if not eps:
        return "n/a"
    return f"n/a (missing in {missing}/{len(eps)})" if missing else str(sum(vals))


def _mean(eps, field, fmt="{:.2f}") -> str:
    return fmt.format(sum(e[field] for e in eps) / len(eps)) if eps else "n/a"


def cost(eps: list[dict]) -> dict:
    calls = sum(e["model_calls"] for e in eps)
    return {"episodes": len(eps), "model_calls": calls, "tool_calls": sum(e["tool_calls"] for e in eps),
            "input_tokens": _sum(eps, "input_tokens"), "output_tokens": _sum(eps, "output_tokens"),
            "model_latency_s": round(sum(e["model_latency_s"] for e in eps), 2),
            "failed_requests": sum(e["api_errors"] for e in eps)}


def condition_rows(eps_by_cond: dict[str, list[dict]]) -> list[list]:
    def row(name, f):
        return [name] + [f(eps_by_cond.get(c, [])) for c in CONDITIONS]
    ok = lambda eps: [e for e in eps if e["error_category"] not in INFRA]  # noqa: E731
    return [
        row("Episodes", lambda eps: len(eps)),
        row("**Final hidden-test success**", lambda eps: _k(eps, lambda e: e["hidden_success"])),
        row("Final hidden success, excluding infra-failed episodes", lambda eps: _k(ok(eps), lambda e: e["hidden_success"])),
        row("Submitted candidate passes public tests", lambda eps: _k(eps, lambda e: e["submitted_public_pass"])),
        row("First agent test run passed", lambda eps: _k(eps, lambda e: e["first_public_pass"])),
        row("Public-test fallback activated (= raw revision regression)", lambda eps: _k(eps, lambda e: e["fallback_activated"])),
        row("…of which the pre-fallback candidate passed hidden tests",
            lambda eps: _k([e for e in eps if e["fallback_activated"]],
                           lambda e: (e["pre_fallback_hidden"] or {}).get("outcome") == "passed")),
        row("Episodes that changed source files", lambda eps: _k(eps, lambda e: bool(e["files_changed"]))),
        row("Edits / test runs (mean)", lambda eps: f"{_mean(eps, 'edits')} / {_mean(eps, 'test_runs')}" if eps else "n/a"),
        row("Model calls (total, mean)", lambda eps: f"{sum(e['model_calls'] for e in eps)} ({_mean(eps, 'model_calls')})" if eps else "n/a"),
        row("Tool calls (total, mean)", lambda eps: f"{sum(e['tool_calls'] for e in eps)} ({_mean(eps, 'tool_calls')})" if eps else "n/a"),
        row("Reply parse errors", lambda eps: sum(e["parse_errors"] for e in eps)),
        row("Input tokens (API)", lambda eps: _sum(eps, "input_tokens")),
        row("Output tokens (API)", lambda eps: _sum(eps, "output_tokens")),
        row("Model latency per call", lambda eps: (f"{sum(e['model_latency_s'] for e in eps) / max(1, sum(e['model_calls'] for e in eps)):.2f}s" if eps else "n/a")),
        row("Episode latency (mean, excl. waits)", lambda eps: _mean(eps, "episode_latency_s", "{:.2f}s")),
        row("Execution timeouts", lambda eps: sum(e["timeouts"] for e in eps)),
        row("API/infrastructure-error episodes", lambda eps: sum(1 for e in eps if e["error_category"] in INFRA)),
        row("Injected context: est. tokens mean / max (budget)",
            lambda eps: (f"{_mean(eps, '_gt', '{:.0f}')} / {max(e['_gt'] for e in eps)} "
                         f"({eps[0]['guidance']['budget_tokens']})") if eps else "n/a"),
        row("Episodes with retrieved memories", lambda eps: _k(eps, lambda e: bool(e["guidance"]["memory_ids"]))),
    ]


PARSE_ERROR_WARN = 0.25  # share of agent turns whose reply could not be parsed


def validity_warnings(eps: list[dict], memory_eps: list[dict], bank: dict | None) -> list[str]:
    """Conditions under which the primary comparison carries no information about memory."""
    out = []
    if bank is not None and not bank.get("memories"):
        out.append("the memory bank is **empty**, so learned_memory sent exactly the same prompt as baseline")
    elif memory_eps and not any(e["guidance"]["memory_ids"] for e in memory_eps):
        out.append("**no memory was retrieved** for any learned_memory episode, so its prompts equal baseline's")
    turns = [t for e in eps for t in e["turns"]]
    bad = sum(1 for t in turns if t["parse_error"])
    if turns and bad / len(turns) > PARSE_ERROR_WARN:
        kinds = sorted({t["parse_error"].split(";")[0][:60] for t in turns if t["parse_error"]})[:4]
        out.append(f"{bad}/{len(turns)} agent turns ({100 * bad / len(turns):.0f}%) were unparseable replies "
                   f"(e.g. {kinds}), so most episodes measure the action protocol, not repair ability")
    if not out:
        return []
    return ["> **PRIMARY COMPARISON NOT INFORMATIVE.** " + "; ".join(out) + ". Differences between conditions "
            "below reflect model nondeterminism or harness problems, not an effect of memory.", ""]


def build_study_report(study_dir: Path) -> str:
    d = Path(study_dir)
    study = _read(d / "study.json") or {}
    bank = _read(d / "memory_bank.json")
    frozen = _read(d / "frozen_protocol.json")
    selection = _read(d / "selection" / "selection.json")
    ms_eps = _jsonl(d / "memory_source" / "episodes.jsonl")
    ext = _read(d / "extraction" / "manifest.json")
    sel_eps = _jsonl(d / "selection" / "episodes.jsonl")
    L = [f"# Memory-guided repair study — `{d.name}`", ""]
    if study.get("synthetic"):
        L += [SYNTHETIC, ""]
    L += [EXPLORATORY, "",
          f"Protocol `{study.get('protocol_version')}` · mode `{study.get('mode')}` · model "
          f"`{(study.get('model') or {}).get('model')}` · limits {study.get('agent_limits')}", ""]

    # ---- memory construction ----
    L += ["## 1. Memory construction (memory_source split only)", ""]
    ms_principles = sorted({p for e in ms_eps for p in e["principles"]})
    if ms_eps:
        L += [_table(["Episode", "End", "Submitted public pass", "Hidden pass (reporting only)", "Fallback"],
                     [[e["episode_id"], e["end_reason"], e["submitted_public_pass"], e["hidden_success"],
                       e["fallback_activated"]] for e in ms_eps]), "",
              "Hidden results of memory-source episodes are reported here but are never given to the extractor.", ""]
    if ext:
        L += [f"Extractor `{ext['extractor'].get('kind')}`: {ext['episodes']} episodes, {ext['model_calls']} calls; "
              f"candidate lessons {ext['candidate_counts']} (flagged = task-specific, rejected = malformed).", ""]
    if bank:
        L += [f"Memory bank `{bank['bank_hash'][:16]}`: {len(bank['memories'])} memories "
              "(unverified hypotheses: public tests are incomplete evidence).", "",
              _table(["Memory", "Trigger", "Evidence episodes", "Source tasks"],
                     [[m["memory_id"], m["trigger"], ", ".join(m["evidence_episode_ids"]),
                       ", ".join(m["provenance"]["source_task_ids"])] for m in bank["memories"]]), ""]
    if selection:
        L += ["### Development selection (public-test outcomes only)", "",
              _table(["min_score", "Submitted public passes", "Total tokens", "Note"],
                     [[th, f"{s['public_pass']}/{s['episodes']}", s["total_tokens"],
                       f"reused from {s['reused_from_threshold']}" if "reused_from_threshold" in s else ""]
                      for th, s in sorted(selection["stats"].items(), key=lambda x: int(x[0]))]), "",
              f"Selected min_score = {selection['selected_min_score']}.", ""]
    construction = [("memory-source episodes", cost(ms_eps)), ("development selection", cost(sel_eps))]
    L += ["### Memory-construction cost (separate from evaluation)", "",
          _table(["Part", "Episodes", "Model calls", "Tool calls", "Input tokens", "Output tokens", "Model latency", "Failed requests"],
                 [[n, c["episodes"], c["model_calls"], c["tool_calls"], c["input_tokens"], c["output_tokens"],
                   f"{c['model_latency_s']}s", c["failed_requests"]] for n, c in construction]
                 + ([["extraction", ext["episodes"], ext["model_calls"], 0, ext["input_tokens"], ext["output_tokens"],
                      "-", ext["failed_requests"]]] if ext else [])), ""]

    # ---- freeze ----
    if frozen:
        L += ["## 2. Frozen protocol", "",
              _table(["Item", "Value"], [
                  ["Frozen at", frozen["frozen_at"]], ["Memory bank hash", frozen["memory_bank_hash"][:16]],
                  ["Static checklist hash", frozen["static_guidance_hash"][:16]],
                  ["Retrieval", f"{frozen['retrieval_settings']} ({frozen['retrieval_source']})"],
                  ["Test tasks × repetitions", f"{len(frozen['test_tasks'])} × {frozen['test_repetitions']}"],
                  ["Primary comparison", frozen["primary_comparison"]]]), ""]

    # ---- evaluations ----
    for ed in sorted(d.glob("eval-*")):
        man = _read(ed / "manifest.json") or {}
        eps = _jsonl(ed / "episodes.jsonl")
        for e in eps:
            e["_gt"] = e["guidance"]["estimated_tokens"]
        L += [f"## 3. Evaluation `{ed.name}` ({man.get('split')} split, status: {man.get('status')})", ""]
        if man.get("status") != "completed":
            L += [f"> **Incomplete run ({man.get('status')}).** Results below are partial.", ""]
        if not eps:
            L += ["No episodes.", ""]
            continue
        by = {c: [e for e in eps if e["condition"] == c] for c in CONDITIONS}
        L += [_table(["Metric"] + list(CONDITIONS), condition_rows(by)), ""]
        b, m = by["baseline"], by["learned_memory"]
        L += validity_warnings(eps, m, bank)
        L += [("**[SYNTHETIC]** " if study.get("synthetic") else "") +
              "**Primary comparison (predeclared): learned_memory vs baseline, final hidden-test success** — "
              f"{_k(m, lambda e: e['hidden_success'])} vs {_k(b, lambda e: e['hidden_success'])} "
              f"(difference {sum(e['hidden_success'] for e in m) - sum(e['hidden_success'] for e in b):+d} episodes). "
              "Descriptive only; static_guidance is the control for generic extra instructions.", ""]
        rel = lambda e: bool(set(e["principles"]) & set(ms_principles))  # noqa: E731
        L += ["### By lesson relevance (maintainer labels; never used by the agent or retrieval)", "",
              f"Memory-source principles: {ms_principles}.", "",
              _table(["Subset"] + list(CONDITIONS),
                     [[name] + [_k([e for e in by[c] if f(e)], lambda e: e["hidden_success"]) for c in CONDITIONS]
                      for name, f in (("lesson-relevant tasks", rel), ("lesson-irrelevant tasks", lambda e: not rel(e)))]), ""]
        tasks = sorted({e["task_id"] for e in eps})
        L += ["### Per task (final hidden-test success; retrieved memories)", "",
              _table(["Task", "Principles"] + list(CONDITIONS) + ["Memories retrieved"],
                     [[t, ", ".join(next(e for e in eps if e["task_id"] == t)["principles"])]
                      + [_k([e for e in by[c] if e["task_id"] == t], lambda e: e["hidden_success"]) for c in CONDITIONS]
                      + [", ".join(sorted({mid for e in by["learned_memory"] if e["task_id"] == t
                                           for mid in e["guidance"]["memory_ids"]})) or "none"]
                      for t in tasks]), "",
              "### Per episode", "",
              _table(["Episode", "Pos", "End", "Calls", "Tools", "Fallback", "Public", "Hidden", "Context est. tok", "Memories", "Category"],
                     [[e["episode_id"], e["order_position"], e["end_reason"], e["model_calls"], e["tool_calls"],
                       e["fallback_activated"], e["submitted_public_pass"], e["hidden_success"], e["_gt"],
                       ",".join(e["guidance"]["memory_ids"]) or "-", e["error_category"]] for e in eps]), ""]
        infra = [e for e in eps if e["error_category"] in INFRA or e["api_errors"]]
        L += ["### Infrastructure / API errors", ""]
        L += [f"- `{e['episode_id']}`: {e['error_category']}, {e['api_errors']} failed request(s)" for e in infra] or ["- none"]
        c = cost(eps)
        L += ["", f"Evaluation cost: {c['model_calls']} model calls, {c['tool_calls']} tool calls, input tokens "
              f"{c['input_tokens']}, output tokens {c['output_tokens']}, {c['failed_requests']} failed requests.", ""]

    L += ["## Limitations", "",
          "- Tiny benchmark: one or two tasks per principle; per-task differences are anecdotal.",
          "- Keyword retrieval can miss relevant lessons and retrieve irrelevant ones; both are visible above.",
          "- Lessons are extracted from public evidence only and are unverified.",
          "- Hosted models are nondeterministic even at temperature 0.", ""]
    return "\n".join(L)


def write_study_report(study_dir: Path) -> Path:
    path = Path(study_dir) / "report.md"
    path.write_text(build_study_report(study_dir), encoding="utf-8")
    return path
