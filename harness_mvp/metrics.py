"""Experiment v2 metrics. Every rate is a (numerator, denominator) pair; None when nothing is eligible."""
from __future__ import annotations

from .search import INFRA_CATEGORIES


def _rate(eps: list[dict], eligible, success) -> dict | None:
    pool = [e for e in eps if eligible(e)]
    if not pool:
        return None
    k = sum(1 for e in pool if success(e))
    return {"k": k, "n": len(pool), "rate": k / len(pool)}


def _sum(eps: list[dict], field: str) -> dict:
    vals = [e.get(field) for e in eps]
    missing = sum(v is None for v in vals)
    return {"total": None if missing or not eps else sum(vals), "missing_episodes": missing}


def revision_metrics(eps: list[dict]) -> dict:
    """Outcome, revision and resource metrics over a set of v2 episodes (failed episodes included)."""
    every = lambda e: True  # noqa: E731
    revised = lambda e: e["revised"]  # noqa: E731
    calls = sum(e["llm_calls"] for e in eps)
    return {
        "episodes": len(eps),
        "tasks": len({e["task_id"] for e in eps}),
        "first_attempt_public_pass": _rate(eps, every, lambda e: e["first_attempt_public_pass"]),
        "first_attempt_hidden_success": _rate(eps, every, lambda e: e["first_hidden_success"]),
        "final_hidden_success": _rate(eps, every, lambda e: e["hidden_success"]),
        "revision_activation": _rate(eps, every, lambda e: e["revision_triggered"]),
        # Among initially hidden-failing episodes that received a revision: final hidden success.
        "recovery": _rate(eps, lambda e: revised(e) and not e["first_hidden_success"],
                          lambda e: e["hidden_success"]),
        # Among initially public-passing but hidden-failing episodes that received a review: repaired.
        "review_benefit": _rate(eps, lambda e: revised(e) and e["first_attempt_public_pass"]
                                and not e["first_hidden_success"], lambda e: e["hidden_success"]),
        # Among initially hidden-correct episodes that received a revision: made incorrect.
        "regression": _rate(eps, lambda e: revised(e) and e["first_hidden_success"],
                            lambda e: not e["hidden_success"]),
        "model_calls": {"total": calls, "per_episode": calls / len(eps) if eps else None},
        "input_tokens": _sum(eps, "input_tokens"),
        "output_tokens": _sum(eps, "output_tokens"),
        "total_tokens": _sum(eps, "total_tokens"),
        "model_latency_s": {"total": round(sum(e["model_latency_s"] for e in eps), 3),
                            "per_call": sum(e["model_latency_s"] for e in eps) / calls if calls else None},
        "episode_latency_s": {"mean": sum(e["episode_latency_s"] for e in eps) / len(eps) if eps else None},
        "wait_s": round(sum(e.get("wait_s", 0) for e in eps), 3),
        "failed_requests": sum(e["api_errors"] for e in eps),
        "infra_error_episodes": sum(1 for e in eps if e["error_category"] in INFRA_CATEGORIES),
        "timeouts": sum(e["timeouts"] for e in eps),
    }


def fmt_rate(r: dict | None) -> str:
    return "N/A (no eligible episodes)" if r is None else f"{r['k']}/{r['n']} ({100 * r['rate']:.0f}%)"


def fmt_sum(s: dict, n: int) -> str:
    if s["missing_episodes"]:
        return f"n/a (missing in {s['missing_episodes']}/{n} episodes)"
    return "n/a" if s["total"] is None else f"{s['total']} (mean {s['total'] / n:.0f}/episode)"


def metric_rows(m: dict) -> list[tuple[str, str]]:
    n = m["episodes"]
    pc = m["model_latency_s"]["per_call"]
    el = m["episode_latency_s"]["mean"]
    return [
        ("Episodes / distinct tasks", f"{n} / {m['tasks']}"),
        ("First-attempt public-test pass", fmt_rate(m["first_attempt_public_pass"])),
        ("First-attempt hidden-test success", fmt_rate(m["first_attempt_hidden_success"])),
        ("Final hidden-test success", fmt_rate(m["final_hidden_success"])),
        ("Revision activation", fmt_rate(m["revision_activation"])),
        ("Recovery (initially hidden-failing, revised)", fmt_rate(m["recovery"])),
        ("Review benefit (public-pass/hidden-fail, revised)", fmt_rate(m["review_benefit"])),
        ("Regression (initially hidden-correct, revised)", fmt_rate(m["regression"])),
        ("Model calls (total, per episode)",
         f"{m['model_calls']['total']} ({m['model_calls']['per_episode']:.2f})" if n else "n/a"),
        ("Input tokens", fmt_sum(m["input_tokens"], n) if n else "n/a"),
        ("Output tokens", fmt_sum(m["output_tokens"], n) if n else "n/a"),
        ("Total tokens", fmt_sum(m["total_tokens"], n) if n else "n/a"),
        ("Model latency (mean per call)", "n/a" if pc is None else f"{pc:.2f}s"),
        ("Episode latency (mean, excl. waits)", "n/a" if el is None else f"{el:.2f}s"),
        ("Deliberate waiting (total)", f"{m['wait_s']:.1f}s"),
        ("Failed API requests", str(m["failed_requests"])),
        ("Episodes ended by API/runner errors", str(m["infra_error_episodes"])),
        ("Execution timeouts", str(m["timeouts"])),
    ]
