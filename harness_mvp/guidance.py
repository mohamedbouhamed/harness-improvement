"""Additional repair guidance per condition: none, a fixed static checklist, or retrieved memories.

Retrieval is deterministic keyword overlap (no embeddings). Query = the new task's title and
statement. score = 2*|Q ∩ trigger| + |Q ∩ scope| + |Q ∩ lesson|; keep memories with
score >= min_score (after lower-casing, stop-word removal and light
suffix stripping), order by (-score, memory_id), take up to max_memories whole lessons that fit
the shared context budget. Retrieval may return nothing.
"""
from __future__ import annotations

import re

from .contracts import RetrievalSettings, estimate_tokens, sha256_text

STATIC_CHECKLIST = """Repair checklist:
1. Restate every rule in the specification, including boundaries, ordering, ties and error cases.
2. Read all writable files: the bug may span several functions or modules.
3. For each comparison and range, check both ends against the specification.
4. For each ordering, check every key, its priority and its direction.
5. Check that no input or shared object is modified unless the specification allows it.
6. Keep public interfaces unchanged.
7. Run the public tests after your last edit; they do not cover every rule."""

MEMORY_HEADER = ("Lessons from earlier repair attempts on other repositories "
                 "(unverified; apply only if relevant):")

STOPWORDS = frozenset("""a an and are as at be been but by can each either else every for from has have if in
into is it its may more most must neither no nor not of on once one only or other otherwise same so such than
that the their them then there these they this those through to too under until up use used uses using was
were what when where whether which while who will with within without would you your also both any""".split())


def stem(w: str) -> str:
    """Light suffix stripping: -ies -> -y, then -ing, -ed, -es, -s (keeping at least 3 letters)."""
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    for suffix in ("ing", "ed", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3 and not w.endswith("ss"):
            return w[: -len(suffix)]
    return w


def tokens(text: str) -> set[str]:
    out = set()
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        if len(w) < 3 or w in STOPWORDS or w.isdigit():
            continue
        out.add(stem(w))
    return out


def score(query: set[str], memory: dict) -> int:
    return (2 * len(query & tokens(memory["trigger"])) + len(query & tokens(memory["scope"]))
            + len(query & tokens(memory["lesson"])))


def format_memory(m: dict) -> str:
    return f"- When: {m['trigger']}\n  Check: {m['lesson']}"


def retrieve(task_view: dict, memories: list[dict], settings: RetrievalSettings) -> dict:
    """Select memories for a task. Uses only the task's public view (title + statement)."""
    query = tokens(task_view["title"] + " " + task_view["statement"])
    scored = sorted(((score(query, m), m["memory_id"], m) for m in memories), key=lambda x: (-x[0], x[1]))
    selected, text, skipped_budget = [], "", []
    for s, mid, m in scored:
        if s < settings.min_score or len(selected) >= settings.max_memories:
            continue
        candidate = "\n".join([MEMORY_HEADER] + [format_memory(x) for x in selected + [m]])
        if estimate_tokens(candidate) > settings.budget_tokens:
            skipped_budget.append(mid)
            continue
        selected.append(m)
        text = candidate
    return {"text": text, "memory_ids": [m["memory_id"] for m in selected],
            "scores": {mid: s for s, mid, _ in scored}, "skipped_for_budget": skipped_budget}


def build_guidance(condition: str, task_view: dict, memories: list[dict] | None,
                   settings: RetrievalSettings, bank_hash: str | None = None) -> dict:
    if condition == "baseline":
        return {"text": "", "memory_ids": [], "scores": {}, "budget_tokens": settings.budget_tokens,
                "source_hash": None}
    if condition == "static_guidance":
        if estimate_tokens(STATIC_CHECKLIST) > settings.budget_tokens:
            raise ValueError("static checklist exceeds the shared context budget")
        return {"text": STATIC_CHECKLIST, "memory_ids": [], "scores": {}, "budget_tokens": settings.budget_tokens,
                "source_hash": static_hash()}
    if condition == "learned_memory":
        r = retrieve(task_view, memories or [], settings)
        return r | {"budget_tokens": settings.budget_tokens, "source_hash": bank_hash}
    raise ValueError(f"unknown condition {condition!r}")


def static_hash() -> str:
    return sha256_text(STATIC_CHECKLIST)
