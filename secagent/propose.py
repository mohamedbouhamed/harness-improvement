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


# ---------------------------------------------------------------------------------------------
# Iteration 2: proposal with feedback from the rejected edit (docs/iteration2-plan.md)
# ---------------------------------------------------------------------------------------------

ITER2_EDITABLE = ("harness/instructions.md", "harness/verification.md")
ITER2_LIMITS = {"harness/instructions.md": 2500, "harness/verification.md": 1200}

FEEDBACK_PROMPT = """You improve the harness of a security-review agent. The agent reads a small Python repository with read-only tools (list_files, read_file, search) and finishes with submit_findings. It is scored on two things per program pair: reporting the planted weakness in the vulnerable version, and reporting nothing for the fixed version (its twin). A pair is solved only if both hold.

## The harness you may change
- harness/instructions.md: the reviewer instructions placed at the start of the system prompt.
- harness/verification.md: an optional verification step, empty in the original harness. If it is non-empty, the agent's first submit_findings becomes a draft: the agent receives this message
---
{verify_template}
---
and its next submit_findings is final. The step shares the same limits as everything else ({calls} replies, {actions} actions per episode); if the limits run out after a draft, the draft counts.
Nothing else can change: not the tools, the limits, the model, the tasks or the scoring.

## Original harness (the starting point for your change)
### harness/instructions.md
{instructions}
### harness/verification.md
(empty)

## A previous attempt, which was rejected
It added this line to instructions.md:
{previous_edit}
Its stated pattern: {previous_pattern}
Training results, original harness vs the previous attempt (one pair per line):
{before_after}

## Training programs: threat model and planted weaknesses
{ground_truth}

## Training episodes (actions, findings with the agent's own stated reasons)
{traces}

## Your task
1. Explain a plausible cause of the previous attempt's regression and of the remaining misses, using the evidence above.
2. Propose ONE targeted change, starting from the original harness: rewrite instructions.md, write a verification step, or both, as one coherent change. A useful change must be general: the evaluation uses different programs, so name no file, function, variable or program from these examples, do not quote their code or the explanations above, and do not add rules that only fit these programs.
3. Predict which behaviour should change and what could get worse.

Reply with one JSON object only:
{{"cause": "...", "change": {{"harness/instructions.md": "<complete new text, or null to keep the original>", "harness/verification.md": "<complete text, or null to keep it empty>"}}, "predicted_improvement": "...", "predicted_risks": "..."}}
Limits: instructions.md at most {ins_limit} characters; verification.md at most {ver_limit} characters."""


def _episode_trace(r: dict, max_reason: int = 700) -> dict:
    s = r["score"]
    if not s["submitted"]:
        outcome = "no submission"
    elif r["variant"] == "vulnerable":
        outcome = "planted weakness reported" if s["detected"] else "planted weakness NOT reported (false negative)"
    else:
        outcome = "nothing reported (correct)" if not s["n_findings"] else f"{s['n_findings']} finding(s) on the fixed version (false positive)"
    actions = [a["name"] + (f" {a['args'].get('path') or a['args'].get('query')}" if a.get("args") else "")
               for t in r["turns"] for a in t["actions"] if a["name"] != "submit_findings"]
    return {"program": r["family"], "version": r["variant"], "harness": r["condition"], "outcome": outcome,
            "actions": actions, "model_calls": r["model_calls"],
            "findings": [{k: (str(f.get(k))[:max_reason] if k == "reason" else f.get(k)) for k in ("file", "function", "cwe", "reason")}
                         for f in (r["findings"] or [])]}


