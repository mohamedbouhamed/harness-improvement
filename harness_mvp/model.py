"""Model backends: a minimal OpenAI-compatible chat-completions adapter and a synthetic mock.

The adapter uses only the standard library so it is easy to swap out. Any object with a
`complete(messages, context) -> ModelResponse` method and a `synthetic` attribute works.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path


class ModelError(Exception):
    category = "api_error"


RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class APIError(ModelError):
    category = "api_error"

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None,
                 retryable: bool = False):
        super().__init__(message)
        self.status, self.retry_after, self.retryable = status, retry_after, retryable


class AuthError(ModelError):
    category = "auth_error"


class BudgetExhausted(Exception):
    category = "budget_exhausted"


@dataclass
class ModelResponse:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    latency_s: float
    finish_reason: str | None = None
    model: str | None = None


class CallBudget:
    """Global request gate: a hard cap on model requests plus an optional pause between them.

    Requests are strictly sequential. Call `reserve()` before every request and `release()` when
    it has finished (success or error). With `min_interval_s`, `reserve()` sleeps until at least
    that many seconds have passed since the previous request *finished*.
    """

    def __init__(self, max_calls: int, min_interval_s: float = 0.0,
                 clock=time.monotonic, sleep=time.sleep, log=None):
        if max_calls < 0:
            raise ValueError("max_calls must be >= 0")
        self.max_calls = max_calls
        self.used = 0
        self.min_interval_s = min_interval_s
        self.waited_s = 0.0
        self._clock, self._sleep, self._log = clock, sleep, log
        self._last_end = None

    def reserve(self) -> None:
        if self.used >= self.max_calls:
            raise BudgetExhausted(f"global model-call budget of {self.max_calls} exhausted")
        if self.min_interval_s and self._last_end is not None:
            delay = self.min_interval_s - (self._clock() - self._last_end)
            if delay > 0:
                if self._log:
                    self._log(f"pausing {delay:.1f}s before next request")
                self._sleep(delay)
                self.waited_s += delay
        self.used += 1

    def release(self) -> None:
        self._last_end = self._clock()

    @property
    def remaining(self) -> int:
        return self.max_calls - self.used


def load_dotenv(path: Path = Path(".env")) -> None:
    """Load KEY=VALUE lines from .env without overriding variables already set."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip().removeprefix("export ").strip(), value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


def sanitize_url(url: str) -> str:
    """Drop credentials, query string and fragment before persisting a URL."""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urllib.parse.urlunsplit((parts.scheme, host, parts.path, "", ""))


