"""Metered model access: billed-usage cost ledger, a hard euro cap, and model-id recording.

`MeteredModel` is an OpenAI-compatible client (same request shape as harness_mvp.model) that adds:
- an optional `reasoning_effort` field, so the thinking level is fixed and recorded;
- the requested model id, the returned model id and the raw `usage` object on every response;
- a cost reservation before each request: the worst case (conservative input estimate + the full
  output allowance) must fit under the cap, otherwise `BudgetExhausted` is raised and nothing is sent;
- settlement from billed usage afterwards. Output is billed as max(completion, total - prompt), so
  thinking tokens are charged even if the endpoint leaves them out of `completion_tokens`.

The ledger is one JSONL file shared by every run, so the cap applies to total paid generation,
not per run. Prices live in secagent/prices.json and must be confirmed by the user.
"""
from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from harness_mvp.model import (RETRYABLE_STATUS, APIError, AuthError, BudgetExhausted, ModelResponse,
                               ModelSettings, _error_info, native_calls_as_actions)

PRICES_FILE = Path(__file__).resolve().parent / "prices.json"
CHARS_PER_TOKEN_LOWER_BOUND = 2.0  # conservative: real text is ~3-4 chars/token, so this over-estimates


@dataclass(frozen=True)
class Price:
    model: str
    input_usd_per_m: float
    output_usd_per_m: float
    source: str
    retrieved: str
    confirmed_by_user: bool

    def usd(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.input_usd_per_m + output_tokens * self.output_usd_per_m) / 1e6


def load_price(model: str, path: Path = PRICES_FILE) -> Price:
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = data["models"].get(model)
    if entry is None:
        raise KeyError(f"no price for {model!r} in {path}")
    return Price(model, entry["input_usd_per_m"], entry["output_usd_per_m"], data["source"], data["retrieved"],
                 bool(data.get("confirmed_by_user")))


class CostLedger:
    """Append-only JSONL of every paid request. `spent_eur` is the sum of settled costs."""

    def __init__(self, path: Path, cap_eur: float, usd_to_eur: float):
        self.path, self.cap_eur, self.usd_to_eur = Path(path), cap_eur, usd_to_eur
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def entries(self) -> list[dict]:
        if not self.path.is_file():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    @property
    def spent_eur(self) -> float:
        """Cost from provider-reported usage."""
        return sum(e["cost_eur"] for e in self.entries())

    @property
    def bound_eur(self) -> float:
        """Upper bound used for the cap: every successful request billed as if it used the whole output
        allowance (covers thinking tokens the endpoint might not report). Entries without a bound
        (failed or older requests) count with their reserved amount, or their cost if larger."""
        return sum(e.get("upper_bound_eur") if e.get("upper_bound_eur") is not None
                   else max(e["cost_eur"], e.get("reserved_eur", 0.0) if e.get("status") == "ok" else e["cost_eur"])
                   for e in self.entries())

    def reserve(self, worst_eur: float) -> None:
        if self.bound_eur + worst_eur > self.cap_eur:
            raise BudgetExhausted(f"cost cap {self.cap_eur:.2f} EUR (upper-bound basis): bound so far "
                                  f"{self.bound_eur:.4f}, reported {self.spent_eur:.4f}, next request up to {worst_eur:.4f}")

    def append(self, entry: dict) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")


def estimate_input_tokens(payload: dict) -> int:
    return math.ceil(len(json.dumps(payload, ensure_ascii=False)) / CHARS_PER_TOKEN_LOWER_BOUND)


def billable_output(usage: dict) -> int | None:
    prompt, completion, total = usage.get("prompt_tokens"), usage.get("completion_tokens"), usage.get("total_tokens")
    if completion is None and total is None:
        return None
    candidates = [c for c in (completion, (total - prompt) if total is not None and prompt is not None else None)
                  if c is not None]
    return max(candidates)


