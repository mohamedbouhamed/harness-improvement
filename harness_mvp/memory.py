"""Lesson extraction from memory-source episodes and the frozen memory bank.

Extractor inputs are built from a WHITELIST of episode fields: the task specification, the source
changes, the agent's actions and public-test feedback. Hidden-test contents and outcomes (and
anything derived from them, such as the error category) are never included.
Extracted lessons are hypotheses: public tests are incomplete evidence, so nothing here is "proven".
"""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from . import llm
from .benchmark import RepoTask
from .contracts import (PROTOCOL_VERSION, ContractError, canonical_hash, memory_id_for, normalize_text,
                        sha256_text, validate_lesson, validate_memory_record)

MAX_LESSONS = 3
FEEDBACK_CHARS = 1500
DIFF_CHARS = 6000
DEDUP_JACCARD = 0.8


# ---------------------------------------------------------------------------
# Extractor input (whitelist)
# ---------------------------------------------------------------------------

def unified_diff(before: dict[str, str], after: dict[str, str]) -> str:
    out = []
    for rel in sorted(set(before) | set(after)):
        a, b = before.get(rel, ""), after.get(rel, "")
        if a != b:
            out += difflib.unified_diff(a.splitlines(keepends=True), b.splitlines(keepends=True),
                                        fromfile=f"a/{rel}", tofile=f"b/{rel}")
    return "".join(out)


def extraction_input(task: RepoTask, record: dict, submitted: dict[str, str]) -> dict:
    """Everything the extractor may see about one episode. Hidden information is excluded by construction."""
    if record["split"] != "memory_source":
        raise ContractError(f"extraction only from memory_source episodes, got {record['split']!r}")
    actions = []
    for turn in record["turns"]:
        for call in turn["tool_calls"]:
            actions.append({"turn": turn["turn"], "tool": call["name"], "ok": call["ok"],
                            "path": call["args"].get("path")})
    feedback = []
    for snap in record["snapshots"]:  # public-test runs only
        pub = snap["public"]
        feedback.append({"source": snap["source"], "outcome": pub["outcome"],
                         "failed_tests": pub["failed_tests"],
                         "output": (pub["stdout"] + "\n" + pub["stderr"]).strip()[-FEEDBACK_CHARS:]})
    return {
        "episode_id": record["episode_id"],
        "task": task.public_view(),
        "source_changes": unified_diff(task.original_candidate(), submitted)[:DIFF_CHARS],
        "agent_actions": actions,
        "public_test_feedback": feedback,
        "end_reason": record["end_reason"],
        "submitted_public_pass": record["submitted_public_pass"],
        "public_fallback_activated": record["fallback_activated"],
    }


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------

EXTRACTOR_SYSTEM = ("You turn one repair attempt on a small Python repository into reusable lessons for "
                    "future repairs of DIFFERENT repositories. Reply with JSON only.")
EXTRACTOR_INSTRUCTIONS = """Below is one repair attempt: the task specification, the source changes, the agent's actions and the public-test feedback.

Write at most {max_lessons} lessons as JSON: {{"lessons": [{{"trigger": "...", "lesson": "...", "scope": "..."}}]}}
- trigger (10-200 chars): when the lesson applies, phrased as features of a task specification.
- lesson (20-400 chars): a reusable check or repair strategy. Do not restate this task's solution, do not name its functions, classes, files or modules, and do not include code.
- scope (3-150 chars): the kind of code it applies to.
The public tests are incomplete, so the submitted code may still be wrong; prefer checks over claims. Return {{"lessons": []}} if nothing reusable was learned.

Repair attempt:
{payload}"""


def parse_lessons(text: str) -> tuple[list | None, str | None]:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None, "no JSON object"
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"
    lessons = data.get("lessons") if isinstance(data, dict) else None
    if not isinstance(lessons, list):
        return None, "missing 'lessons' list"
    return lessons[:MAX_LESSONS], None


