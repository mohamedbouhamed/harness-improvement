"""Sending model requests through the global budget, with optional retries after transient errors."""
from __future__ import annotations

import time
from datetime import datetime, timezone

from .model import AuthError, BudgetExhausted, CallBudget, ModelError

MAX_BACKOFF_S = 120.0
sleep = time.sleep  # patched in tests


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def backoff_delay(retry_no: int, base_s: float, retry_after: float | None) -> float:
    """Exponential backoff (base, 2*base, 4*base, ...), at least the server's hint, capped."""
    return min(MAX_BACKOFF_S, max(base_s * 2 ** (retry_no - 1), retry_after or 0.0))


def sum_or_none(values):
    """Sum, or None if any value is missing. Missing usage is never treated as zero."""
    values = list(values)
    return None if not values or any(v is None for v in values) else sum(values)


def _call(model, messages, budget: CallBudget, context: dict) -> tuple[object, dict]:
    """One model request; waits for it to finish. Returns (response or exception, record)."""
    budget.reserve()  # raises BudgetExhausted before anything is sent; applies the pause
    start = time.perf_counter()
    try:
        resp = model.complete(messages, context=context)
    except ModelError as exc:
        return exc, {"ok": False, "error_category": exc.category, "error": str(exc)[:500],
                     "status": getattr(exc, "status", None), "retry_after": getattr(exc, "retry_after", None),
                     "input_tokens": None, "output_tokens": None, "total_tokens": None,
                     "latency_s": round(time.perf_counter() - start, 3), "finish_reason": None}
    finally:
        budget.release()
    return resp, {"ok": True, "error_category": None, "error": None,
                  "input_tokens": resp.input_tokens, "output_tokens": resp.output_tokens,
                  "total_tokens": resp.total_tokens, "latency_s": round(resp.latency_s, 3),
                  "finish_reason": resp.finish_reason}


def request(model, messages, budget: CallBudget, context: dict, *, api_retries: int, retry_wait_s: float,
            log, label: str) -> tuple[object | None, list[dict], str | None, float]:
    """Send one logical request, re-sending only after transient API errors (429/5xx/network).

    Every request, re-sends included, goes through the global budget.
    Returns (response or None, request records, fatal category or None, backoff seconds waited).
    """
    records: list[dict] = []
    result, fatal, waited = None, None, 0.0
    for retry_no in range(1 + api_retries):
        if retry_no:
            delay = backoff_delay(retry_no, retry_wait_s, getattr(result, "retry_after", None))
            log(f"waiting {delay:.0f}s before retry {retry_no}/{api_retries}")
            sleep(delay)
            waited += delay
        try:
            result, rec = _call(model, messages, budget, context)
        except BudgetExhausted:
            return None, records, "budget_exhausted", waited
        records.append(rec)
        log(f"request #{budget.used}/{budget.max_calls} ({label}): "
            + (f"ok in {rec['latency_s']:.1f}s, out={rec['output_tokens']} tok, finish={rec['finish_reason']}"
               if rec["ok"] else rec["error"][:90]))
        if isinstance(result, AuthError):
            return None, records, "auth_error", waited
        if not isinstance(result, Exception):
            return result, records, None, waited
        if not getattr(result, "retryable", False):
            break  # e.g. HTTP 400: re-sending the same request will not help
    return None, records, fatal, waited