@dataclass
class ModelSettings:
    base_url: str
    model: str
    api_key: str = field(repr=False)
    temperature: float | None = 0.0
    max_tokens: int = 1024
    max_tokens_param: str = "max_tokens"
    timeout_s: float = 90.0

    @classmethod
    def from_env(cls, model_override: str | None = None) -> "ModelSettings":
        temp = os.environ.get("HARNESS_TEMPERATURE", "0").strip()
        return cls(
            base_url=os.environ.get("HARNESS_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            model=model_override or os.environ.get("HARNESS_MODEL", ""),
            api_key=os.environ.get("HARNESS_API_KEY") or os.environ.get("OPENAI_API_KEY", ""),
            temperature=None if temp.lower() in ("", "none", "omit") else float(temp),
            max_tokens=int(os.environ.get("HARNESS_MAX_TOKENS", "1024")),
            max_tokens_param=os.environ.get("HARNESS_MAX_TOKENS_PARAM", "max_tokens"),
            timeout_s=float(os.environ.get("HARNESS_REQUEST_TIMEOUT", "90")),
        )

    def problems(self) -> list[str]:
        out = []
        if not self.model:
            out.append("HARNESS_MODEL is not set")
        if not self.api_key or self.api_key.startswith("PASTE_YOUR"):
            out.append("HARNESS_API_KEY (or OPENAI_API_KEY) is not set")
        return out

    def public_dict(self) -> dict:
        return {"backend": "openai-compatible", "base_url": sanitize_url(self.base_url),
                "model": self.model, "temperature": self.temperature,
                "max_tokens": self.max_tokens, "max_tokens_param": self.max_tokens_param,
                "request_timeout_s": self.timeout_s}


class OpenAICompatibleModel:
    """POST {base_url}/chat/completions. No automatic retries."""

    synthetic = False

    def __init__(self, settings: ModelSettings):
        self.settings = settings

    def complete(self, messages: list[dict], context: dict | None = None) -> ModelResponse:
        s = self.settings
        payload: dict = {"model": s.model, "messages": messages, s.max_tokens_param: s.max_tokens}
        if s.temperature is not None:
            payload["temperature"] = s.temperature
        request = urllib.request.Request(
            f"{s.base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {s.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=s.timeout_s) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            detail, retry_after = _error_info(exc)
            # Gemini's OpenAI-compatible endpoint reports a bad key as HTTP 400 "API key not valid".
            if exc.code in (401, 403) or (exc.code == 400 and "api key" in detail.lower()):
                raise AuthError(f"HTTP {exc.code}: {detail}") from None
            raise APIError(f"HTTP {exc.code}: {detail}", exc.code, retry_after,
                           exc.code in RETRYABLE_STATUS) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise APIError(f"request failed: {type(exc).__name__}: {exc}", retryable=True) from None
        latency = time.perf_counter() - start
        try:
            data = json.loads(body)
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise APIError(f"unexpected response shape: {type(exc).__name__}") from None
        usage = data.get("usage") or {}
        inp, out = usage.get("prompt_tokens"), usage.get("completion_tokens")
        total = usage.get("total_tokens")
        if total is None and inp is not None and out is not None:
            total = inp + out
        return ModelResponse(text, inp, out, total, latency, choice.get("finish_reason"), data.get("model"))


_RETRY_HINTS = (re.compile(r'"retryDelay"\s*:\s*"([\d.]+)s"'), re.compile(r"retry in ([\d.]+)\s*s", re.I))


def _error_info(exc: urllib.error.HTTPError) -> tuple[str, float | None]:
    """Best-effort (error message, server-suggested retry delay in seconds)."""
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 - best-effort error text only
        raw = ""
    message = raw
    try:
        data = json.loads(raw)
        if isinstance(data, list) and data:  # some providers wrap the error in a list
            data = data[0]
        msg = data.get("error", {}) if isinstance(data, dict) else {}
        if isinstance(msg, dict) and msg.get("message"):
            message = msg["message"]
    except ValueError:
        pass
    retry_after = None
    header = exc.headers.get("Retry-After") if exc.headers else None
    if header:
        try:
            retry_after = float(header)
        except ValueError:
            pass
    for pattern in _RETRY_HINTS:
        if retry_after is None and (m := pattern.search(raw)):
            retry_after = float(m.group(1))
    return message[:300], retry_after


# ---------------------------------------------------------------------------
# Mock backend. Every response is SYNTHETIC: scripted per task/style/attempt so
# that offline runs exercise retry-then-success, permanent failure, malformed
# output, syntax errors, and public-pass/hidden-fail paths.
# ---------------------------------------------------------------------------

MOCK_SATURATING_ADD_PARTIAL = '''INT32_MIN = -2**31
INT32_MAX = 2**31


def saturating_add(a, b):
    total = a + b
    if total > INT32_MAX:
        return INT32_MAX - 1
    if total < INT32_MIN:
        return INT32_MIN
    return total
'''

MOCK_SYNTAX_ERROR = '''def chunk_string(text, size)
    return [text]
'''

# Used only by runner checks (smoke): must hit the execution timeout.
TIMEOUT_CANDIDATE = '''def merge_intervals(intervals):
    while True:
        pass
'''


def _fence(code: str) -> str:
    return f"```python\n{code}```"


class MockModel:
    synthetic = True

    def __init__(self, tasks):
        self.tasks = {t.task_id: t for t in tasks}

    def trusted_sources(self) -> list[str]:
        """Every candidate this mock can emit (plus runner-check code); all repo-authored."""
        sources = [MOCK_SATURATING_ADD_PARTIAL, TIMEOUT_CANDIDATE]
        for t in self.tasks.values():
            sources += [t.buggy_source, t.reference_source, *t.mock_sources()]
        return sources

    def _script_v2(self, task, context: str, attempt: int) -> str:
        """Experiment v2 scenarios, chosen by the task's `mock_scenario` (maintainer data).

        recovery: buggy, then fixed by any revision.  review_benefit: public-pass/hidden-fail
        partial; a review with full context repairs it, minimal context returns it unchanged.
        regression: correct first; a review with minimal context breaks it.
        malformed_recovery: prose without code, then fixed.  permanent_failure: buggy twice.
        """
        buggy, ref = task.buggy_source, task.reference_source
        scenario = task.mock_scenario or "recovery"
        if scenario == "review_benefit":
            partial = task.mock_variant("partial")
            return _fence(ref if attempt == 2 and context == "full" else partial)
        if scenario == "regression":
            return _fence(task.mock_variant("regressed") if attempt == 2 and context == "minimal" else ref)
        if scenario == "malformed_recovery":
            return "The fix is to compare timestamps strictly." if attempt == 1 else _fence(ref)
        if scenario == "permanent_failure":
            return _fence(buggy)
        return _fence(buggy if attempt == 1 else ref)

    def _script(self, task_id: str, style: str, attempt: int) -> str:
        task = self.tasks[task_id]
        buggy, ref = task.buggy_source, task.reference_source
        if task_id == "merge_intervals":  # fails first, repaired on retry
            return _fence(buggy) if attempt == 1 else _fence(ref)
        if task_id == "stable_dedupe":  # first-attempt success
            return _fence(ref)
        if task_id == "top_k_words":  # direct: permanent failure; structured: repaired
            if style == "structured":
                return _fence(buggy) if attempt == 1 else _fence(ref)
            return ("I would sort by count and then alphabetically." if attempt == 1
                    else _fence(buggy))
        if task_id == "saturating_add":  # direct: public pass, hidden fail (no retry)
            return _fence(ref) if style == "structured" else _fence(MOCK_SATURATING_ADD_PARTIAL)
        if task_id == "chunk_string":  # syntax error, then unfenced correct module
            return _fence(MOCK_SYNTAX_ERROR) if attempt == 1 else ref
        return _fence(ref)

    def complete(self, messages: list[dict], context: dict | None = None) -> ModelResponse:
        ctx = context or {}
        if ctx.get("experiment") == "v2":
            text = self._script_v2(self.tasks[ctx["task_id"]], ctx["config"]["repair_context"], ctx["attempt"])
        else:
            text = self._script(ctx["task_id"], ctx["config"]["instruction_style"], ctx["attempt"])
        # Synthetic token counts: ~4 characters per token. Not real usage.
        inp = sum(len(m["content"]) for m in messages) // 4 + 1
        out = len(text) // 4 + 1
        return ModelResponse(text, inp, out, inp + out, 0.0, "stop", "mock")