class LLMExtractor:
    kind = "llm"

    def __init__(self, model, model_info: dict):
        self.model, self.model_info = model, model_info

    def config(self) -> dict:
        return {"kind": self.kind, "model": self.model_info.get("model"), "max_lessons": MAX_LESSONS,
                "prompt_hash": sha256_text(EXTRACTOR_SYSTEM + EXTRACTOR_INSTRUCTIONS)}

    def extract(self, payload: dict, budget, *, api_retries=0, retry_wait_s=10.0, log=None):
        messages = [{"role": "system", "content": EXTRACTOR_SYSTEM},
                    {"role": "user", "content": EXTRACTOR_INSTRUCTIONS.format(
                        max_lessons=MAX_LESSONS, payload=json.dumps(payload, indent=1))}]
        resp, requests, fatal, _ = llm.request(self.model, messages, budget, {"extraction": payload["episode_id"]},
                                              api_retries=api_retries, retry_wait_s=retry_wait_s,
                                              log=log or (lambda m: None), label=f"extract {payload['episode_id']}")
        return (resp.text if resp else None), requests, fatal


BOUNDARY_LESSON = {
    "trigger": "The specification defines inclusive or exclusive ends for ranges, windows or intervals.",
    "lesson": "List every boundary rule stated in the contract and check each comparison at both ends; an "
              "excluded end needs a strict comparison, and the last valid position must still be reachable.",
    "scope": "boundary checks on ranges, windows, intervals and limits"}
ORDERING_LESSON = {
    "trigger": "Output must be sorted by several criteria with tie-breaking rules.",
    "lesson": "Encode every ordering criterion from the contract in the sort key, in priority order and with "
              "its stated direction, and check that each tie-breaking value is computed correctly upstream.",
    "scope": "sorting, ranking and tie-breaking"}
RETEST_LESSON = {
    "trigger": "Several edits are made before submitting a repair.",
    "lesson": "Run the public tests again after the last edit; an untested final change can break a "
              "candidate that previously passed.",
    "scope": "multi-step repairs"}


class MockExtractor:
    """Deterministic SYNTHETIC extractor: pattern rules over the whitelisted input only.

    It also emits one task-specific lesson (to exercise flagging) and one malformed lesson
    (to exercise rejection).
    """
    kind = "mock"

    def config(self) -> dict:
        return {"kind": self.kind, "model": None, "max_lessons": MAX_LESSONS + 2, "rules": "diff-patterns-v1"}

    def extract(self, payload: dict, budget=None, **_):
        changed = [l[1:] for l in payload["source_changes"].splitlines()
                   if l[:1] in "+-" and not l.startswith(("+++", "---"))]
        lessons = []
        if any(re.search(r"<=?|>=?|range\(", l) for l in changed):
            lessons.append(BOUNDARY_LESSON)
        if any(re.search(r"sorted\(|key=|max\(|min\(", l) for l in changed):
            lessons.append(ORDERING_LESSON)
        acts = [a["tool"] for a in payload["agent_actions"]]
        if "write_file" in acts and ("run_public_tests" not in acts
                                     or acts[::-1].index("write_file") < acts[::-1].index("run_public_tests")):
            lessons.append(RETEST_LESSON)
        names = re.findall(r"`[\w.]*?(\w+)\(", payload["task"]["statement"])
        if names:  # deliberately task-specific: must be flagged and kept out of the bank
            lessons.append({"trigger": f"The task asks to repair `{names[0]}()`.",
                            "lesson": f"When repairing `{names[0]}()`, re-check how it handles its inputs.",
                            "scope": "this repository"})
        lessons.append({"trigger": "fix", "lesson": "", "scope": "x"})
        return json.dumps({"lessons": lessons}), [], None


# ---------------------------------------------------------------------------
# Candidate lessons -> validated, deduplicated, frozen bank
# ---------------------------------------------------------------------------

def source_lines(task: RepoTask) -> set[str]:
    return {l.strip() for rel in task.allowed_files for l in task.repo_files()[rel].splitlines() if len(l.strip()) >= 20}


