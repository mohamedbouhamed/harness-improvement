"""Harness search space, random-search sampling and winner selection (black-box: config -> outcomes)."""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass

INSTRUCTION_STYLES = ("direct", "structured")
RETRY_FEEDBACK = ("full", "compact")
# Episodes that ended because of the API or the runner, not because of the agent's code.
INFRA_CATEGORIES = ("api_error", "runner_error")


@dataclass(frozen=True)
class HarnessConfig:
    instruction_style: str
    retry_feedback: str

    def __post_init__(self):
        if self.instruction_style not in INSTRUCTION_STYLES:
            raise ValueError(f"bad instruction_style {self.instruction_style!r}")
        if self.retry_feedback not in RETRY_FEEDBACK:
            raise ValueError(f"bad retry_feedback {self.retry_feedback!r}")

    @property
    def config_id(self) -> str:
        return f"{self.instruction_style}+{self.retry_feedback}"

    def to_dict(self) -> dict:
        return {"config_id": self.config_id, **asdict(self)}

    @classmethod
    def from_id(cls, config_id: str) -> "HarnessConfig":
        style, feedback = config_id.split("+")
        return cls(style, feedback)


BASELINE = HarnessConfig("direct", "full")


def search_space() -> list[HarnessConfig]:
    space = [HarnessConfig(s, f) for s in INSTRUCTION_STYLES for f in RETRY_FEEDBACK]
    return sorted(space, key=lambda c: c.config_id)


def sample_alternatives(seed: int, n: int = 2, baseline: HarnessConfig = BASELINE) -> list[HarnessConfig]:
    """Sample n distinct non-baseline configs. Same seed -> same list."""
    others = [c for c in search_space() if c != baseline]
    if not 0 <= n <= len(others):
        raise ValueError(f"n must be in [0, {len(others)}]")
    return random.Random(seed).sample(others, n)


def summarize_candidate(episodes: list[dict]) -> dict:
    tokens = [e.get("total_tokens") for e in episodes]
    return {
        "episodes": len(episodes),
        "solved": sum(1 for e in episodes if e.get("hidden_success")),
        "first_attempt_public_pass": sum(1 for e in episodes if e.get("first_attempt_public_pass")),
        "llm_calls": sum(e.get("llm_calls", 0) for e in episodes),
        "infra_errors": sum(1 for e in episodes if e.get("error_category") in INFRA_CATEGORIES),
        # Never substitute zero for missing usage.
        "total_tokens": None if (not tokens or any(t is None for t in tokens)) else sum(tokens),
    }


def select_winner(candidates: list, optimization_episodes: list[dict], baseline=BASELINE) -> dict:
    """Rank by (most final hidden-test successes, fewest tokens if known for all, config id).

    Works for any config family with `config_id`/`to_dict` (v1 HarnessConfig, v2 RevisionConfig).
    Optimization data only: any other episode is rejected.
    """
    for e in optimization_episodes:
        if e.get("phase") != "optimization" or e.get("split") != "optimization":
            raise ValueError(f"select_winner received non-optimization episode {e.get('episode_id')}")
    stats = {}
    for c in candidates:
        eps = [e for e in optimization_episodes if e["config_id"] == c.config_id]
        stats[c.config_id] = summarize_candidate(eps)
    use_tokens = all(s["total_tokens"] is not None for s in stats.values())

    def key(cid: str):
        s = stats[cid]
        return (-s["solved"], s["total_tokens"] if use_tokens else 0, cid)

    ranking = sorted(stats, key=key)
    winner = ranking[0]
    decided_by = "solved"
    if len(ranking) > 1:
        a, b = stats[ranking[0]], stats[ranking[1]]
        if a["solved"] == b["solved"]:
            decided_by = "total_tokens" if use_tokens and a["total_tokens"] != b["total_tokens"] else "config_id"
    infra = [e["episode_id"] for e in optimization_episodes if e.get("error_category") in INFRA_CATEGORIES]
    return {
        "selected_config_id": winner,
        "selected_config": next(c for c in candidates if c.config_id == winner).to_dict(),
        "baseline_config_id": baseline.config_id,
        "selected_is_baseline": winner == baseline.config_id,
        "decided_by": decided_by,
        "token_tiebreak_available": use_tokens,
        "token_tiebreak_note": None if use_tokens else
            "token usage missing for at least one candidate; token tie-breaker skipped for all",
        # Infra failures count as unsolved, so they can decide the ranking: flag them.
        "reliable": not infra,
        "infra_error_episodes": infra,
        "ranking": [{"config_id": cid, **stats[cid]} for cid in ranking],
        "rule": "1) most optimization episodes solved (final hidden tests); 2) lowest total tokens "
                "if known for every candidate; 3) config_id ascending",
    }
