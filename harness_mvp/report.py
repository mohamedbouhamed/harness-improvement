"""Builds report.md from manifest.json, selection.json and episodes.jsonl."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .metrics import fmt_rate, metric_rows, revision_metrics
from .search import INFRA_CATEGORIES

SMALL_SAMPLE = ("**Small-sample warning.** This experiment uses 5 tasks (3 optimization, 2 held-out). "
                "That is enough to validate the pipeline, not to establish generalizable performance. "
                "No p-values or significance claims are made; a null or negative result is a valid outcome.")
SYNTHETIC = ("> **SYNTHETIC RESULTS — mock model backend.** Every number below comes from scripted "
             "mock responses with estimated token counts. These are NOT real-model observations.")


def load_run(run_dir: Path) -> tuple[dict, dict | None, list[dict]]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    sel_path = run_dir / "selection.json"
    selection = json.loads(sel_path.read_text(encoding="utf-8")) if sel_path.exists() else None
    episodes = []
    ep_path = run_dir / "episodes.jsonl"
    if ep_path.exists():
        episodes = [json.loads(l) for l in ep_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return manifest, selection, episodes


def _rate(k: int, n: int) -> str:
    return f"{k}/{n} ({100 * k / n:.0f}%)" if n else "n/a"


def _tok_sum(eps: list[dict], field: str) -> str:
    vals = [e.get(field) for e in eps]
    missing = sum(v is None for v in vals)
    if not eps:
        return "n/a"
    if missing:
        return f"n/a (missing in {missing}/{len(eps)} episodes)"
    total = sum(vals)
    return f"{total} (mean {total / len(eps):.0f}/episode)"


def aggregate(eps: list[dict]) -> dict[str, str]:
    n = len(eps)
    calls = [e["llm_calls"] for e in eps]
    total_calls = sum(calls)
    return {
        "Episodes": str(n),
        "Distinct tasks": str(len({e["task_id"] for e in eps})),
        "Final hidden-test success": _rate(sum(e["hidden_success"] for e in eps), n),
        "First-attempt public-test pass": _rate(sum(e["first_attempt_public_pass"] for e in eps), n),
        "LLM calls per episode (mean, min-max)":
            f"{total_calls / n:.2f} ({min(calls)}-{max(calls)})" if n else "n/a",
        "Input tokens": _tok_sum(eps, "input_tokens"),
        "Output tokens": _tok_sum(eps, "output_tokens"),
        "Total tokens": _tok_sum(eps, "total_tokens"),
        "Model-request latency (mean per call)":
            f"{sum(e['model_latency_s'] for e in eps) / total_calls:.2f}s" if total_calls else "n/a",
        "Episode latency (mean per episode, excl. waits)":
            f"{sum(e['episode_latency_s'] for e in eps) / n:.2f}s" if n else "n/a",
        "Deliberate waiting (pacing + retry backoff, total)": f"{sum(e.get('wait_s', 0) for e in eps):.1f}s",
        "API errors (failed requests)": str(sum(e["api_errors"] for e in eps)),
        "Execution timeouts": str(sum(e["timeouts"] for e in eps)),
        "Error categories": ", ".join(f"{k}: {v}" for k, v in sorted(Counter(e["error_category"] for e in eps).items())) or "n/a",
    }


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join("" if c is None else str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _yn(b) -> str:
    return "yes" if b else "no"


def build_report_v1(run_dir: Path) -> str:
    manifest, selection, episodes = load_run(run_dir)
    mock = manifest.get("mode") == "mock" or any(e.get("synthetic") for e in episodes)
    opt = [e for e in episodes if e["phase"] == "optimization"]
    held = [e for e in episodes if e["phase"] == "heldout"]
    L = [f"# Harness MVP report — run `{manifest.get('run_id')}`", ""]
    if mock:
        L += [SYNTHETIC, ""]
    else:
        L += ["> Real-model observations from the configured endpoint (see settings).", ""]
    L += [SMALL_SAMPLE, ""]
    status = manifest.get("status", "unknown")
    if status != "completed":
        L += [f"> **Run status: {status}.** Results below are partial.", ""]

    m = manifest.get("model", {})
    L += ["## Run settings", "", _table(["Setting", "Value"], [
        ["Mode", manifest.get("mode")],
        ["Model", m.get("model")],
        ["Endpoint", m.get("base_url", "n/a")],
        ["Temperature / max output tokens", f"{m.get('temperature')} / {m.get('max_tokens')}"],
        ["Seed", manifest.get("seed")],
        ["Candidates", ", ".join(c["config_id"] for c in manifest.get("candidates", []))],
        ["Held-out repetitions", manifest.get("heldout_repetitions")],
        ["Max model calls (global) / used", f"{manifest.get('max_model_calls')} / {manifest.get('model_calls_used', 'n/a')}"],
        ["API retries / backoff base / min interval",
         f"{manifest.get('api_retries', 0)} / {manifest.get('retry_wait_s', 'n/a')}s / {manifest.get('min_interval_s', 0)}s"],
        ["Runner", manifest.get("runner", {}).get("runner")],
        ["Started / finished", f"{manifest.get('started_at')} / {manifest.get('finished_at', 'n/a')}"],
        ["Code version", manifest.get("code_version")],
    ]), "",
        "Fixed seeds make candidate sampling reproducible; they do not make hosted-model outputs deterministic.", ""]

    L += ["## Optimization phase (selection data only)", "",
          f"{len(opt)} episodes on optimization tasks: {', '.join(manifest.get('optimization_tasks', []))}. "
          "Held-out scores were not available to the selector.", ""]
    opt_infra = [e for e in opt if e["error_category"] in INFRA_CATEGORIES]
    if selection and opt_infra:
        L += [f"> **SELECTION NOT RELIABLE.** {len(opt_infra)}/{len(opt)} optimization episodes ended in "
              "API/runner errors and were counted as unsolved, so the ranking partly reflects infrastructure "
              "failures rather than harness quality. Do not draw conclusions; re-run (e.g. with "
              "`--min-interval` and `--api-retries`).", ""]
    if selection:
        infra_by_cfg = {c: sum(1 for e in opt_infra if e["config_id"] == c) for c in {e["config_id"] for e in opt}}
        rows = [[r["config_id"] + (" (baseline)" if r["config_id"] == selection["baseline_config_id"] else ""),
                 f"{r['solved']}/{r['episodes']}", f"{r['first_attempt_public_pass']}/{r['episodes']}",
                 r["llm_calls"], "n/a (missing)" if r["total_tokens"] is None else r["total_tokens"],
                 infra_by_cfg.get(r["config_id"], 0),
                 "**selected**" if r["config_id"] == selection["selected_config_id"] else ""]
                for r in selection["ranking"]]
        L += [_table(["Config", "Solved (hidden)", "1st-attempt public pass", "LLM calls", "Total tokens",
                      "Infra errors", ""], rows), "",
              f"Selection rule: {selection['rule']}.", "",
              f"Selected **{selection['selected_config_id']}** (decided by: {selection['decided_by']}).", ""]
        if selection.get("token_tiebreak_note"):
            L += [f"Note: {selection['token_tiebreak_note']}.", ""]
    else:
        L += ["No selection was made (optimization phase did not complete).", ""]

    L += ["## Held-out evaluation (test split, frozen configuration)", ""]
    if selection and held:
        base_id, sel_id = selection["baseline_config_id"], selection["selected_config_id"]
        base_eps = [e for e in held if e["config_id"] == base_id]
        if selection["selected_is_baseline"]:
            L += ["**No alternative was selected**: the baseline won on the optimization tasks. The held-out "
                  "observations below are the baseline's and are reused for the 'selected' column; no "
                  "duplicate evaluations were paid for.", ""]
            sel_eps = base_eps
        else:
            sel_eps = [e for e in held if e["config_id"] == sel_id]
        held_infra = [e for e in held if e["error_category"] in INFRA_CATEGORIES]
        if held_infra:
            L += [f"> **Held-out results affected by infrastructure errors:** {len(held_infra)}/{len(held)} "
                  "held-out episodes ended in API/runner errors (counted as failures). Success rates below are "
                  "not a fair comparison.", ""]
        a, b = aggregate(base_eps), aggregate(sel_eps)
        L += [_table(["Metric", f"Baseline ({base_id})", f"Selected ({sel_id})"],
                     [[k, a[k], b[k]] for k in a]), ""]
    else:
        L += ["No held-out episodes recorded.", ""]

    L += ["## Per-task results", ""]
    rows = []
    for e in episodes:
        rows.append([e["phase"], e["task_id"], e["split"], e["config_id"], e["repetition"],
                     " → ".join(a["outcome"] or "?" for a in e["attempts"]),
                     _yn(e["first_attempt_public_pass"]), _yn(e["hidden_success"]), e["llm_calls"],
                     "n/a" if e["input_tokens"] is None else e["input_tokens"],
                     "n/a" if e["output_tokens"] is None else e["output_tokens"],
                     f"{e['model_latency_s']:.2f}s", f"{e['episode_latency_s']:.2f}s", e["error_category"]])
    L += [_table(["Phase", "Task", "Split", "Config", "Rep", "Attempt outcomes", "1st pass", "Hidden pass",
                  "Calls", "In tok", "Out tok", "Model lat.", "Episode lat.", "Error category"], rows), ""]

    problems = [e for e in episodes if e["api_errors"] or e["fatal"] or e["error_category"] in ("runner_error", "timeout")]
    L += ["## Errors and missing data", ""]
    if problems:
        for e in problems:
            errs = [r["error"] for a in e["attempts"] for r in a["requests"] if r["error"]]
            L.append(f"- `{e['episode_id']}`: {e['error_category']}" + (f" — {errs[0][:200]}" if errs else ""))
    else:
        L.append("- No API errors, runner errors, timeouts or aborts recorded.")
    missing = sum(1 for e in episodes if e["total_tokens"] is None)
    if missing:
        L.append(f"- Token usage missing in {missing} episode(s); shown as n/a, never as zero.")
    L += ["", "## Interpretation limits", "",
          "- Success means passing hidden tests; hidden tests repeat the public cases plus extra edge cases.",
          "- With 2 held-out tasks × 2 repetitions per configuration, differences of one episode are within noise.",
          "- Only automatic configuration selection was performed; the model and harness code were not modified.",
          ""]
    return "\n".join(L)


# ---------------------------------------------------------------------------
# Experiment v2 and calibration reports
# ---------------------------------------------------------------------------

EXPLORATORY = ("**Exploratory only.** Samples are small (a handful of tasks and repetitions per configuration). "
               "Differences of a few episodes are within noise; no p-values or significance claims are made, "
               "and no improvement is claimed unless it is measured below. A null or negative result is valid.")
CALIBRATION_TARGET = (0.30, 0.70)


def _settings_rows(manifest: dict) -> list[list]:
    m = manifest.get("model", {})
    return [
        ["Experiment", manifest.get("experiment")],
        ["Mode", manifest.get("mode")],
        ["Model", m.get("model")],
        ["Endpoint", m.get("base_url", "n/a")],
        ["Temperature / max output tokens", f"{m.get('temperature')} / {m.get('max_tokens')}"],
        ["Configurations", ", ".join(c["config_id"] for c in manifest.get("candidates", []))],
        ["Baseline", manifest.get("baseline", {}).get("config_id")],
        ["Planned max episodes / model calls (before API retries)",
         f"{manifest.get('planned_max_episodes')} / {manifest.get('planned_max_model_calls')}"],
        ["Global request budget (incl. re-sends) / used", f"{manifest.get('max_model_calls')} / {manifest.get('model_calls_used', 'n/a')}"],
        ["API retries / backoff base / min interval",
         f"{manifest.get('api_retries', 0)} / {manifest.get('retry_wait_s', 'n/a')}s / {manifest.get('min_interval_s', 0)}s"],
        ["Runner", manifest.get("runner", {}).get("runner")],
        ["Started / finished", f"{manifest.get('started_at')} / {manifest.get('finished_at', 'n/a')}"],
        ["Code version", manifest.get("code_version")],
    ]


def _metrics_table(groups: list[tuple[str, list[dict]]]) -> str:
    computed = [(label, metric_rows(revision_metrics(eps))) for label, eps in groups]
    rows = [[name] + [rows[i][1] for _, rows in computed] for i, (name, _) in enumerate(computed[0][1])]
    return _table(["Metric"] + [label for label, _ in computed], rows)


def _episode_rows(episodes: list[dict]) -> list[list]:
    rows = []
    for e in episodes:
        rows.append([e["phase"], e["task_id"], e["config_id"], e["repetition"],
                     " → ".join(a["outcome"] or "?" for a in e["attempts"]),
                     e["revision_reason"] or "-", _yn(e["first_hidden_success"]), _yn(e["hidden_success"]),
                     _yn(e["candidate_changed"]), e["llm_calls"],
                     "n/a" if e["total_tokens"] is None else e["total_tokens"], e["error_category"]])
    return rows


EPISODE_HEADERS = ["Phase", "Task", "Config", "Rep", "Attempt outcomes", "Revision", "1st hidden",
                   "Final hidden", "Changed", "Calls", "Tokens", "Error category"]

DEFINITIONS = [
    "- *First-attempt* metrics score the attempt-1 candidate; its hidden tests run only after the episode ended.",
    "- *Revision activation*: share of episodes where the policy requested a second attempt.",
    "- *Recovery*: final hidden success among episodes that failed hidden tests initially and received a revision.",
    "- *Review benefit*: among episodes whose first candidate passed public but failed hidden tests and received a revision, the share repaired.",
    "- *Regression*: among episodes whose first candidate passed hidden tests and received a revision, the share made incorrect.",
    "- The final candidate is the latest syntactically valid one, scored even if the revision regressed it.",
    "- Denominators include failed episodes; N/A means no episode was eligible.",
]


def _errors_section(episodes: list[dict]) -> list[str]:
    L = ["## Errors and missing data", ""]
    problems = [e for e in episodes if e["api_errors"] or e["fatal"] or e["error_category"] in ("runner_error", "timeout")]
    for e in problems:
        errs = [r["error"] for a in e["attempts"] for r in a["requests"] if r["error"]]
        L.append(f"- `{e['episode_id']}`: {e['error_category']}, {e['api_errors']} failed request(s)"
                 + (f" — {errs[0][:160]}" if errs else ""))
    if not problems:
        L.append("- No API errors, runner errors, timeouts or aborts recorded.")
    missing = sum(1 for e in episodes if e["total_tokens"] is None)
    if missing:
        L.append(f"- Token usage missing in {missing} episode(s); shown as n/a, never as zero.")
    return L + [""]


def _header(manifest: dict, episodes: list[dict], title: str) -> list[str]:
    mock = manifest.get("mode") == "mock" or any(e.get("synthetic") for e in episodes)
    L = [f"# {title} — run `{manifest.get('run_id')}`", ""]
    L += [SYNTHETIC if mock else "> Real-model observations from the configured endpoint (see settings).", ""]
    L += [EXPLORATORY, ""]
    if manifest.get("status") != "completed":
        L += [f"> **Run status: {manifest.get('status')}.** Results below are partial.", ""]
    L += ["## Run settings", "", _table(["Setting", "Value"], _settings_rows(manifest)), "",
          "Fixed seeds and temperature 0 do not make hosted-model outputs deterministic.", ""]
    return L


def build_report_v2(run_dir: Path) -> str:
    manifest, selection, episodes = load_run(run_dir)
    opt = [e for e in episodes if e["phase"] == "optimization"]
    held = [e for e in episodes if e["phase"] == "heldout"]
    configs = [c["config_id"] for c in manifest.get("candidates", [])]
    L = _header(manifest, episodes, "Harness experiment v2 report")
    L += ["## Optimization phase (selection data only)", "",
          f"{len(opt)} episodes; tasks: {', '.join(manifest.get('optimization_tasks', []))}; "
          f"{manifest.get('optimization_repetitions')} repetition(s). Held-out results were not available to the selector.", ""]
    opt_infra = [e for e in opt if e["error_category"] in INFRA_CATEGORIES]
    if opt_infra:
        L += [f"> **SELECTION NOT RELIABLE.** {len(opt_infra)}/{len(opt)} optimization episodes ended in API/runner "
              "errors and were counted as unsolved.", ""]
    if opt:
        L += [_metrics_table([(c, [e for e in opt if e["config_id"] == c]) for c in configs]), ""]
    if selection:
        L += [f"Selection rule: {selection['rule']}.", "",
              f"Selected **{selection['selected_config_id']}** (decided by: {selection['decided_by']}).", ""]
        if selection.get("token_tiebreak_note"):
            L += [f"Note: {selection['token_tiebreak_note']}.", ""]
    else:
        L += ["No selection was made (optimization phase did not complete).", ""]

    L += ["## Held-out evaluation (test split, frozen configuration)", ""]
    if selection and held:
        base_id, sel_id = selection["baseline_config_id"], selection["selected_config_id"]
        base_eps = [e for e in held if e["config_id"] == base_id]
        sel_eps = base_eps if selection["selected_is_baseline"] else [e for e in held if e["config_id"] == sel_id]
        if selection["selected_is_baseline"]:
            L += ["**No alternative was selected**: the baseline won on the optimization tasks; its held-out "
                  "observations are reused for both columns (no duplicate evaluations were run).", ""]
        held_infra = [e for e in held if e["error_category"] in INFRA_CATEGORIES]
        if held_infra:
            L += [f"> **Held-out results affected by infrastructure errors:** {len(held_infra)}/{len(held)} episodes.", ""]
        L += [_metrics_table([(f"Baseline ({base_id})", base_eps), (f"Selected ({sel_id})", sel_eps)]), ""]
    else:
        L += ["No held-out episodes recorded.", ""]
    L += ["## Resource consumption (all phases, all episodes)", "",
          _metrics_table([("All episodes", episodes)]) if episodes else "No episodes.", "",
          "## Metric definitions", "", *DEFINITIONS, "",
          "## Per-episode results", "", _table(EPISODE_HEADERS, _episode_rows(episodes)), ""]
    return "\n".join(L + _errors_section(episodes))


def build_report_calibration(run_dir: Path) -> str:
    manifest, _, episodes = load_run(run_dir)
    configs = [c["config_id"] for c in manifest.get("candidates", [])]
    lo, hi = CALIBRATION_TARGET
    L = _header(manifest, episodes, "Calibration report (development-only tasks)")
    L += ["Calibration tasks are never used in the final evaluation.", "",
          "## Task difficulty", "",
          f"Exploratory target: roughly {lo:.0%}–{hi:.0%} first-attempt hidden success per task. "
          "This is a guide for choosing benchmark difficulty, not a requirement.", ""]
    rows = []
    for task_id in manifest.get("calibration_tasks", []):
        eps = [e for e in episodes if e["task_id"] == task_id]
        m = revision_metrics(eps)
        r = m["first_attempt_hidden_success"]
        band = "n/a" if r is None else ("too easy" if r["rate"] > hi else "too hard" if r["rate"] < lo else "in range")
        rows.append([task_id, len(eps), fmt_rate(m["first_attempt_public_pass"]), fmt_rate(r),
                     fmt_rate(m["final_hidden_success"]), fmt_rate(m["revision_activation"]), band])
    L += [_table(["Task", "Episodes", "1st public pass", "1st hidden success", "Final hidden success",
                  "Revision activation", "Band"], rows), ""]
    if episodes:
        L += ["## Per-configuration metrics", "",
              _metrics_table([(c, [e for e in episodes if e["config_id"] == c]) for c in configs]), "",
              "## Metric definitions", "", *DEFINITIONS, "",
              "## Per-episode results", "", _table(EPISODE_HEADERS, _episode_rows(episodes)), ""]
    return "\n".join(L + _errors_section(episodes))


def build_report(run_dir: Path) -> str:
    experiment = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8")).get("experiment", "v1")
    if experiment == "v2":
        return build_report_v2(run_dir)
    if experiment == "v2-calibration":
        return build_report_calibration(run_dir)
    return build_report_v1(run_dir)


def write_report(run_dir: Path) -> Path:
    path = run_dir / "report.md"
    path.write_text(build_report(run_dir), encoding="utf-8")
    return path
