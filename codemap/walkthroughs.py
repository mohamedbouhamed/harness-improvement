"""AI walkthroughs: written by a coding agent, then checked against the verified graph.

The agent only *interprets*. Every step must anchor to a node id and a line range that exist in the
static graph; anything that does not check out is flagged, never silently trusted. The checks are
recomputed on every build, so a walkthrough whose code has changed since it was written shows as stale.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

CODE_KINDS = ("function", "method", "toplevel", "class")
PATH_EDGE_KINDS = ("call", "instantiate", "reference")
MAX_SOURCE_CHARS = 150_000
MENTION = re.compile(r"\[\[([^\]]+)\]\]")

SCHEMA = {
    "type": "object",
    "properties": {
        "walkthroughs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "question": {"type": "string", "description": "the question this walkthrough answers"},
                    "summary": {"type": "string"},
                    "steps": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "node": {"type": "string"},
                                "start_line": {"type": "integer"},
                                "end_line": {"type": "integer"},
                                "title": {"type": "string"},
                                "text": {"type": "string"},
                            },
                            "required": ["node", "start_line", "end_line", "title", "text"],
                        },
                    },
                },
                "required": ["title", "question", "summary", "steps"],
            },
        }
    },
    "required": ["walkthroughs"],
}

INSTRUCTIONS = """You are writing guided walkthroughs for an interactive, zoomable map of a codebase.
Each walkthrough is a tour: the map flies to each step's code and shows your text beside it.

The map's VERIFIED STRUCTURE (from static analysis, not from you) is given below: a node index,
the call relationships, and the entry points. Your text is shown as an AI interpretation, visibly
separate from the verified structure, and every step is machine-checked against it.

Rules:
- Each step anchors to exactly ONE node id copied verbatim from the NODE INDEX, plus a line range
  (absolute file line numbers) that lies inside that node's lines. Prefer small ranges (1-12 lines)
  that point at the code your sentence is about.
- Follow real execution order. Step k+1 should normally be code that step k calls (directly or a few
  calls deep) or the place step k's data goes next. If you jump elsewhere, say why in the text.
- Text: 1-3 sentences, at most 70 words. Be concrete: what comes in, what is decided or transformed,
  what is returned, written, sent or raised. Name other code as [[node.id]] using ids from the index.
- Only describe what the code shows. Where behaviour depends on runtime (dynamic dispatch, config,
  environment), say so instead of guessing.
- Do not restate the code line by line; explain intent and consequences.
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _code_nodes(graph: dict) -> dict[str, dict]:
    return {n["id"]: n for n in graph["nodes"] if n["kind"] in CODE_KINDS}


def _node_line(n: dict) -> str:
    sig = n.get("sig") or n["kind"]
    doc = (n.get("doc") or "").split("\n")[0][:120]
    entry = f" [ENTRY: {n['entry']}]" if n.get("entry") and n["entry"] != "test" else ""
    return f"{n['id']} | {n['kind']} | {n['file']}:{n['line']}-{n['end']} | {sig}{entry}" + (f" | {doc}" if doc else "")


