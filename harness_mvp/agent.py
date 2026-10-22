"""The repair agent: prompt construction, code extraction, retry feedback and the episode loop."""
from __future__ import annotations

import ast
import re
import time
from datetime import datetime, timezone

from .model import AuthError, BudgetExhausted, CallBudget, ModelError
from .runner import ExecResult
from .search import HarnessConfig
from .tasks import Task, sha256_text

MAX_ATTEMPTS = 2
MAX_BACKOFF_S = 120.0
sleep = time.sleep  # patched in tests
FULL_FEEDBACK_CAP = 4000
COMPACT_MAX_LINES = 5
COMPACT_LINE_CAP = 160

SYSTEM_PROMPTS = {
    "direct": (
        "You repair bugs in small Python modules. Reply with the complete corrected module "
        "in a single ```python code block and nothing else."
    ),
    "structured": (
        "You repair bugs in small Python modules. Before answering, check privately: "
        "(1) the exact function contract, (2) every public test, (3) edge cases such as empty "
        "inputs, boundaries, ties and ordering, (4) that inputs are not mutated. Do not write "
        "out these checks or any reasoning. Reply with the complete corrected module in a "
        "single ```python code block and nothing else."
    ),
}

RETURN_INSTRUCTION = ("Return the complete corrected module in a single ```python code block, "
                      "with no commentary.")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


INITIAL_USER_TEMPLATE = (
    "Fix the bug in the Python module below so that it satisfies the contract.\n\n"
    "## Contract\nFunction: `{signature}`\n{statement}\n\n"
    "## Current module (buggy)\n```python\n{buggy}```\n\n"
    "## Public tests (your module is saved as candidate.py)\n"
    "```python\n{public_tests}```\n\n"
    "{return_instruction}"
)


def initial_user_message(task: Task) -> str:
    return INITIAL_USER_TEMPLATE.format(signature=task.signature, statement=task.statement,
                                        buggy=task.buggy_source, public_tests=task.public_tests_source,
                                        return_instruction=RETURN_INSTRUCTION)


def build_initial_messages(task: Task, config: HarnessConfig) -> list[dict]:
    return [{"role": "system", "content": SYSTEM_PROMPTS[config.instruction_style]},
            {"role": "user", "content": initial_user_message(task)}]


_FENCE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_PY_LANGS = {"", "python", "py", "python3"}


def extract_code(text: str) -> tuple[str | None, str]:
    """Return (code, status). Status: fenced | multiple_fences_longest | unterminated_fence | raw | no_code."""
    if not text or not text.strip():
        return None, "no_code"
    blocks = [(lang.strip().lower(), body) for lang, body in _FENCE.findall(text)]
    py_blocks = [b for lang, b in blocks if lang in _PY_LANGS] or [b for _, b in blocks]
    py_blocks = [b for b in py_blocks if b.strip()]
    if len(py_blocks) == 1:
        return _norm(py_blocks[0]), "fenced"
    if len(py_blocks) > 1:
        return _norm(max(py_blocks, key=len)), "multiple_fences_longest"
    if blocks:  # only empty fenced blocks
        return None, "no_code"
    if "```" in text:  # opening fence without a closing one (e.g. truncated output)
        after = text.split("```", 1)[1]
        after = after.split("\n", 1)[1] if "\n" in after else ""
        if after.strip():
            return _norm(after), "unterminated_fence"
        return None, "no_code"
    stripped = text.strip()
    if re.search(r"^\s*(def|class|import|from)\s", stripped, re.MULTILINE) and syntax_error(stripped) is None:
        return _norm(stripped), "raw"
    return None, "no_code"


def _norm(code: str) -> str:
    return code.strip("\n") + "\n"


def syntax_error(code: str) -> str | None:
    """Parse only (never executes). Returns an error message or None."""
    try:
        ast.parse(code)
    except SyntaxError as exc:
        return f"line {exc.lineno}: {exc.msg}"
    return None


def _cap_middle(text: str, cap: int) -> str:
    if len(text) <= cap:
        return text
    head = cap // 4
    tail = cap - head
    return text[:head] + f"\n...[{len(text) - cap} chars truncated]...\n" + text[-tail:]


def compact_summary(result: ExecResult) -> str:
    lines = []
    if result.failed_tests:
        lines.append("Failed tests: " + ", ".join(result.failed_tests[:10]))
    errors = []
    for line in (result.stdout + "\n" + result.stderr).splitlines():
        line = line.strip()
        if re.match(r"^[A-Za-z_.]*(Error|Exception)\b", line) and line not in errors:
            errors.append(line[:COMPACT_LINE_CAP])
        if len(errors) >= COMPACT_MAX_LINES:
            break
    if errors:
        lines.append("Errors:\n" + "\n".join(f"- {e}" for e in errors))
    if result.note:
        lines.append(f"Note: {result.note}")
    return "\n".join(lines) or "Public tests failed (no further detail)."


