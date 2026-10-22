"""Bounded vulnerability-detection agent: one episode = one program variant + a fresh conversation.

Read-only tools over an in-memory copy of the program (nothing is written or executed). The episode
ends with `submit_findings`; the findings are scored afterwards (scoring.py) and the score never
reaches the model. The agent never receives the task id, split, class, answer key or witness tests.

The tool protocol and limits live here (read-only for Phase 2). `harness/instructions.md` (the
reviewer's instructions) and the guidance text (empty, a control text, or harness/memory.md) are
inputs, so only they can change between conditions or Phase 2 edits.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from harness_mvp import llm
from harness_mvp.contracts import estimate_tokens
from harness_mvp.model import CallBudget
from harness_mvp.tools import cap
from harness_mvp.workspace import WorkspaceError, normalize_rel

from .scoring import score_episode
from .tasks import SecTask

HARNESS_DIR = Path(__file__).resolve().parent.parent / "harness"
GUIDANCE_HEADING = "## Additional guidance"


@dataclass(frozen=True)
class DetectLimits:
    max_model_calls: int = 8
    max_tool_calls: int = 30
    max_actions_per_turn: int = 6
    read_chars: int = 9000
    tool_result_chars: int = 9000
    search_max_matches: int = 40
    max_findings: int = 10

    def to_dict(self) -> dict:
        return asdict(self)


FINDING_FIELDS = {
    "file": "repository-relative path of the file with the weakness",
    "function": "name of the function where the weakness is",
    "lines": "[first, last] line numbers of the weak code",
    "cwe": "CWE identifier, e.g. CWE-79",
    "source": "where the untrusted input enters",
    "path": "list of function names from the entry point to the weak code",
    "reason": "one or two sentences: why the input is not handled safely",
}
TOOLS = {
    "list_files": ({}, "list the repository files"),
    "read_file": ({"path": "string"}, "read one file (shown with line numbers)"),
    "search": ({"query": "string"}, "case-sensitive substring search over all files; returns path:line: text"),
    "submit_findings": ({"findings": "array"}, "finish the review: the list of findings (may be empty)"),
}
EXAMPLE = ('{"actions": [{"action": "read_file", "path": "pkg/api.py"}, '
           '{"action": "search", "query": "subprocess"}]}')
SUBMIT_EXAMPLE = ('{"actions": [{"action": "submit_findings", "findings": [{"file": "<path>", "function": "<name>", '
                  '"lines": [<first>, <last>], "cwe": "CWE-<number>", "source": "<entry point>", '
                  '"path": ["<entry function>", "...", "<weak function>"], "reason": "<why>"}]}]}')

PROTOCOL = """Act only by requesting actions. Call the provided functions, or reply with exactly one JSON object and nothing else, for example:
{example}
Up to {per_turn} actions per reply run in order; their results come back in the next message.

Actions:
{tools}