def build_feedback_prompt(baseline: list[dict], candidate: list[dict], tasks: dict, previous: dict,
                          instructions: str, limits) -> str:
    from .agent import VERIFY_TEMPLATE

    def pair_line(recs, fam):
        v = next((r for r in recs if r["family"] == fam and r["variant"] == "vulnerable"), None)
        p = next((r for r in recs if r["family"] == fam and r["variant"] == "patched"), None)
        det = bool(v and v["score"]["detected"])
        fp = p["score"]["n_findings"] if p and p["score"]["submitted"] else "no submission"
        return f"detected={det}, twin findings={fp}, solved={bool(det and p and p['score']['clean'])}"

    fams = sorted({tasks[r["task_id"]].family for r in baseline})
    before_after = "\n".join(f"- {f}: original: {pair_line(baseline, f)} | previous attempt: {pair_line(candidate, f)}" for f in fams)
    gt = []
    for tid in sorted({r["task_id"] for r in baseline}):
        t, a = tasks[tid], tasks[tid].answer
        readme = t.variant_files("vulnerable")["README.md"].strip()
        gt.append(f"### {t.family}\nREADME (threat model):\n{readme}\nPlanted weakness ({a['class']}, {a['cwe']}): "
                  f"{a['file']} in {', '.join(a['functions'])}; path {' -> '.join(a['path'])}. {a['summary']}\n"
                  f"The fixed version changes only the code needed to remove it.")
    traces = [_episode_trace(r) for r in sorted(baseline + candidate, key=lambda r: (r["family"], r["variant"], r["condition"]))]
    return FEEDBACK_PROMPT.format(verify_template=VERIFY_TEMPLATE.format(text="<text of harness/verification.md>", n="N", draft="<draft findings>"),
                                  calls=limits.max_model_calls, actions=limits.max_tool_calls, instructions=instructions,
                                  previous_edit=previous["line"], previous_pattern=previous["pattern"], before_after=before_after,
                                  ground_truth="\n\n".join(gt), traces=json.dumps(traces, indent=1),
                                  ins_limit=ITER2_LIMITS["harness/instructions.md"], ver_limit=ITER2_LIMITS["harness/verification.md"])


def parse_feedback_proposal(text: str) -> tuple[dict | None, str | None]:
    m = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not m:
        return None, "no JSON object"
    try:
        p = json.loads(m.group(0))
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"
    missing = [k for k in ("cause", "change", "predicted_improvement", "predicted_risks")
               if not (isinstance(p.get(k), dict) if k == "change" else isinstance(p.get(k), str) and p[k].strip())]
    if missing:
        return None, f"missing fields {missing}"
    return p, None


def _shingles(text: str, n: int = 6) -> set[tuple]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {tuple(words[i:i + n]) for i in range(len(words) - n + 1)}


def validate_feedback_proposal(p: dict, train_tasks: list[SecTask], original: dict[str, str]) -> list[str]:
    problems = []
    change = {k: v for k, v in p["change"].items() if v is not None}
    unknown = sorted(set(p["change"]) - set(ITER2_EDITABLE))
    if unknown:
        problems.append(f"changes files outside the allowed surface: {unknown}")
    if not change:
        problems.append("no change proposed")
    for path, text in change.items():
        if path not in ITER2_EDITABLE:
            continue
        if not isinstance(text, str):
            problems.append(f"{path}: content must be text")
            continue
        if len(text) > ITER2_LIMITS[path]:
            problems.append(f"{path}: too long ({len(text)} > {ITER2_LIMITS[path]})")
        if text.strip() == original.get(path, "").strip():
            problems.append(f"{path}: identical to the original")
        if path == "harness/instructions.md" and not text.strip():
            problems.append("instructions.md must not be empty")
        problems += [f"{path}: {x}" for x in validate_proposal({"target": "harness/memory.md", "new_content": text}, train_tasks)
                     if "target" not in x and "too long" not in x]
        answers = set()
        for t in train_tasks:
            answers |= _shingles(t.answer["summary"]) | _shingles(t.variant_files("vulnerable")["README.md"])
        copied = _shingles(text) & answers
        if copied:
            problems.append(f"{path}: copies text from training answers or READMEs: {[' '.join(c) for c in sorted(copied)[:3]]}")
    return problems


def apply_files(change: dict[str, str], allowed=ITER2_EDITABLE) -> list[str]:
    """Write the changed editable files, then verify that nothing outside `allowed` differs from HEAD."""
    if changed_files():
        raise RuntimeError(f"working tree not clean before applying: {changed_files()}")
    old = {}
    for path, text in change.items():
        if text is None:
            continue
        f = REPO_ROOT / path
        old[path] = f.read_text(encoding="utf-8") if f.is_file() else ""
        f.write_text(text.strip() + "\n", encoding="utf-8")
    after = changed_files()
    outside = [f for f in after if f not in allowed]
    if outside:
        for path, text in old.items():
            (REPO_ROOT / path).write_text(text, encoding="utf-8")
        raise RuntimeError(f"edit touched read-only files: {outside}")
    return after