def build_prompt(graph: dict, focus: dict | None = None, count: int | None = None) -> tuple[str, bool]:
    """Returns (prompt, needs_tools). Tools are only needed when the source does not fit in the prompt."""
    nodes = _code_nodes(graph)
    focus = focus or {"kind": "overview"}
    parts = [INSTRUCTIONS]
    if focus["kind"] == "overview":
        n = count or 4
        parts.append(f"TASK: Write {n} walkthroughs that together explain how this program works, each 5-10 steps. "
                     "Start from the real entry points (marked ENTRY) and follow the most important execution "
                     "paths end to end: how input arrives, where decisions are made, what external effects "
                     "happen (files, network, subprocesses) and what the final output is. Give each a "
                     "distinct question, e.g. 'What happens when I run X?'.")
    elif focus["kind"] == "node":
        nid = focus["node"]
        parts.append(f"TASK: Write 1 walkthrough (4-8 steps) explaining how [[{nid}]] works: where its inputs "
                     "come from (start at a caller if that helps), its key decisions, what it calls, and what it "
                     "returns or writes. Its callers and callees are in the relationships below.")
    elif focus["kind"] == "trace":
        path = " -> ".join(focus["path"])
        tracked = f" Follow the value of parameter `{focus['param']}` of [[{focus['path'][0]}]]." \
            if focus.get("param") else ""
        parts.append("TASK: Write 1 walkthrough narrating this specific execution path, one or more steps per "
                     f"function, in order: {path}.{tracked} Explain what each hop passes along and why.")
    parts.append("NODE INDEX (id | kind | file:lines | signature | doc):")
    parts.extend(_node_line(n) for n in nodes.values())
    parts.append("\nCALL RELATIONSHIPS (caller -> callee, kind, confidence, call lines):")
    for e in graph["edges"]:
        if e["kind"] in PATH_EDGE_KINDS and e["s"] in nodes and e["t"] in nodes:
            parts.append(f"{e['s']} -> {e['t']} ({e['kind']}, {e['conf']}, line {','.join(map(str, e['lines'][:4]))})")
    ext_by_src: dict[str, list[str]] = {}
    for e in graph["edges"]:
        if e["kind"] == "external" and e["s"] in nodes:
            ext_by_src.setdefault(e["s"], []).append(e["t"][4:])
    parts.append("\nEXTERNAL CALLS (node: library calls):")
    parts.extend(f"{k}: {', '.join(sorted(set(v)))}" for k, v in ext_by_src.items())

    sources, total = [], 0
    files = graph["files"]
    order = list(files)
    if focus["kind"] in ("node", "trace"):
        wanted = {focus.get("node")} | set(focus.get("path", []))
        first = [f for f in order if any(nodes.get(w, {}).get("file") == f for w in wanted)]
        order = first + [f for f in order if f not in first]
    for rel in order:
        text = "\n".join(f"{i:>4}  {line}" for i, line in enumerate(files[rel]["lines"], 1))
        if total + len(text) > MAX_SOURCE_CHARS:
            continue
        sources.append(f"\n===== {rel} =====\n{text}")
        total += len(text)
    needs_tools = len(sources) < len(files)
    parts.append("\nSOURCE (line-numbered)" + (" — partial; read the remaining files with your tools if needed:"
                                                if needs_tools else ":"))
    parts.extend(sources)
    parts.append("\nReturn only the JSON object described by the schema.")
    return "\n".join(parts), needs_tools


def run_agent(prompt: str, cwd: Path, agent_cmd: str | None = None, model: str | None = None,
              tools: bool = False, timeout_s: int = 900) -> tuple[dict, dict]:
    """Ask the coding agent for walkthrough JSON. Returns (data, provenance)."""
    if agent_cmd:
        cmd = shlex.split(agent_cmd)
        proc = subprocess.run(cmd, input=prompt + "\n\nJSON schema:\n" + json.dumps(SCHEMA), cwd=cwd,
                              capture_output=True, text=True, timeout=timeout_s)
        if proc.returncode != 0:
            raise RuntimeError(f"agent command failed ({proc.returncode}): {proc.stderr[-800:]}")
        return _parse_json_blob(proc.stdout), {"agent": agent_cmd}
    cmd = ["claude", "-p", "--output-format", "json", "--json-schema", json.dumps(SCHEMA)]
    if tools:
        cmd += ["--tools", "Read,Grep,Glob", "--allowedTools", "Read,Grep,Glob"]
    else:
        cmd += ["--tools", ""]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(cmd, input=prompt, cwd=cwd, capture_output=True, text=True, timeout=timeout_s)
    except FileNotFoundError:
        raise RuntimeError("the `claude` CLI was not found on PATH; install Claude Code or pass --agent-cmd") from None
    if proc.returncode != 0:
        raise RuntimeError(f"claude -p failed ({proc.returncode}): {(proc.stderr or proc.stdout)[-800:]}")
    out = json.loads(proc.stdout)
    if out.get("is_error"):
        raise RuntimeError(f"claude -p reported an error: {str(out.get('result'))[:800]}")
    data = out.get("structured_output")
    if data is None:
        data = _parse_json_blob(out.get("result") or "")
    models = sorted((out.get("modelUsage") or {}).keys())
    return data, {"agent": "claude -p (Claude Code)", "models": models,
                  "cost_usd": out.get("total_cost_usd"), "session_id": out.get("session_id")}


