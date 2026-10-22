"""CLI: build | serve | explain."""
from __future__ import annotations

import argparse
import json
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .analyze import analyze, source_fingerprint
from .layout import layout
from .walkthroughs import generate, load_store, verify

VIEWER = Path(__file__).with_name("viewer.html")
OUT_DIR = ".codemap"
CONFIG = "config.json"
STORE = "walkthroughs.json"


def load_config(root: Path) -> dict:
    p = root / OUT_DIR / CONFIG
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else {}


def resolve_paths(root: Path, given: list[str]) -> list[str]:
    """Paths from the command line win; otherwise the ones remembered in .codemap/config.json; else the root."""
    if given:
        paths = given
        cfg_path = root / OUT_DIR / CONFIG
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        cfg = load_config(root)
        cfg["paths"] = paths
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
        return paths
    return load_config(root).get("paths") or ["."]


def build_data(root: Path, paths: list[str], server: bool = False) -> dict:
    graph = layout(analyze(root, [root / p for p in paths]))
    graph["walkthroughs"] = verify(graph, load_store(root / OUT_DIR / STORE))
    graph["server"] = server
    graph["paths"] = paths
    graph["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    graph["fingerprint"] = fingerprint(root, paths)
    return graph


def fingerprint(root: Path, paths: list[str]) -> str:
    store = root / OUT_DIR / STORE
    extra = f"{store.stat().st_mtime_ns}" if store.is_file() else "-"
    return source_fingerprint(root, [root / p for p in paths]) + ":" + extra


def render_html(data: dict) -> str:
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    return VIEWER.read_text(encoding="utf-8").replace("__CODEMAP_JSON__", blob)


def cmd_build(args) -> int:
    root = Path(args.root).resolve()
    paths = resolve_paths(root, args.paths)
    data = build_data(root, paths)
    out = root / OUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    (out / "graph.json").write_text(json.dumps(data, indent=1) + "\n", encoding="utf-8")
    page = out / "index.html"
    page.write_text(render_html(data), encoding="utf-8")
    code = sum(1 for n in data["nodes"] if n["kind"] in ("function", "method"))
    print(f"[codemap] {len(data['files'])} files, {code} functions/methods, {len(data['edges'])} relationships, "
          f"{len(data['walkthroughs'])} AI walkthroughs -> {page}")
    for err in data["errors"]:
        print(f"[codemap] skipped {err['file']}: {err['error']}")
    if args.open:
        webbrowser.open(page.as_uri())
    return 0


def cmd_explain(args) -> int:
    root = Path(args.root).resolve()
    paths = resolve_paths(root, args.paths)
    graph = build_data(root, paths)
    focus = {"kind": "node", "node": args.node} if args.node else {"kind": "overview"}
    if args.node and args.node not in {n["id"] for n in graph["nodes"]}:
        raise SystemExit(f"error: unknown node id {args.node!r} (ids look like package.module.function)")
    print(f"[codemap] asking the coding agent for walkthroughs ({focus['kind']}); this can take a few minutes ...",
          flush=True)
    new = generate(graph, root, root / OUT_DIR / STORE, focus=focus, count=args.count,
                   agent_cmd=args.agent_cmd, model=args.model, dry_run=args.dry_run)
    if args.dry_run:
        return 0
    checked = {w["id"]: w for w in verify(graph, {"walkthroughs": new})}
    for w in new:
        v = checked[w["id"]]
        print(f"  + {w['title']}  ({len(w['steps'])} steps, {v['problems']} failed anchor checks, "
              f"{v['unverified_hops']} hops without a static call path)")
    print(f"[codemap] saved to {root / OUT_DIR / STORE}; run `python -m codemap build` or reload `serve` to see them")
    return 0


def cmd_serve(args) -> int:
    root = Path(args.root).resolve()
    paths = resolve_paths(root, args.paths)
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj):
            self._send(code, json.dumps(obj).encode(), "application/json")

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, render_html(build_data(root, paths, server=True)).encode(), "text/html; charset=utf-8")
            elif path == "/api/version":
                self._json(200, {"fingerprint": fingerprint(root, paths), "busy": lock.locked()})
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/api/explain":
                return self._json(404, {"error": "not found"})
            if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{args.port}", f"http://localhost:{args.port}"):
                return self._json(403, {"error": "cross-origin request refused"})
            try:
                req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad JSON"})
            focus = req.get("focus") or {"kind": "overview"}
            if not lock.acquire(blocking=False):
                return self._json(409, {"error": "another explanation is already being generated"})
            try:
                graph = build_data(root, paths)
                new = generate(graph, root, root / OUT_DIR / STORE, focus=focus, count=req.get("count"),
                               agent_cmd=args.agent_cmd, model=args.model)
                graph = build_data(root, paths)
                self._json(200, {"new": [w["id"] for w in new], "walkthroughs": graph["walkthroughs"],
                                 "fingerprint": graph["fingerprint"]})
            except Exception as exc:  # noqa: BLE001 - reported to the viewer
                self._json(500, {"error": str(exc)[:1500]})
            finally:
                lock.release()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"[codemap] serving {root} ({', '.join(paths)}) at {url}  (Ctrl+C to stop)", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m codemap",
                                description="Interactive spatial map of a Python codebase with traced execution "
                                            "paths and AI walkthroughs.")
    p.add_argument("--root", default=".", help="repository root (default: current directory)")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("build", help="analyze and write a self-contained .codemap/index.html")
    b.add_argument("paths", nargs="*", help="files/dirs to map, relative to root (remembered for next time)")
    b.add_argument("--open", action="store_true", help="open the map in a browser")
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("serve", help="serve the live map (rebuilds on change, AI explanations on demand)")
    s.add_argument("paths", nargs="*")
    s.add_argument("--port", type=int, default=8765)
    s.add_argument("--open", action="store_true")
    s.add_argument("--agent-cmd", default=None,
                   help="coding-agent command that reads a prompt on stdin and prints JSON (default: claude -p)")
    s.add_argument("--model", default=None, help="model passed to claude -p")
    s.set_defaults(func=cmd_serve)

    e = sub.add_parser("explain", help="ask the coding agent for AI walkthroughs and store them")
    e.add_argument("paths", nargs="*")
    e.add_argument("--node", default=None, help="explain one function/class id instead of the whole program")
    e.add_argument("--count", type=int, default=None, help="number of overview walkthroughs (default 4)")
    e.add_argument("--agent-cmd", default=None)
    e.add_argument("--model", default=None)
    e.add_argument("--dry-run", action="store_true", help="print the prompt instead of calling the agent")
    e.set_defaults(func=cmd_explain)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)