def build_feedback(attempt: dict, public: ExecResult | None, config: HarnessConfig, timeout_s) -> str:
    """Feedback for the retry. Takes ONLY public-test information by construction."""
    outcome = attempt["outcome"]
    if outcome == "no_code":
        body = "Your previous response did not contain a Python code block."
    elif outcome == "syntax_error":
        body = f"Your previous candidate has a syntax error ({attempt['syntax_error']})."
    elif outcome == "timeout":
        body = f"The public tests timed out (limit {timeout_s}s). Check for infinite loops."
    elif config.retry_feedback == "full":
        raw = (public.stdout + ("\n" if public.stdout else "") + public.stderr).strip()
        body = "Public test output:\n```\n" + _cap_middle(raw, FULL_FEEDBACK_CAP) + "\n```"
    else:
        body = compact_summary(public)
    return f"The previous candidate did not pass the public tests.\n\n{body}\n\n{RETURN_INSTRUCTION}"


def _call(model, messages, budget: CallBudget, context: dict) -> tuple[object, dict]:
    """One model request; waits for it to finish. Returns (response or exception, record). No retries here."""
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


def _sum_or_none(values):
    return None if any(v is None for v in values) or not values else sum(values)


def backoff_delay(retry_no: int, base_s: float, retry_after: float | None) -> float:
    """Exponential backoff (base, 2*base, 4*base, ...), at least the server's hint, capped."""
    return min(MAX_BACKOFF_S, max(base_s * 2 ** (retry_no - 1), retry_after or 0.0))


def request_with_retries(model, messages, budget: CallBudget, context: dict, attempt: dict, *,
                         api_retries: int, retry_wait_s: float, log, transcript: list, episode_id: str):
    """Send one logical request and wait for it, re-sending only after transient API errors.

    Every request (including re-sends) goes through the global budget and is appended to
    attempt["requests"]. Returns (response or None, fatal category or None, backoff seconds).
    """
    resp = result = None
    fatal, waited = None, 0.0
    for retry_no in range(1 + api_retries):
        if retry_no:
            delay = backoff_delay(retry_no, retry_wait_s, getattr(result, "retry_after", None))
            log(f"waiting {delay:.0f}s before retry {retry_no}/{api_retries}")
            sleep(delay)
            waited += delay
        try:
            result, req = _call(model, messages, budget, context)
        except BudgetExhausted:
            fatal = "budget_exhausted"
            break
        attempt["requests"].append(req)
        log(f"request #{budget.used}/{budget.max_calls} (attempt {attempt['attempt']}): "
            + (f"ok in {req['latency_s']:.1f}s" if req["ok"] else req["error"][:90]))
        transcript.append({"episode_id": episode_id, "attempt": attempt["attempt"],
                           "messages": list(messages), "ok": req["ok"],
                           "response": None if not req["ok"] else result.text,
                           "error": req["error"]})
        if isinstance(result, AuthError):
            fatal = "auth_error"
            break
        if not isinstance(result, Exception):
            resp = result
            break
        if not getattr(result, "retryable", False):
            break  # e.g. HTTP 400: re-sending the same request will not help
    return resp, fatal, waited


def new_attempt(attempt_no: int) -> dict:
    return {"attempt": attempt_no, "requests": [], "outcome": None, "parse_status": None,
            "candidate_hash": None, "syntax_error": None, "public": None}


def evaluate_reply(text: str, task: Task, runner, attempt: dict) -> tuple[str | None, str | None, ExecResult | None]:
    """Extract code, syntax-check it (parse only) and run the PUBLIC tests in the runner.

    Fills attempt["outcome"]. Returns (extracted code or None, valid code or None, public result).
    """
    code, status = extract_code(text)
    attempt["parse_status"] = status
    if code is None:
        attempt["outcome"] = "no_code"
        return None, None, None
    attempt["candidate_hash"] = sha256_text(code)
    err = syntax_error(code)
    if err:
        attempt["outcome"], attempt["syntax_error"] = "syntax_error", err
        return code, None, None
    public = runner.run(code, task, "public")
    attempt["public"] = public.to_record()
    attempt["outcome"] = {"passed": "public_pass", "failed": "public_fail"}.get(
        public.outcome, public.outcome)  # timeout | runner_error pass through
    return code, code, public


def classify_episode(fatal, attempts: list[dict], final_attempt: int | None, hidden: dict | None) -> str:
    """Primary error category of an episode, derived from the final candidate's outcome."""
    if fatal:
        return fatal
    if hidden and hidden["outcome"] == "passed":
        return "none"
    if any(a["outcome"] == "api_error" for a in attempts):  # an attempt got no response at all
        return "api_error"
    outcome = attempts[final_attempt - 1]["outcome"] if final_attempt else (attempts[-1]["outcome"] if attempts else None)
    if outcome in ("no_code", "syntax_error", "timeout", "runner_error"):
        return outcome
    if outcome == "public_fail":
        return "public_test_failure"
    if hidden and hidden["outcome"] in ("timeout", "runner_error"):
        return hidden["outcome"]
    return "hidden_test_failure"