def _parse_json_blob(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise RuntimeError("agent output did not contain a JSON object")
    return json.loads(text[start:end + 1])


# ---------------------------------------------------------------------------
# Storage and verification
# ---------------------------------------------------------------------------

def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "walkthrough"


def anchor_hash(graph: dict, file: str, start: int, end: int) -> str:
    import hashlib
    lines = graph["files"][file]["lines"][start - 1:end]
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]


def resolve_node_id(raw: str, nodes: dict[str, dict]) -> tuple[str | None, str | None]:
    """Exact id, else a unique suffix match (agents sometimes drop the package prefix)."""
    raw = raw.strip().strip("`[]")
    if raw in nodes:
        return raw, None
    matches = [nid for nid in nodes if nid.endswith("." + raw) or nid.endswith(":" + raw)]
    if len(matches) == 1:
        return matches[0], f"node id `{raw}` corrected to `{matches[0]}`"
    return None, None


def stamp(graph: dict, raw: dict, provenance: dict, focus: dict | None) -> list[dict]:
    """Normalize the agent's output and record content hashes of every anchor at generation time."""
    nodes = _code_nodes(graph)
    out = []
    for w in raw.get("walkthroughs", []):
        steps = []
        for s in w.get("steps", []):
            nid, note = resolve_node_id(str(s.get("node", "")), nodes)
            step = {"node": nid or str(s.get("node")), "start": int(s.get("start_line") or 0),
                    "end": int(s.get("end_line") or 0), "title": str(s.get("title", ""))[:120],
                    "text": str(s.get("text", ""))[:1200]}
            if note:
                step["note"] = note
            if nid:
                n = nodes[nid]
                lo, hi = max(step["start"], n["line"]), min(step["end"] or step["start"], n["end"])
                if lo <= hi:
                    step["anchor_sha"] = anchor_hash(graph, n["file"], lo, hi)
            steps.append(step)
        title = str(w.get("title", "Walkthrough"))[:120]
        out.append({"id": f"{slug(title)}-{now_iso()[:19].replace(':', '')}", "title": title,
                    "question": str(w.get("question", ""))[:300], "summary": str(w.get("summary", ""))[:1200],
                    "steps": steps, "generated_at": now_iso(), "provenance": provenance, "focus": focus})
    return out


def load_store(path: Path) -> dict:
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"version": 1, "walkthroughs": []}


def save_store(path: Path, store: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2) + "\n", encoding="utf-8")


def _adjacency(graph: dict) -> dict[str, list[tuple[str, str]]]:
    adj: dict[str, list[tuple[str, str]]] = {}
    for e in graph["edges"]:
        if e["kind"] in PATH_EDGE_KINDS:
            adj.setdefault(e["s"], []).append((e["t"], e["conf"]))
    return adj


def _host_chain(nid: str, nodes: dict) -> list[str]:
    """The node and its enclosing code nodes (nested function -> parent function -> ...)."""
    chain = [nid]
    while nodes.get(chain[-1], {}).get("nested"):
        chain.append(nodes[chain[-1]]["parent"])
    return chain


def static_path(graph_adj, nodes, a: str, b: str, max_len: int = 5) -> tuple[list[str] | None, str | None]:
    """Shortest static call path a -> b. Returns (path, weakest confidence on it)."""
    if a == b or b in _host_chain(a, nodes) or a in _host_chain(b, nodes):
        return [a], "resolved"
    starts = _host_chain(a, nodes)
    prev: dict[str, tuple[str | None, str]] = {s: (None, "resolved") for s in starts}
    q = deque((s, 0) for s in starts)
    rank = {"resolved": 2, "inferred": 1, "dynamic": 0}
    while q:
        cur, d = q.popleft()
        if cur == b:
            path, weakest = [], "resolved"
            while cur is not None:
                path.append(cur)
                p, conf = prev[cur]
                if p is not None and rank[conf] < rank[weakest]:
                    weakest = conf
                cur = p
            return path[::-1], weakest
        if d >= max_len:
            continue
        for t, conf in graph_adj.get(cur, []):
            if t not in prev:
                prev[t] = (cur, conf)
                q.append((t, d + 1))
    return None, None


def short_name(nid: str) -> str:
    return nid.split(":")[0].rsplit(".", 1)[-1] + (" (top level)" if nid.endswith(":<module>") else "")


