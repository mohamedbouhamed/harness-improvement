"""Text action protocol for the repository agent (no agent framework).

The model replies with one JSON object, optionally inside a ```json fence:
    {"actions": [{"action": "read_file", "path": "pkg/a.py"}, {"action": "run_public_tests"}]}
Arguments sit next to "action". For robustness the parser also accepts "tool"/"name" instead of
"action", arguments nested under "args"/"arguments", "tool_calls" instead of "actions", a single
action object, or a bare list. The protocol avoids the words "tool_calls"/"function": Gemini's
OpenAI-compatible endpoint treats such JSON as native function calling and returns empty replies
(finish_reason MALFORMED_FUNCTION_CALL), as observed in pilot 1.
"""
from __future__ import annotations

import ast
import json
import re

from .contracts import AgentLimits
from .workspace import Workspace, WorkspaceError

TOOL_SPECS = {
    "list_files": ({}, "list repository files; writable ones are marked"),
    "read_file": ({"path": str}, "read one file"),
    "search": ({"query": str}, "case-sensitive substring search; returns path:line: text"),
    "write_file": ({"path": str, "content": str}, "replace a writable file with its full new content"),
    "replace_in_file": ({"path": str, "old": str, "new": str}, "replace one exact, unique occurrence of `old`"),
    "run_public_tests": ({}, "run the public test suite in the sandbox"),
    "submit": ({}, "finish; the current files are your final answer"),
}


EXAMPLE_REPLY = '{"actions": [{"action": "read_file", "path": "pkg/module.py"}, {"action": "run_public_tests"}]}'
_NAME_KEYS = ("action", "tool", "name")
_LIST_KEYS = ("actions", "tool_calls", "calls")


def tool_help() -> str:
    lines = []
    for name, (args, desc) in TOOL_SPECS.items():
        fields = "".join(f', "{k}": <{t.__name__}>' for k, t in args.items())
        lines.append(f'- {{"action": "{name}"{fields}}}: {desc}')
    return "\n".join(lines)


def function_declarations() -> list[dict]:
    """The same actions as OpenAI-format function declarations (sent with every agent request).

    Gemini's OpenAI-compatible endpoint lets the model emit native function calls even when the
    prompt asks for JSON text; without declarations they fail as MALFORMED_FUNCTION_CALL with an
    empty reply (seen in pilot 1 and in the first memory-v2 attempt). Declaring them lets both
    forms work; native calls are converted back to the text action format by the adapter.
    """
    out = []
    for name, (args, desc) in TOOL_SPECS.items():
        props = {k: {"type": "string"} for k in args}
        out.append({"type": "function", "function": {
            "name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": list(args)}}})
    return out


_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def _call_args(c: dict) -> dict | None:
    nested = c.get("args", c.get("arguments"))
    if nested is not None and not isinstance(nested, dict):
        return None
    flat = {k: v for k, v in c.items() if k not in _NAME_KEYS + ("args", "arguments")}
    return flat | (nested or {})


def parse_tool_calls(text: str, max_per_turn: int) -> tuple[list[dict] | None, str | None]:
    """Return (actions, error). Actions are validated for name and argument types."""
    if not text or not text.strip():
        return None, f"empty reply; expected e.g. {EXAMPLE_REPLY}"
    candidates = [m.strip() for m in _FENCE.findall(text)] + [text.strip()]
    data, err = None, "no JSON object found"
    for chunk in candidates:
        start = min([i for i in (chunk.find("{"), chunk.find("[")) if i >= 0], default=-1)
        if start < 0:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(chunk[start:])
            break
        except json.JSONDecodeError as exc:
            err = f"invalid JSON: {exc.msg}"
    if data is None:
        return None, f"{err}; expected e.g. {EXAMPLE_REPLY}"
    if isinstance(data, dict):
        key = next((k for k in _LIST_KEYS if k in data), None)
        if key is not None:
            data = data[key]
    calls = data if isinstance(data, list) else [data]
    if not calls:
        return None, f"no actions; expected e.g. {EXAMPLE_REPLY}"
    if len(calls) > max_per_turn:
        return None, f"too many actions in one reply ({len(calls)} > {max_per_turn})"
    out = []
    for c in calls:
        name = next((c[k] for k in _NAME_KEYS if isinstance(c, dict) and k in c), None)
        if name not in TOOL_SPECS:
            return None, (f"unknown action {str(c)[:80]!r}; valid actions: {sorted(TOOL_SPECS)}; "
                          f"expected e.g. {EXAMPLE_REPLY}")
        args = _call_args(c)
        if args is None:
            return None, f"{name}: arguments must be fields of the action object, e.g. {EXAMPLE_REPLY}"
        for key, typ in TOOL_SPECS[name][0].items():
            if not isinstance(args.get(key), typ):
                return None, (f'{name}: missing or invalid "{key}" (a {typ.__name__}); write it next to '
                              f'"action", e.g. {EXAMPLE_REPLY}')
        out.append({"name": name, "args": {k: args[k] for k in TOOL_SPECS[name][0]}})
    if any(c["name"] == "submit" for c in out[:-1]):
        return None, "submit must be the last action"
    return out, None


def cap(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = limit // 4
    return text[:head] + f"\n...[{len(text) - limit} chars truncated]...\n" + text[-(limit - head):]


class ToolExecutor:
    """Executes file tools against a Workspace. Test runs and submission are handled by the agent."""

    def __init__(self, ws: Workspace, limits: AgentLimits):
        self.ws, self.limits = ws, limits
        self.edits = 0

    def execute(self, name: str, args: dict) -> tuple[bool, str]:
        try:
            return True, getattr(self, f"_{name}")(**args)
        except WorkspaceError as exc:
            return False, f"error: {exc}"

    def _list_files(self) -> str:
        return "\n".join(f"{rel}{'  (writable)' if rel in self.ws.allowed else ''}" for rel in self.ws.list_files())

    def _read_file(self, path: str) -> str:
        return cap(self.ws.read(path), self.limits.read_chars)

    def _search(self, query: str) -> str:
        hits = self.ws.search(query, self.limits.search_max_matches)
        return "\n".join(hits) if hits else "no matches"

    def _write_file(self, path: str, content: str) -> str:
        if len(content) > self.limits.write_chars:
            raise WorkspaceError(f"content too large ({len(content)} > {self.limits.write_chars} chars)")
        rel = self.ws.write(path, content)
        self.edits += 1
        return f"wrote {rel} ({len(content)} chars)" + _syntax_note(content)

    def _replace_in_file(self, path: str, old: str, new: str) -> str:
        text = self.ws.read(path)
        count = text.count(old) if old else 0
        if count != 1:
            raise WorkspaceError(f"`old` must occur exactly once in {path} (found {count})")
        updated = text.replace(old, new, 1)
        if len(updated) > self.limits.write_chars:
            raise WorkspaceError("resulting file too large")
        rel = self.ws.write(path, updated)
        self.edits += 1
        return f"edited {rel}" + _syntax_note(updated)


def _syntax_note(code: str) -> str:
    try:
        ast.parse(code)  # parse only; never executed on the host
    except SyntaxError as exc:
        return f" (warning: syntax error at line {exc.lineno}: {exc.msg})"
    return ""