class MeteredModel:
    synthetic = False

    def __init__(self, settings: ModelSettings, price: Price, ledger: CostLedger, *, run_id: str,
                 reasoning_effort: str | None = None):
        if not price.confirmed_by_user:
            raise RuntimeError(f"prices in {PRICES_FILE.name} are not confirmed by the user; no paid request is sent")
        if price.model != settings.model:
            raise RuntimeError(f"price entry {price.model!r} does not match the requested model {settings.model!r}")
        self.settings, self.price, self.ledger = settings, price, ledger
        self.run_id, self.reasoning_effort = run_id, reasoning_effort

    def describe(self) -> dict:
        return self.settings.public_dict() | {"reasoning_effort": self.reasoning_effort,
                                              "price_usd_per_m": [self.price.input_usd_per_m, self.price.output_usd_per_m],
                                              "price_source": self.price.source, "price_retrieved": self.price.retrieved,
                                              "cap_eur": self.ledger.cap_eur, "usd_to_eur": self.ledger.usd_to_eur}

    def _eur(self, input_tokens: int, output_tokens: int) -> float:
        return self.price.usd(input_tokens, output_tokens) * self.ledger.usd_to_eur

    def complete(self, messages: list[dict], context: dict | None = None) -> ModelResponse:
        s = self.settings
        payload: dict = {"model": s.model, "messages": messages, s.max_tokens_param: s.max_tokens}
        if s.temperature is not None:
            payload["temperature"] = s.temperature
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if context and context.get("tools"):
            payload["tools"] = context["tools"]
        est_in = estimate_input_tokens(payload)
        worst = self._eur(est_in, s.max_tokens)
        self.ledger.reserve(worst)  # raises BudgetExhausted before anything is sent
        entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "run_id": self.run_id,
                 "label": (context or {}).get("label"), "requested_model": s.model, "returned_model": None,
                 "estimated_input_tokens": est_in, "reserved_eur": round(worst, 6), "usage": None,
                 "billed_input_tokens": None, "billed_output_tokens": None, "cost_eur": 0.0, "status": None}
        request = urllib.request.Request(
            f"{s.base_url}/chat/completions", data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {s.api_key}", "Content-Type": "application/json"}, method="POST")
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=s.timeout_s) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            detail, retry_after = _error_info(exc)
            # Rejected requests (4xx) are not billed; 5xx may have been processed, so charge the reservation.
            entry.update(status=f"http_{exc.code}", cost_eur=round(worst, 6) if exc.code >= 500 else 0.0)
            self.ledger.append(entry)
            if exc.code in (401, 403) or (exc.code == 400 and "api key" in detail.lower()):
                raise AuthError(f"HTTP {exc.code}: {detail}") from None
            raise APIError(f"HTTP {exc.code}: {detail}", exc.code, retry_after, exc.code in RETRYABLE_STATUS) from None
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            entry.update(status="network_error", cost_eur=round(worst, 6))  # may have been processed
            self.ledger.append(entry)
            raise APIError(f"request failed: {type(exc).__name__}: {exc}", retryable=True) from None
        latency = time.perf_counter() - start
        try:
            data = json.loads(body)
            choice = data["choices"][0]
            text = choice["message"].get("content") or ""
            native = native_calls_as_actions(choice["message"].get("tool_calls"))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            entry.update(status="bad_response", cost_eur=round(worst, 6))
            self.ledger.append(entry)
            raise APIError(f"unexpected response shape: {type(exc).__name__}") from None
        usage = data.get("usage") or {}
        inp, out = usage.get("prompt_tokens"), billable_output(usage)
        cost = self._eur(inp, out) if inp is not None and out is not None else worst
        bound = self._eur(inp if inp is not None else est_in, max(out or 0, s.max_tokens))
        entry.update(status="ok", returned_model=data.get("model"), usage=usage, billed_input_tokens=inp,
                     billed_output_tokens=out, cost_eur=round(cost, 6), upper_bound_eur=round(bound, 6),
                     output_over_allowance=bool(out is not None and out > s.max_tokens))
        self.ledger.append(entry)
        if native is not None:
            text, n_native = native
        else:
            n_native = 0
        r = ModelResponse(text, inp, out, usage.get("total_tokens"), latency, choice.get("finish_reason"),
                          data.get("model"), n_native)
        r.raw_usage, r.requested_model, r.cost_eur = usage, s.model, cost
        if entry["output_over_allowance"]:
            # The reservation assumed output <= max_tokens. Stop rather than risk overrunning the cap.
            raise BudgetExhausted(f"billed output {out} exceeded max_tokens {s.max_tokens}; reservation unsafe")
        return r