def verify(graph: dict, store: dict) -> list[dict]:
    """Attach machine checks to each walkthrough step. Never modifies the AI text."""
    nodes = _code_nodes(graph)
    adj = _adjacency(graph)
    out = []
    for w in store.get("walkthroughs", []):
        w = json.loads(json.dumps(w))
        prev_node = None
        seen_nodes: list[str] = []
        problems = 0
        for s in w["steps"]:
            checks = []
            n = nodes.get(s["node"])
            if n is None:
                checks.append({"ok": False, "kind": "node", "msg": f"`{s['node']}` is not a node in the verified graph"})
                problems += 1
            else:
                if s.get("note"):
                    checks.append({"ok": True, "kind": "node", "msg": s["note"]})
                start, end = s["start"], s["end"] or s["start"]
                if start < n["line"] or end > n["end"] or start > end:
                    checks.append({"ok": False, "kind": "lines",
                                   "msg": f"lines {start}-{end} fall outside {n['qual']} ({n['line']}-{n['end']}); "
                                          "anchored to the whole node instead"})
                    s["start"], s["end"] = n.get("def_line", n["line"]), n["end"]
                    problems += 1
                else:
                    checks.append({"ok": True, "kind": "lines", "msg": f"lines {start}-{end} exist in {n['file']}"})
                if s.get("anchor_sha"):
                    lo, hi = max(start, n["line"]), min(end, n["end"])
                    now = anchor_hash(graph, n["file"], lo, hi) if lo <= hi else None
                    if now != s["anchor_sha"]:
                        checks.append({"ok": False, "kind": "stale",
                                       "msg": "the code at this anchor changed after the explanation was written"})
                        s["stale"] = True
                        problems += 1
                if prev_node and prev_node != s["node"]:
                    path, conf = static_path(adj, nodes, prev_node, s["node"])
                    if path is None:
                        rpath, _ = static_path(adj, nodes, s["node"], prev_node)
                        earlier = next(((k, p, c) for k, p, c in
                                        ((k, *static_path(adj, nodes, k, s["node"])) for k in reversed(seen_nodes[:-1]))
                                        if p), None)
                        if earlier and not rpath:
                            k, epath, econf = earlier
                            s["link"] = {"status": "verified" if econf in ("resolved", "inferred") else "dynamic",
                                         "path": epath}
                            checks.append({"ok": True, "kind": "link",
                                           "msg": f"continues from earlier step {short_name(k)}: "
                                                  + " → ".join(short_name(p) for p in epath)})
                        elif rpath:
                            s["link"] = {"status": "return", "path": rpath[::-1]}
                            checks.append({"ok": True, "kind": "link",
                                           "msg": "returns to a caller of the previous step (verified call path)"})
                        else:
                            s["link"] = {"status": "unverified"}
                            checks.append({"ok": False, "kind": "link",
                                           "msg": "no static call path from the previous step; the sequence is the AI's claim"})
                    else:
                        status = "verified" if conf in ("resolved", "inferred") else "dynamic"
                        s["link"] = {"status": status, "path": path}
                        checks.append({"ok": True, "kind": "link",
                                       "msg": ("verified call path: " if status == "verified" else
                                               "call path via dynamic dispatch: ") + " → ".join(
                                           short_name(p) for p in path)})
            bad = [m for m in MENTION.findall(s["text"]) if resolve_node_id(m, nodes)[0] is None]
            if bad:
                checks.append({"ok": False, "kind": "mention", "msg": "unknown symbols mentioned: " + ", ".join(bad)})
            s["checks"] = checks
            prev_node = s["node"] if n else prev_node
            if n and s["node"] not in seen_nodes:
                seen_nodes.append(s["node"])
        w["problems"] = problems
        w["unverified_hops"] = sum(1 for st in w["steps"] if st.get("link", {}).get("status") == "unverified")
        out.append(w)
    return out


def generate(graph: dict, root: Path, store_path: Path, focus: dict | None = None, count: int | None = None,
             agent_cmd: str | None = None, model: str | None = None, dry_run: bool = False) -> list[dict]:
    prompt, needs_tools = build_prompt(graph, focus, count)
    if dry_run:
        print(prompt)
        return []
    raw, provenance = run_agent(prompt, root, agent_cmd=agent_cmd, model=model, tools=needs_tools)
    new = stamp(graph, raw, provenance, focus)
    store = load_store(store_path)
    store["walkthroughs"].extend(new)
    save_store(store_path, store)
    return new
