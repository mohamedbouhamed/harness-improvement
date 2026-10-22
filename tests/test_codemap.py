import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from codemap.analyze import analyze
from codemap.cli import render_html
from codemap.layout import layout
from codemap.walkthroughs import build_prompt, stamp, verify

FILES = {
    "pkg/__init__.py": '"""Demo package."""\n',
    "pkg/__main__.py": "from .cli import main\n\nmain()\n",
    "pkg/cli.py": '''
        import argparse
        import subprocess

        from . import core
        from .core import Config, Engine


        def cmd_go(args):
            cfg = Config(args.name, retries=2)
            engine = Engine(cfg)
            return engine.start(args.name)


        def cmd_stop(args):
            subprocess.run(["true"])


        def main(argv=None):
            p = argparse.ArgumentParser()
            sub = p.add_subparsers()
            sub.add_parser("go").set_defaults(func=cmd_go)
            sub.add_parser("stop").set_defaults(func=cmd_stop)
            args = p.parse_args(argv)
            return args.func(args)
    ''',
    "pkg/core.py": '''
        import json
        from dataclasses import dataclass


        @dataclass
        class Config:
            name: str
            retries: int = 1

            @property
            def label(self):
                return self.name.upper()


        class Engine:
            def __init__(self, config: Config):
                self.config = config

            def start(self, target):
                total = 0
                for i in range(self.config.retries):
                    if i > 1:
                        raise RuntimeError("too many")
                    else:
                        total += helper(target)
                text = self.config.label

                def emit(extra):
                    return json.dumps([target, extra])

                return emit(total)


        def helper(x):
            return len(x)
    ''',
}


def make_repo(root: Path) -> None:
    for rel, src in FILES.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(src).lstrip("\n"), encoding="utf-8")


class AnalyzeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        make_repo(cls.root)
        cls.g = layout(analyze(cls.root))
        cls.nodes = {n["id"]: n for n in cls.g["nodes"]}
        cls.edges = {(e["s"], e["t"], e["kind"]): e for e in cls.g["edges"]}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def edge(self, s, t, kind="call"):
        return self.edges.get((s, t, kind))

    def test_resolved_calls_through_imports_and_self(self):
        self.assertEqual(self.edge("pkg.core.Engine.start", "pkg.core.helper")["conf"], "resolved")
        self.assertEqual(self.edge("pkg.__main__:<module>", "pkg.cli.main")["conf"], "resolved")

    def test_constructor_and_inferred_method_call(self):
        self.assertIsNotNone(self.edge("pkg.cli.cmd_go", "pkg.core.Config", "instantiate"))
        self.assertIsNotNone(self.edge("pkg.cli.cmd_go", "pkg.core.Engine.__init__", "instantiate"))
        self.assertEqual(self.edge("pkg.cli.cmd_go", "pkg.core.Engine.start")["conf"], "inferred")

    def test_argument_to_parameter_mapping(self):
        go = self.nodes["pkg.cli.cmd_go"]
        cfg_call = next(c for c in go["calls"] if c.get("target") == "pkg.core.Config")
        self.assertEqual(cfg_call["pm"]["pkg.core.Config"], ["name", "retries"])  # dataclass fields
        start = next(c for c in go["calls"] if c.get("target") == "pkg.core.Engine.start")
        self.assertEqual(start["pm"]["pkg.core.Engine.start"], ["target"])  # self skipped
        self.assertEqual(start["to"], ["return"])

    def test_registry_dispatch_is_dynamic(self):
        for target in ("pkg.cli.cmd_go", "pkg.cli.cmd_stop"):
            self.assertEqual(self.edge("pkg.cli.main", target)["conf"], "dynamic")
            self.assertIsNotNone(self.edge("pkg.cli.main", target, "reference"))

    def test_external_effects_and_property_access(self):
        ext = self.edge("pkg.cli.cmd_stop", "ext:subprocess.run", "external")
        self.assertIsNotNone(ext)
        self.assertEqual(self.nodes["ext:subprocess.run"]["effect"], "process")
        self.assertIsNotNone(self.edge("pkg.core.Engine.start", "pkg.core.Config.label"))

    def test_events_capture_control_flow(self):
        start = self.nodes["pkg.core.Engine.start"]
        kinds = [e["k"] for e in start["events"]]
        for k in ("loop", "branch", "raise", "return", "def", "assign"):
            self.assertIn(k, kinds)
        texts = [e["t"] for e in start["events"] if e["k"] == "branch"]
        self.assertIn("else:", texts)
        assign = next(e for e in start["events"] if e["t"].startswith("total += "))
        self.assertIn("total", assign["w"])
        self.assertIn("target", assign["r"])

    def test_nested_function_is_region_inside_host(self):
        emit = self.nodes["pkg.core.Engine.start.emit"]
        host = self.nodes["pkg.core.Engine.start"]
        self.assertTrue(emit["nested"])
        self.assertEqual(emit["host"], host["id"])
        self.assertIn("target", emit["free"])
        self.assertTrue(host["y"] <= emit["y"] <= host["y"] + host["h"])

    def test_entry_point_and_layout_coordinates(self):
        self.assertEqual(self.nodes["pkg.__main__:<module>"]["entry"], "python -m pkg")
        for n in self.g["nodes"]:
            if n["kind"] in ("function", "method", "class", "module", "external", "toplevel"):
                self.assertIn("x", n, n["id"])

    def test_html_embedding_escapes_script_end(self):
        html = render_html({"nodes": [], "note": "</script><b>"})
        self.assertNotIn("</script><b>", html)
        self.assertIn("<\\/script><b>", html)


class WalkthroughCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        make_repo(self.root)
        self.g = analyze(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def raw(self, steps):
        return {"walkthroughs": [{"title": "T", "question": "Q", "summary": "S", "steps": steps}]}

    def step(self, node, a, b):
        return {"node": node, "start_line": a, "end_line": b, "title": "t", "text": "calls [[helper]]"}

    def test_valid_steps_and_verified_link(self):
        stored = stamp(self.g, self.raw([self.step("pkg.cli.cmd_go", 8, 10),
                                         self.step("core.Engine.start", 20, 22)]), {}, None)
        self.assertEqual(stored[0]["steps"][1]["node"], "pkg.core.Engine.start")  # suffix corrected
        w = verify(self.g, {"walkthroughs": stored})[0]
        self.assertEqual(w["problems"], 0)
        self.assertEqual(w["steps"][1]["link"]["status"], "verified")

    def test_bad_node_bad_lines_and_unverified_hop_are_flagged(self):
        stored = stamp(self.g, self.raw([self.step("pkg.core.helper", 34, 35),
                                         self.step("pkg.cli.cmd_stop", 1, 400),
                                         self.step("pkg.nope", 1, 2)]), {}, None)
        w = verify(self.g, {"walkthroughs": stored})[0]
        self.assertEqual(w["steps"][1]["link"]["status"], "unverified")
        self.assertTrue(any(c["kind"] == "lines" and not c["ok"] for c in w["steps"][1]["checks"]))
        self.assertTrue(any(c["kind"] == "node" and not c["ok"] for c in w["steps"][2]["checks"]))
        self.assertEqual(w["problems"], 2)

    def test_stale_after_code_change(self):
        stored = stamp(self.g, self.raw([self.step("pkg.core.helper", 34, 35)]), {}, None)
        core = self.root / "pkg/core.py"
        core.write_text(core.read_text().replace("return len(x)", "return len(x) + 1"))
        w = verify(analyze(self.root), {"walkthroughs": stored})[0]
        self.assertTrue(w["steps"][0].get("stale"))

    def test_prompt_contains_index_relationships_and_source(self):
        prompt, needs_tools = build_prompt(self.g)
        self.assertFalse(needs_tools)
        self.assertIn("pkg.core.Engine.start | method", prompt)
        self.assertIn("pkg.core.Engine.start -> pkg.core.helper (call, resolved", prompt)
        self.assertIn("===== pkg/core.py =====", prompt)
        json.dumps(prompt)


if __name__ == "__main__":
    unittest.main()