Finish with submit_findings, for example:
{submit}
Each finding has these fields:
{fields}
Submit an empty list if there is nothing to report. Limits: {calls} replies and {tool_calls} actions in total; if you run out of replies without submitting, nothing is reported."""


def tool_help() -> str:
    return "\n".join(f'- {{"action": "{n}"' + "".join(f', "{k}": <{t}>' for k, t in a.items()) + f"}}: {d}"
                     for n, (a, d) in TOOLS.items())


def function_declarations() -> list[dict]:
    finding = {"type": "object", "properties": {
        "file": {"type": "string"}, "function": {"type": "string"},
        "lines": {"type": "array", "items": {"type": "integer"}}, "cwe": {"type": "string"},
        "source": {"type": "string"}, "path": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"}}, "required": ["file", "function", "cwe", "reason"]}
    props = {"path": {"type": "string"}, "query": {"type": "string"},
             "findings": {"type": "array", "items": finding}}
    return [{"type": "function", "function": {"name": n, "description": d, "parameters": {
        "type": "object", "properties": {k: props[k] for k in a}, "required": list(a)}}}
        for n, (a, d) in TOOLS.items()]


def read_instructions(path: Path = HARNESS_DIR / "instructions.md") -> str:
    return path.read_text(encoding="utf-8").strip()


def system_prompt(instructions: str, limits: DetectLimits) -> str:
    protocol = PROTOCOL.format(example=EXAMPLE, per_turn=limits.max_actions_per_turn, tools=tool_help(),
                               submit=SUBMIT_EXAMPLE, calls=limits.max_model_calls, tool_calls=limits.max_tool_calls,
                               fields="\n".join(f"- {k}: {v}" for k, v in FINDING_FIELDS.items()))
    return instructions + "\n\n" + protocol


def numbered(text: str) -> str:
    return "\n".join(f"{i:4d}  {line}" for i, line in enumerate(text.splitlines(), 1))


def first_message(files: dict[str, str], guidance: str) -> str:
    parts = ["Review this repository.", "", "Files:"] + [f"- {rel}" for rel in sorted(files)]
    if "README.md" in files:
        parts += ["", "### README.md", files["README.md"].strip()]
    if guidance.strip():
        parts += ["", GUIDANCE_HEADING, guidance.strip()]
    return "\n".join(parts)


_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def parse_actions(text: str, max_per_turn: int) -> tuple[list[dict] | None, str | None]:
    if not text or not text.strip():
        return None, f"empty reply; expected e.g. {EXAMPLE}"
    data, err = None, "no JSON object found"
    for chunk in [m.strip() for m in _FENCE.findall(text)] + [text.strip()]:
        start = min([i for i in (chunk.find("{"), chunk.find("[")) if i >= 0], default=-1)
        if start < 0:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(chunk[start:])
            break
        except json.JSONDecodeError as exc:
            err = f"invalid JSON: {exc.msg}"
    if data is None:
        return None, f"{err}; expected e.g. {EXAMPLE}"
    if isinstance(data, dict) and isinstance(data.get("actions"), list):
        data = data["actions"]
    calls = data if isinstance(data, list) else [data]
    if not calls:
        return None, "no actions"
    if len(calls) > max_per_turn:
        return None, f"too many actions in one reply ({len(calls)} > {max_per_turn})"
    out = []
    for c in calls:
        name = c.get("action", c.get("name")) if isinstance(c, dict) else None
        if name not in TOOLS:
            return None, f"unknown action {str(c)[:80]!r}; valid actions: {sorted(TOOLS)}"
        args = {k: v for k, v in c.items() if k not in ("action", "name")}
        if isinstance(args.get("arguments"), dict):
            args = args["arguments"]
        if name == "submit_findings" and isinstance(args.get("findings"), str):
            try:
                args["findings"] = json.loads(args["findings"])
            except json.JSONDecodeError:
                return None, "findings must be a JSON list"
        for key, typ in TOOLS[name][0].items():
            want = list if typ == "array" else str
            if not isinstance(args.get(key), want):
                return None, f'{name}: missing or invalid "{key}" ({typ})'
        out.append({"name": name, "args": {k: args[k] for k in TOOLS[name][0]}})
    if any(c["name"] == "submit_findings" for c in out[:-1]):
        return None, "submit_findings must be the last action"
    return out, None


class ReadOnlyRepo:
    def __init__(self, files: dict[str, str]):
        self.files = dict(files)

    def _get(self, rel) -> tuple[str, str]:
        rel = normalize_rel(rel)
        if rel not in self.files:
            raise WorkspaceError(f"no such file: {rel}")
        return rel, self.files[rel]

    def run(self, name: str, args: dict, limits: DetectLimits) -> tuple[bool, str]:
        try:
            if name == "list_files":
                return True, "\n".join(sorted(self.files))
            if name == "read_file":
                rel, text = self._get(args["path"])
                return True, cap(numbered(text), limits.read_chars)
            if name == "search":
                if not args["query"]:
                    raise WorkspaceError("empty query")
                hits = [f"{rel}:{i}: {line.strip()[:200]}" for rel in sorted(self.files)
                        for i, line in enumerate(self.files[rel].splitlines(), 1) if args["query"] in line]
                return True, "\n".join(hits[:limits.search_max_matches]) or "no matches"
        except WorkspaceError as exc:
            return False, f"error: {exc}"
        raise ValueError(name)


def run_episode(task: SecTask, variant: str, model, budget: CallBudget, *, episode_id: str, condition: str,
                repetition: int, instructions: str, guidance: str, limits: DetectLimits = DetectLimits(),
                api_retries: int = 2, retry_wait_s: float = 10.0, log=None) -> tuple[dict, list[dict]]:
    """Returns (episode record with score, transcript entries)."""
    log = log or (lambda m: None)
    t0, started = time.perf_counter(), llm.now_iso()
    files = task.variant_files(variant)
    repo = ReadOnlyRepo(files)
    messages = [{"role": "system", "content": system_prompt(instructions, limits)},
                {"role": "user", "content": first_message(files, guidance)}]
    turns, transcript = [], []
    tool_calls, findings, end_reason, fatal = 0, None, "model_call_limit", None
    requested_model = getattr(getattr(model, "settings", None), "model", None)
    for turn_no in range(1, limits.max_model_calls + 1):
        context = {"tools": function_declarations(), "label": f"{episode_id} t{turn_no}",
                   "task_id": task.task_id, "variant": variant, "turn": turn_no}
        resp, requests, fatal, _ = llm.request(model, messages, budget, context, api_retries=api_retries,
                                               retry_wait_s=retry_wait_s, log=log, label=f"{episode_id} t{turn_no}")
        for r in requests:
            r["requested_model"] = requested_model
        if resp is not None and requests:
            requests[-1].update(returned_model=getattr(resp, "model", None),
                                raw_usage=getattr(resp, "raw_usage", None), cost_eur=getattr(resp, "cost_eur", None))
        turn = {"turn": turn_no, "requests": requests, "parse_error": None, "actions": []}
        turns.append(turn)
        transcript.append({"episode_id": episode_id, "turn": turn_no, "messages": list(messages),
                           "response": resp.text if resp else None})
        if resp is None:
            end_reason = fatal or "api_error"
            break
        messages = messages + [{"role": "assistant", "content": resp.text}]
        calls, err = parse_actions(resp.text, limits.max_actions_per_turn)
        if err:
            turn["parse_error"] = err
            messages.append({"role": "user", "content": f"Invalid reply: {err}. Reply with one JSON object only."})
            continue
        results = []
        for call in calls:
            if tool_calls >= limits.max_tool_calls:
                results.append(f"[{call['name']}] error: action limit reached")
                break
            tool_calls += 1
            if call["name"] == "submit_findings":
                findings = call["args"]["findings"][:limits.max_findings]
                turn["actions"].append({"name": "submit_findings", "n": len(findings)})
                break
            ok, out = repo.run(call["name"], call["args"], limits)
            out = cap(out, limits.tool_result_chars)
            turn["actions"].append({"name": call["name"], "args": call["args"], "ok": ok, "result_chars": len(out)})
            results.append(f"[{call['name']} {json.dumps(call['args'])}]\n{out}")
        if findings is not None:
            end_reason = "submitted"
            break
        if tool_calls >= limits.max_tool_calls:
            end_reason = "tool_call_limit"
            break
        messages.append({"role": "user", "content": "Action results:\n\n" + "\n\n".join(results)})

    # ---- Episode over: no more model calls. Scoring uses the answer key, never shown to the model. ----
    score = score_episode(task, variant, findings)
    reqs = [r for t in turns for r in t["requests"]]
    record = {
        "episode_id": episode_id, "task_id": task.task_id, "split": task.split, "family": task.family,
        "class": task.weakness_class, "witness_kind": task.entry.get("witness_kind"), "variant": variant, "condition": condition, "repetition": repetition,
        "synthetic": bool(getattr(model, "synthetic", False)), "started_at": started,
        "task_hashes": task.hashes(), "guidance_chars": len(guidance), "guidance_est_tokens": estimate_tokens(guidance),
        "end_reason": end_reason, "fatal": fatal, "findings": findings, "score": score,
        "model_calls": len(reqs), "tool_calls": tool_calls, "parse_errors": sum(1 for t in turns if t["parse_error"]),
        "files_read": sorted({a["args"]["path"] for t in turns for a in t["actions"] if a["name"] == "read_file"}),
        "input_tokens": llm.sum_or_none(r["input_tokens"] for r in reqs),
        "output_tokens": llm.sum_or_none(r["output_tokens"] for r in reqs),
        "cost_eur": round(sum(r.get("cost_eur") or 0.0 for r in reqs), 6),
        "model_latency_s": round(sum(r["latency_s"] for r in reqs), 3),
        "episode_latency_s": round(time.perf_counter() - t0, 3),
        "api_errors": sum(1 for r in reqs if not r["ok"]),
        "requested_models": sorted({r["requested_model"] for r in reqs if r.get("requested_model")}),
        "returned_models": sorted({r["returned_model"] for r in reqs if r.get("returned_model")}),
        "turns": turns,
    }
    return record, transcript