def candidate_lessons(raw: list, task: RepoTask, record: dict, submitted: dict, extractor_cfg: dict,
                      created_at: str, synthetic: bool) -> list[dict]:
    out = []
    for i, lesson in enumerate(raw):
        errors, flags = validate_lesson(lesson, task.identifiers(), source_lines(task))
        status = "rejected" if errors else ("flagged" if flags else "accepted")
        clean = {k: lesson.get(k, "").strip() for k in ("trigger", "lesson", "scope")} if isinstance(lesson, dict) else {}
        out.append({
            "candidate_id": f"{record['episode_id']}#{i}", "status": status, "errors": errors, "flags": flags,
            **clean, "raw": lesson if errors else None,
            "evidence_episode_ids": [record["episode_id"]],
            "provenance": {"source_split": record["split"], "source_task_ids": [task.task_id],
                           "extractor": extractor_cfg, "created_at": created_at, "synthetic": synthetic,
                           "source_hashes": {record["episode_id"]: {
                               "task_repo": task.hashes()["repo"], "task_entry": task.hashes()["entry"],
                               "submitted_candidate": record["submitted_candidate_hash"]}}},
        })
    return out


def _tokens(text: str) -> set[str]:
    return set(normalize_text(text).split())


def build_bank(candidates: list[dict], created_at: str) -> dict:
    """Accepted lessons only; near-duplicates are merged (evidence and provenance united)."""
    kept: list[dict] = []
    for c in candidates:
        if c["status"] != "accepted":
            continue
        if c["provenance"]["source_split"] != "memory_source":
            raise ContractError(f"candidate {c['candidate_id']} does not come from memory_source")
        toks = _tokens(c["trigger"] + " " + c["lesson"])
        dup = next((k for k in kept if normalize_text(k["lesson"]) == normalize_text(c["lesson"])
                    or len(toks & k["_tokens"]) / max(1, len(toks | k["_tokens"])) >= DEDUP_JACCARD), None)
        if dup:
            prov = dup["provenance"]
            dup["evidence_episode_ids"] = sorted(set(dup["evidence_episode_ids"]) | set(c["evidence_episode_ids"]))
            prov["source_task_ids"] = sorted(set(prov["source_task_ids"]) | set(c["provenance"]["source_task_ids"]))
            prov["source_hashes"].update(c["provenance"]["source_hashes"])
            prov["merged_candidates"].append(c["candidate_id"])
            continue
        kept.append({"memory_id": memory_id_for(c["trigger"], c["lesson"]), "trigger": c["trigger"],
                     "lesson": c["lesson"], "scope": c["scope"],
                     "evidence_episode_ids": list(c["evidence_episode_ids"]),
                     "provenance": json.loads(json.dumps(c["provenance"])) | {"merged_candidates": [c["candidate_id"]]},
                     "status": "unverified hypothesis (public tests are incomplete evidence)",
                     "_tokens": toks})
    memories = sorted(({k: v for k, v in m.items() if k != "_tokens"} for m in kept), key=lambda m: m["memory_id"])
    for m in memories:
        validate_memory_record(m)
    counts = {s: sum(1 for c in candidates if c["status"] == s) for s in ("accepted", "flagged", "rejected")}
    return {"protocol_version": PROTOCOL_VERSION, "created_at": created_at, "memories": memories,
            "candidate_counts": counts, "synthetic": any(m["provenance"]["synthetic"] for m in memories),
            "bank_hash": canonical_hash(memories),  # identifies this exact artifact (incl. provenance times)
            "content_hash": canonical_hash([{k: m[k] for k in ("memory_id", "trigger", "lesson", "scope",
                                                                "evidence_episode_ids")} for m in memories])}


def write_bank(path: Path, bank: dict) -> None:
    if path.exists():
        raise FileExistsError(f"{path} exists; a memory bank is written once")
    path.write_text(json.dumps(bank, indent=2) + "\n", encoding="utf-8")


def load_bank(path: Path, expected_hash: str | None = None) -> dict:
    bank = json.loads(path.read_text(encoding="utf-8"))
    for m in bank["memories"]:
        validate_memory_record(m)
    actual = canonical_hash(bank["memories"])
    if actual != bank["bank_hash"]:
        raise ContractError("memory bank content does not match its recorded hash")
    if expected_hash is not None and actual != expected_hash:
        raise ContractError(f"memory bank hash {actual[:12]} != frozen {expected_hash[:12]}")
    return bank
