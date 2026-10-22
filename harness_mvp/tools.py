"""Text tool protocol for the repository agent (no agent framework).

The model replies with one JSON object, optionally inside a ```json fence:
    {"tool_calls": [{"name": "read_file", "args": {"path": "pkg/a.py"}}, ...]}
A single {"name": ..., "args": ...} object or a bare list of calls is also accepted.
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


def tool_help() -> str:
    lines = []
    for name, (args, desc) in TOOL_SPECS.items():
        sig = ", ".join(f'"{k}": {t.__name__}' for k, t in args.items())
        lines.append(f"- {name} {{{sig}}}: {desc}")
    return "\n".join(lines)


_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL)


def parse_tool_calls(text: str, max_per_turn: int) -> tuple[list[dict] | None, str | None]:
    """Return (calls, error). Calls are validated for name and argument types."""
    if not text or not text.strip():
        return None, "empty reply"
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
        return None, err
    if isinstance(data, dict) and "tool_calls" in data:
        data = data["tool_calls"]
    calls = data if isinstance(data, list) else [data]
    if not calls:
        return None, "no tool calls"
    if len(calls) > max_per_turn:
        return None, f"too many tool calls in one reply ({len(calls)} > {max_per_turn})"
    out = []
    for c in calls:
        if not isinstance(c, dict) or c.get("name") not in TOOL_SPECS:
            return None, f"unknown tool call {str(c)[:80]!r}; tools: {sorted(TOOL_SPECS)}"
        args = c.get("args", c.get("arguments", {})) or {}
        if not isinstance(args, dict):
            return None, f"{c['name']}: args must be an object"
        for key, typ in TOOL_SPECS[c["name"]][0].items():
            if not isinstance(args.get(key), typ):
                return None, f"{c['name']}: argument {key!r} must be a {typ.__name__}"
        out.append({"name": c["name"], "args": {k: args[k] for k in TOOL_SPECS[c["name"]][0]}})
    if any(c["name"] == "submit" for c in out[:-1]):
        return None, "submit must be the last tool call"
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
