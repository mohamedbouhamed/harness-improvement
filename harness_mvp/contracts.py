"""Data contracts for the memory-guided repair experiment (protocol `memory-v1`, see docs/protocol.md).

Plain dataclasses and validators; every artifact written to disk is checked against these.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass

PROTOCOL_VERSION = "memory-v1"
SPLITS = ("calibration", "memory_source", "development", "test")
CONDITIONS = ("baseline", "static_guidance", "learned_memory")
GUIDANCE_HEADING = "## Additional repair guidance"


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_hash(obj) -> str:
    """SHA-256 of canonical JSON (sorted keys, no whitespace variation)."""
    return sha256_text(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def estimate_tokens(text: str) -> int:
    """Conservative prompt-budget estimate: ceil(chars / 3). Never mixed with API-reported usage."""
    return math.ceil(len(text) / 3)


@dataclass(frozen=True)
class AgentLimits:
    max_model_calls: int = 8
    max_tool_calls: int = 24
    max_tool_calls_per_turn: int = 4
    max_test_runs: int = 4
    tool_result_chars: int = 4000
    read_chars: int = 8000
    write_chars: int = 20000
    search_max_matches: int = 30
    exec_timeout_s: int = 10

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalSettings:
    max_memories: int = 3
    min_score: int = 3
    budget_tokens: int = 300

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Task manifest entries
# ---------------------------------------------------------------------------

TASK_REQUIRED = {"id": str, "split": str, "family": str, "title": str, "statement": str,
                 "allowed_files": list, "public_tests": str, "principles": list}
_SAFE_REL = re.compile(r"^(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9_./-]+$")


class ContractError(ValueError):
    pass


def validate_task_entry(entry: dict) -> None:
    for key, typ in TASK_REQUIRED.items():
        if not isinstance(entry.get(key), typ):
            raise ContractError(f"task {entry.get('id')!r}: field {key!r} missing or not {typ.__name__}")
    if entry["split"] not in SPLITS:
        raise ContractError(f"task {entry['id']}: invalid split {entry['split']!r}")
    paths = entry["allowed_files"] + [entry["public_tests"]]
    for p in paths:
        if not isinstance(p, str) or not _SAFE_REL.match(p):
            raise ContractError(f"task {entry['id']}: unsafe relative path {p!r}")
    if entry["public_tests"] in entry["allowed_files"]:
        raise ContractError(f"task {entry['id']}: public tests must not be writable")
    if not entry["allowed_files"]:
        raise ContractError(f"task {entry['id']}: no allowed_files")


def check_split_families(entries: list[dict]) -> None:
    """A family (near-duplicates / shared source) must live in exactly one split."""
    seen: dict[str, set] = {}
    for e in entries:
        seen.setdefault(e["family"], set()).add(e["split"])
    crossing = sorted(f for f, s in seen.items() if len(s) > 1)
    if crossing:
        raise ContractError(f"task families cross splits: {crossing}")
    ids = [e["id"] for e in entries]
    if len(ids) != len(set(ids)):
        raise ContractError("duplicate task ids")


# ---------------------------------------------------------------------------
# Lessons and memory records
# ---------------------------------------------------------------------------

LESSON_LIMITS = {"trigger": (10, 200), "lesson": (20, 400), "scope": (3, 150)}


def validate_lesson(obj, task_terms: set[str] = frozenset(), source_lines: set[str] = frozenset()):
    """Validate one extractor lesson.

    Returns (errors, flags). Errors mean the lesson is malformed and rejected. Flags mark
    suspicious task-specific content (excluded from the bank by default).
    """
    errors, flags = [], []
    if not isinstance(obj, dict):
        return ["not an object"], flags
    for key, (lo, hi) in LESSON_LIMITS.items():
        val = obj.get(key)
        if not isinstance(val, str) or not val.strip():
            errors.append(f"{key}: missing or not a string")
        elif not lo <= len(val.strip()) <= hi:
            errors.append(f"{key}: length {len(val.strip())} outside [{lo}, {hi}]")
    extra = set(obj) - set(LESSON_LIMITS) - {"memory_id", "evidence_episode_ids"}
    if extra:
        errors.append(f"unexpected fields: {sorted(extra)}")
    if errors:
        return errors, flags
    text = " ".join(obj[k] for k in LESSON_LIMITS)
    if "```" in text or re.search(r"^\s*(def|class|import|return)\s", text, re.MULTILINE):
        errors.append("contains code")
    words = {w.lower() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", text)}
    named = sorted(t for t in task_terms if t.lower() in words)
    if named:
        flags.append(f"names task-specific identifiers: {named}")
    copied = [l for l in source_lines if len(l) >= 20 and l in text]
    if copied:
        flags.append("copies source lines")
    return errors, flags


def memory_id_for(trigger: str, lesson: str) -> str:
    return "mem-" + sha256_text(normalize_text(trigger) + "|" + normalize_text(lesson))[:10]


def normalize_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", text.lower()))


MEMORY_REQUIRED = ("memory_id", "trigger", "lesson", "scope", "evidence_episode_ids", "provenance")
PROVENANCE_REQUIRED = ("source_split", "source_task_ids", "extractor", "created_at", "source_hashes", "synthetic")


def validate_memory_record(m: dict) -> None:
    for key in MEMORY_REQUIRED:
        if key not in m:
            raise ContractError(f"memory {m.get('memory_id')!r}: missing {key}")
    for key in PROVENANCE_REQUIRED:
        if key not in m["provenance"]:
            raise ContractError(f"memory {m['memory_id']}: provenance missing {key}")
    if m["provenance"]["source_split"] != "memory_source":
        raise ContractError(f"memory {m['memory_id']}: source split must be memory_source, "
                            f"got {m['provenance']['source_split']!r}")
    if not m["evidence_episode_ids"]:
        raise ContractError(f"memory {m['memory_id']}: no evidence episodes")
