"""Experiment v2: revision policy x repair context.

Attempt 1 is identical for every configuration. The configuration only decides whether a second
attempt is requested (revision_policy) and what the second request contains (repair_context).
The second request is a fresh, single-turn request (not a continuation of the conversation), so
the two repair contexts differ exactly by the blocks listed in REVISION_TEMPLATES.

Hidden tests run only after the last model call of the episode; their results never reach a
prompt, the revision decision or the choice of the final candidate.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from . import agent
from .agent import (RETURN_INSTRUCTION, _cap_middle, classify_episode, evaluate_reply, new_attempt,
                    now_iso, request_with_retries, resource_fields)
from .model import CallBudget
from .runner import ExecResult
from .tasks import Task, sha256_text

EXPERIMENT = "v2"
REVISION_POLICIES = ("on_failure", "always_review")
REPAIR_CONTEXTS = ("minimal", "full")
FEEDBACK_CAP = 3000          # characters of public-test output shown in a revision prompt
HISTORY_MAX_ENTRIES = 3
HISTORY_ENTRY_CAP = 300


@dataclass(frozen=True)
class RevisionConfig:
    revision_policy: str
    repair_context: str

    def __post_init__(self):
        if self.revision_policy not in REVISION_POLICIES:
            raise ValueError(f"bad revision_policy {self.revision_policy!r}")
        if self.repair_context not in REPAIR_CONTEXTS:
            raise ValueError(f"bad repair_context {self.repair_context!r}")

    @property
    def config_id(self) -> str:
        return f"{self.revision_policy}+{self.repair_context}"

    def to_dict(self) -> dict:
        return {"config_id": self.config_id, **asdict(self)}

    @classmethod
    def from_id(cls, config_id: str) -> "RevisionConfig":
        policy, context = config_id.split("+")
        return cls(policy, context)


BASELINE_V2 = RevisionConfig("on_failure", "minimal")


def space_v2() -> list[RevisionConfig]:
    """All four configurations, baseline first, then by config id."""
    others = sorted((RevisionConfig(p, c) for p in REVISION_POLICIES for c in REPAIR_CONTEXTS
                     if (p, c) != ("on_failure", "minimal")), key=lambda c: c.config_id)
    return [BASELINE_V2] + others


# ---------------------------------------------------------------------------
# Prompt templates (recorded verbatim in every v2 run manifest)
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = agent.SYSTEM_PROMPTS["direct"]

FIX_INSTRUCTION = ("Revise the current candidate: it does not pass the public tests. Fix the bug(s) "
                   "while keeping the function interface unchanged.")
REVIEW_INSTRUCTION = ("The current candidate passes the public tests, but they do not cover every case. "
                      "Review it carefully against the specification and edge cases (empty inputs, "
                      "boundaries, ties, ordering, error handling). Fix any remaining bug; if it is "
                      "already correct, return it unchanged. Keep the function interface unchanged.")

REVISION_TEMPLATES = {
    # minimal: interface + current candidate + latest public-test result + instruction.
    "minimal": (
        "{instruction}\n\n"
        "Function interface (keep it unchanged): `{signature}`\n\n"
        "## Current candidate\n```python\n{candidate}```\n\n"
        "## Latest public-test result\n{feedback}\n\n"
        "{return_instruction}"
    ),
    # full: everything in minimal + the original task (contract, buggy module, public tests)
    # + a bounded structured history of previous attempts.
    "full": (
        "{instruction}\n\n"
        "## Contract\nFunction (keep the interface unchanged): `{signature}`\n{statement}\n\n"
        "## Original module (buggy)\n```python\n{buggy}```\n\n"
        "## Public tests (your module is saved as candidate.py)\n```python\n{public_tests}```\n\n"
        "## Previous attempts\n{history}\n\n"
        "## Current candidate\n```python\n{candidate}```\n\n"
        "## Latest public-test result\n{feedback}\n\n"
        "{return_instruction}"
    ),
}


def prompt_templates() -> dict:
    return {"system": SYSTEM_PROMPT, "initial_user": agent.INITIAL_USER_TEMPLATE,
            "return_instruction": RETURN_INSTRUCTION, "fix_instruction": FIX_INSTRUCTION,
            "review_instruction": REVIEW_INSTRUCTION, "revision": REVISION_TEMPLATES,
            "feedback_cap_chars": FEEDBACK_CAP, "history_max_entries": HISTORY_MAX_ENTRIES,
            "history_entry_cap_chars": HISTORY_ENTRY_CAP}


def public_feedback(attempt: dict, public: ExecResult | None, timeout_s) -> str:
    """Latest public-test result. Built ONLY from the attempt record and the public run."""
    outcome = attempt["outcome"]
    if outcome == "public_pass":
        return f"All {public.tests_run} public tests passed."
    if outcome == "no_code":
        return ("The previous reply contained no Python code block, so the original module is shown "
                "as the current candidate.")
    if outcome == "syntax_error":
        return f"The current candidate has a syntax error ({attempt['syntax_error']})."
    if outcome == "timeout":
        return f"The public tests timed out (limit {timeout_s}s). Check for infinite loops."
    raw = (public.stdout + ("\n" if public.stdout else "") + public.stderr).strip()
    return "Public tests failed:\n```\n" + _cap_middle(raw, FEEDBACK_CAP) + "\n```"


def history_block(attempts: list[dict]) -> str:
    lines = []
    for a in attempts[-HISTORY_MAX_ENTRIES:]:
        public = a.get("public") or {}
        parts = [f"Attempt {a['attempt']}: reply={a['parse_status']}", f"result={a['outcome']}"]
        if a.get("syntax_error"):
            parts.append(f"syntax error: {a['syntax_error']}")
        if public.get("failed_tests"):
            parts.append("failed public tests: " + ", ".join(t.rsplit(".", 1)[-1] for t in public["failed_tests"][:5]))
        if a.get("candidate_hash"):
            parts.append(f"candidate sha256 {a['candidate_hash'][:12]}")
        lines.append("- " + "; ".join(parts)[:HISTORY_ENTRY_CAP])
    return "\n".join(lines) or "- (none)"


def build_revision_messages(task: Task, config: RevisionConfig, trigger: str, candidate: str,
                            feedback: str, history: list[dict]) -> list[dict]:
    fields = {"instruction": REVIEW_INSTRUCTION if trigger == "review" else FIX_INSTRUCTION,
              "signature": task.signature, "candidate": candidate, "feedback": feedback,
              "return_instruction": RETURN_INSTRUCTION}
    if config.repair_context == "full":
        fields.update(statement=task.statement, buggy=task.buggy_source,
                      public_tests=task.public_tests_source, history=history_block(history))
    user = REVISION_TEMPLATES[config.repair_context].format(**fields)
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def revision_trigger(config: RevisionConfig, outcome: str) -> str | None:
    """Decide whether to request a second attempt. Uses only the public outcome of attempt 1."""
    if outcome in ("api_error", "auth_error", "budget_exhausted", "runner_error"):
        return None  # no response or infrastructure failure: nothing to revise
    if outcome != "public_pass":
        return "fix"
    return "review" if config.revision_policy == "always_review" else None


def run_revision_episode(task: Task, config: RevisionConfig, model, runner, budget: CallBudget, *,
                         phase: str, repetition: int, episode_id: str, api_retries: int = 0,
                         retry_wait_s: float = 10.0, order_position: int | None = None,
                         log=None) -> tuple[dict, list[dict], dict[int, str]]:
    """Run one v2 episode. Returns (record, transcript, {attempt number: extracted code})."""
    log = log or (lambda msg: None)
    started_at, t0 = now_iso(), time.perf_counter()
    pacing_before, backoff_waited = budget.waited_s, 0.0
    attempts: list[dict] = []
    transcript: list[dict] = []
    candidates: dict[int, str] = {}
    valid: dict[int, str] = {}
    fatal = trigger = None

    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": agent.initial_user_message(task)}]
    for attempt_no in (1, 2):
        attempt = new_attempt(attempt_no)
        attempts.append(attempt)
        context = {"experiment": EXPERIMENT, "task_id": task.task_id, "attempt": attempt_no,
                   "config": config.to_dict(), "trigger": trigger}
        resp, fatal, waited = request_with_retries(model, messages, budget, context, attempt,
                                                   api_retries=api_retries, retry_wait_s=retry_wait_s,
                                                   log=log, transcript=transcript, episode_id=episode_id)
        backoff_waited += waited
        if resp is None:
            attempt["outcome"] = fatal or "api_error"
            break
        code, ok_code, public = evaluate_reply(resp.text, task, runner, attempt)
        if code is not None:
            candidates[attempt_no] = code
        if ok_code is not None:
            valid[attempt_no] = ok_code
        if attempt_no == 2:
            break
        trigger = revision_trigger(config, attempt["outcome"])
        if trigger is None:
            break
        current = code if code is not None else task.buggy_source
        feedback = public_feedback(attempt, public, getattr(runner, "timeout_s", "?"))
        messages = build_revision_messages(task, config, trigger, current, feedback, attempts)

    # ---- Episode over: no more model calls. Hidden tests are scored only from here on. ----
    final_attempt = max(valid) if valid else None  # latest syntactically valid candidate, even if it regressed
    first_hidden = runner.run(valid[1], task, "hidden").to_record() if 1 in valid else None
    if final_attempt is None:
        final_hidden = None
    elif final_attempt == 1 or sha256_text(valid[final_attempt]) == sha256_text(valid.get(1, "")):
        final_hidden = first_hidden  # unchanged candidate: reuse the score
    else:
        final_hidden = runner.run(valid[final_attempt], task, "hidden").to_record()

    revised = len(attempts) == 2 and attempts[1]["outcome"] not in ("api_error", "auth_error", "budget_exhausted")
    waited = (budget.waited_s - pacing_before) + backoff_waited
    hidden_runs = [h for h in (first_hidden, final_hidden if final_hidden is not first_hidden else None) if h]
    record = {
        "experiment": EXPERIMENT,
        "episode_id": episode_id,
        "phase": phase,
        "task_id": task.task_id,
        "split": task.split,
        "family": task.family,
        "config_id": config.config_id,
        "config": config.to_dict(),
        "repetition": repetition,
        "order_position": order_position,
        "synthetic": bool(getattr(model, "synthetic", False)),
        "buggy_source_hash": sha256_text(task.buggy_source),
        "started_at": started_at,
        "attempts": attempts,
        "first_attempt_public_pass": attempts[0]["outcome"] == "public_pass",
        "first_candidate_hash": sha256_text(valid[1]) if 1 in valid else None,
        "first_hidden": first_hidden,
        "first_hidden_success": bool(first_hidden and first_hidden["outcome"] == "passed"),
        "revision_triggered": trigger is not None,
        "revision_reason": trigger,
        "revised": revised,
        "candidate_changed": bool(final_attempt == 2 and sha256_text(valid[2]) != sha256_text(valid.get(1, ""))),
        "final_candidate_attempt": final_attempt,
        "final_candidate_hash": sha256_text(valid[final_attempt]) if final_attempt else None,
        "final_public_pass": bool(final_attempt and attempts[final_attempt - 1]["outcome"] == "public_pass"),
        "hidden": final_hidden,
        "hidden_success": bool(final_hidden and final_hidden["outcome"] == "passed"),
        **resource_fields(attempts, waited, t0),
        "timeouts": sum(1 for a in attempts if a["outcome"] == "timeout")
                    + sum(1 for h in hidden_runs if h["outcome"] == "timeout"),
        "error_category": classify_episode(fatal, attempts, final_attempt, final_hidden),
        "fatal": fatal,
    }
    return record, transcript, candidates