def resource_fields(attempts: list[dict], waited: float, t0: float) -> dict:
    requests = [r for a in attempts for r in a["requests"]]
    return {
        "llm_calls": len(requests),
        "input_tokens": _sum_or_none([r["input_tokens"] for r in requests]),
        "output_tokens": _sum_or_none([r["output_tokens"] for r in requests]),
        "total_tokens": _sum_or_none([r["total_tokens"] for r in requests]),
        "model_latency_s": round(sum(r["latency_s"] for r in requests), 3),
        # Deliberate waits (pacing + retry backoff) are excluded from episode latency.
        "wait_s": round(waited, 3),
        "episode_latency_s": round(time.perf_counter() - t0 - waited, 3),
        "api_errors": sum(1 for r in requests if not r["ok"]),
    }


def run_episode(task: Task, config: HarnessConfig, model, runner, budget: CallBudget, *,
                phase: str, repetition: int, episode_id: str, api_retries: int = 0,
                retry_wait_s: float = 10.0, order_position: int | None = None,
                log=None) -> tuple[dict, list[dict]]:
    """Experiment v1 episode: multi-turn retry only after a failed attempt. Returns (record, transcript).

    `api_retries` re-sends a request only after a transient API error (429/5xx/network),
    sleeping with exponential backoff; every re-send still goes through the global budget.
    """
    log = log or (lambda msg: None)
    started_at, t0 = now_iso(), time.perf_counter()
    pacing_before, backoff_waited = budget.waited_s, 0.0
    messages = build_initial_messages(task, config)
    attempts: list[dict] = []
    transcript: list[dict] = []
    last_valid: tuple[int, str] | None = None
    fatal: str | None = None

    for attempt_no in range(1, MAX_ATTEMPTS + 1):
        attempt = new_attempt(attempt_no)
        attempts.append(attempt)
        context = {"task_id": task.task_id, "attempt": attempt_no, "config": config.to_dict()}
        resp, fatal, waited = request_with_retries(model, messages, budget, context, attempt,
                                                   api_retries=api_retries, retry_wait_s=retry_wait_s,
                                                   log=log, transcript=transcript, episode_id=episode_id)
        backoff_waited += waited
        if resp is None:
            attempt["outcome"] = fatal or "api_error"
            break  # no candidate from this attempt; the last valid one (if any) is kept

        _, valid, public = evaluate_reply(resp.text, task, runner, attempt)
        if valid is not None:
            last_valid = (attempt_no, valid)
        if attempt["outcome"] in ("public_pass", "runner_error") or attempt_no == MAX_ATTEMPTS:
            break
        messages = messages + [
            {"role": "assistant", "content": resp.text},
            {"role": "user", "content": build_feedback(attempt, public, config, getattr(runner, "timeout_s", "?"))},
        ]

    # Final scoring on hidden tests, in a separate execution. Never fed back to the model.
    hidden = None
    if last_valid is not None:
        hidden = runner.run(last_valid[1], task, "hidden").to_record()
    hidden_success = bool(hidden and hidden["outcome"] == "passed")
    waited = (budget.waited_s - pacing_before) + backoff_waited
    timeouts = sum(1 for a in attempts if a["outcome"] == "timeout") + int(bool(hidden and hidden["outcome"] == "timeout"))

    record = {
        "experiment": "v1",
        "episode_id": episode_id,
        "phase": phase,
        "task_id": task.task_id,
        "split": task.split,
        "config_id": config.config_id,
        "config": config.to_dict(),
        "repetition": repetition,
        "order_position": order_position,
        "synthetic": bool(getattr(model, "synthetic", False)),
        "buggy_source_hash": sha256_text(task.buggy_source),
        "started_at": started_at,
        "attempts": attempts,
        "first_attempt_public_pass": bool(attempts and attempts[0]["outcome"] == "public_pass"),
        "final_public_pass": bool(last_valid and attempts[last_valid[0] - 1]["outcome"] == "public_pass"),
        "final_candidate_attempt": last_valid[0] if last_valid else None,
        "final_candidate_hash": sha256_text(last_valid[1]) if last_valid else None,
        "hidden": hidden,
        "hidden_success": hidden_success,
        **resource_fields(attempts, waited, t0),
        "timeouts": timeouts,
        # v1 labels by the LAST attempt's outcome (final_attempt=None), as in the original experiment.
        "error_category": classify_episode(fatal, attempts, None, hidden),
        "fatal": fatal,
    }
    return record, transcript
