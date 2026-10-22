"""One automatic harness proposal: mine failure patterns from TRAIN traces, ask the model for one edit
to an editable surface, validate it, and check that nothing else changed.

Editable surfaces: harness/instructions.md and harness/memory.md only. The proposer sees, for the
train pairs only: each episode's outcome, the files the agent read, its findings, and the answer
key's class/file/function for that train pair. It never sees evaluation tasks, witness tests, or
any held-out trace.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from harness_mvp import llm
from harness_mvp.contracts import _names_identifier
from harness_mvp.model import CallBudget

from .agent import HARNESS_DIR
from .tasks import SecTask

REPO_ROOT = HARNESS_DIR.parent
EDITABLE = ("harness/instructions.md", "harness/memory.md")
MAX_MEMORY_CHARS = 1500
MAX_INSTRUCTION_CHARS = 2500


def mine_patterns(records: list[dict], tasks: dict[str, SecTask]) -> list[dict]:
    """Deterministic per-episode summary of what went wrong (train episodes only)."""
    out = []
    for r in sorted(records, key=lambda r: r["episode_id"]):
        s, t = r["score"], tasks[r["task_id"]]
        if r["variant"] == "vulnerable":
            outcome = "found the planted weakness" if s["detected"] else (
                "did not submit" if not s["submitted"] else "missed the planted weakness")
        else:
            outcome = "no finding on the fixed version (correct)" if s["clean"] else (
                "did not submit" if not s["submitted"] else f"{s['alarms']} false alarm(s) on the fixed version")
        item = {"program": t.family, "version": r["variant"], "guidance": r["condition"], "outcome": outcome,
                "files_read": r["files_read"], "model_calls": r["model_calls"],
                "findings": [{k: f.get(k) for k in ("file", "function", "cwe", "reason")} for f in (r["findings"] or [])]}
        if r["variant"] == "vulnerable":
            a = t.answer
            item["planted_weakness"] = {"class": a["class"], "file": a["file"], "functions": a["functions"],
                                        "explanation": a["summary"]}
        out.append(item)
    return out


PROPOSER_PROMPT = """You improve the harness of a security-review agent. The agent reads a small Python repository with read-only tools and submits findings; it is scored on finding the planted weakness in vulnerable versions and on reporting nothing for fixed versions.

The harness has two editable text files:
- harness/instructions.md: the reviewer instructions (current text below);
- harness/memory.md: extra guidance shown in the first message (current text below; may be empty).

Below are the agent's results on some TRAINING programs, including the planted weakness for each vulnerable version. The edit will be evaluated on DIFFERENT programs with different code and possibly different weakness classes, so a useful edit must be general: name no file, function, variable or program from these examples, and do not quote their code.

Propose exactly ONE small edit tied to ONE failure pattern you see. Reply with one JSON object only:
{{"pattern": "<the failure pattern, one sentence>", "target": "harness/memory.md" or "harness/instructions.md", "new_content": "<the complete new text of that file>", "rationale": "<why this edit addresses the pattern without causing false alarms, one or two sentences>"}}
Limits: memory.md at most {mem} characters; instructions.md at most {ins} characters.

## Current harness/instructions.md
{instructions}

## Current harness/memory.md
{memory}

## Training results
{results}"""


def build_prompt(patterns: list[dict], instructions: str, memory: str) -> str:
    return PROPOSER_PROMPT.format(mem=MAX_MEMORY_CHARS, ins=MAX_INSTRUCTION_CHARS, instructions=instructions,
                                  memory=memory or "(empty)", results=json.dumps(patterns, indent=1))


def parse_proposal(text: str) -> tuple[dict | None, str | None]:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None, "no JSON object"
    try:
        p = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"
    missing = [k for k in ("pattern", "target", "new_content", "rationale") if not isinstance(p.get(k), str) or not p[k].strip()]
    if missing:
        return None, f"missing fields {missing}"
    return p, None


def validate_proposal(p: dict, train_tasks: list[SecTask]) -> list[str]:
    """Problems that make the proposal inadmissible (it is then logged as rejected, never applied)."""
    problems = []
    if p["target"] not in EDITABLE:
        problems.append(f"target {p['target']!r} is not an editable surface")
    limit = MAX_MEMORY_CHARS if p["target"].endswith("memory.md") else MAX_INSTRUCTION_CHARS
    if len(p["new_content"]) > limit:
        problems.append(f"new content too long ({len(p['new_content'])} > {limit})")
    terms = set()
    for t in train_tasks:
        terms |= {t.family}
        for rel, src in t.variant_files("vulnerable").items():
            terms.add(Path(rel).stem)
            terms |= set(re.findall(r"def (\w+)\(", src)) | set(re.findall(r"class (\w+)", src))
    terms = {x for x in terms if len(x) >= 4 and not x.startswith("__") and x not in ("README", "init", "main")}
    named = sorted(x for x in terms if _names_identifier(p["new_content"], x))
    if named:
        problems.append(f"names training identifiers: {named}")
    if "```" in p["new_content"]:
        problems.append("contains code")
    return problems


def changed_files() -> list[str]:
    """Tracked files that differ from HEAD, plus untracked files (the read-only diff check)."""
    diff = subprocess.run(["git", "diff", "--name-only", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True).stdout
    new = subprocess.run(["git", "ls-files", "--others", "--exclude-standard"], cwd=REPO_ROOT, capture_output=True, text=True).stdout
    return sorted({l for l in (diff + new).splitlines() if l.strip()})


def propose_once(model, budget: CallBudget, patterns: list[dict], *, api_retries: int = 2, log=print) -> dict:
    instructions = (HARNESS_DIR / "instructions.md").read_text(encoding="utf-8").strip()
    memory = (HARNESS_DIR / "memory.md").read_text(encoding="utf-8").strip()
    prompt = build_prompt(patterns, instructions, memory)
    messages = [{"role": "user", "content": prompt}]
    resp, requests, fatal, _ = llm.request(model, messages, budget, {"label": "proposer"}, api_retries=api_retries,
                                           retry_wait_s=10.0, log=log, label="proposer")
    rec = {"prompt": prompt, "requests": requests, "fatal": fatal, "response": resp.text if resp else None,
           "returned_model": getattr(resp, "model", None) if resp else None}
    if resp is None:
        return rec | {"proposal": None, "error": fatal or "api_error"}
    p, err = parse_proposal(resp.text)
    return rec | {"proposal": p, "error": err}


def apply_proposal(p: dict) -> list[str]:
    """Write the edit, then verify that only editable surfaces differ from HEAD. Reverts on violation."""
    before = changed_files()
    if before:
        raise RuntimeError(f"working tree not clean before applying: {before}")
    path = REPO_ROOT / p["target"]
    old = path.read_text(encoding="utf-8")
    path.write_text(p["new_content"].strip() + "\n", encoding="utf-8")
    after = changed_files()
    outside = [f for f in after if f not in EDITABLE]
    if outside:
        path.write_text(old, encoding="utf-8")
        raise RuntimeError(f"edit touched read-only files: {outside}")
    return after
