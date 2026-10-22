"""Static analysis: Python sources -> modules, classes, functions, statement events and relationships.

Everything here comes from parsing with `ast`. Nothing is executed and no model is involved, so this
is the *verified* layer of the map. Each call relationship records how it was resolved:

- resolved: follows from names, imports and class definitions alone
- inferred: the receiver's class comes from an annotation or a constructor assignment
- dynamic:  the receiver is unknown; the edge links every project method with that name, or every
            function registered under that keyword (e.g. argparse `set_defaults(func=...)`)
"""
from __future__ import annotations

import ast
import builtins
import hashlib
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

EXCLUDE_DIRS = {".git", ".hg", ".venv", "venv", "env", "node_modules", "__pycache__", ".codemap", "build",
                "dist", ".mypy_cache", ".pytest_cache", ".tox", ".ruff_cache", "site-packages"}
BUILTIN_NAMES = set(dir(builtins))
IO_BUILTINS = {"open", "print", "input", "exit", "quit", "breakpoint"}
MUTATORS = {"append", "extend", "insert", "add", "update", "setdefault", "write", "writelines", "put",
            "appendleft", "discard", "remove", "pop", "clear", "sort", "reverse", "__setitem__"}
MAX_DYNAMIC_TARGETS = 4
MAX_REGISTRY_TARGETS = 24
TEXT_CAP = 140

# External side-effect categories, by dotted-name prefix (first match wins).
EFFECTS = [
    ("urllib", "network"), ("http", "network"), ("socket", "network"), ("requests", "network"),
    ("httpx", "network"), ("aiohttp", "network"), ("ssl", "network"),
    ("subprocess", "process"), ("os.system", "process"), ("os.exec", "process"), ("os.spawn", "process"),
    ("multiprocessing", "process"), ("signal", "process"),
    ("builtins.open", "filesystem"), ("shutil", "filesystem"), ("tempfile", "filesystem"),
    ("pathlib", "filesystem"), ("os.path", "filesystem"), ("os.chmod", "filesystem"),
    ("os.remove", "filesystem"), ("os.unlink", "filesystem"), ("os.makedirs", "filesystem"),
    ("os.mkdir", "filesystem"), ("os.listdir", "filesystem"), ("os.walk", "filesystem"),
    ("os.rename", "filesystem"), ("os.replace", "filesystem"), ("glob", "filesystem"), ("io", "filesystem"),
    ("builtins.print", "output"), ("builtins.input", "input"), ("sys.stdout", "output"),
    ("sys.stderr", "output"), ("logging", "output"), ("sys.exit", "exit"), ("builtins.exit", "exit"),
    ("os.environ", "environment"), ("os.getenv", "environment"), ("dotenv", "environment"),
    ("json", "serialize"), ("pickle", "serialize"), ("csv", "serialize"), ("tomllib", "serialize"),
    ("yaml", "serialize"), ("sqlite3", "database"),
    ("time.sleep", "wait"), ("asyncio.sleep", "wait"),
    ("time", "clock"), ("datetime", "clock"), ("random", "random"), ("uuid", "random"),
    ("secrets", "random"), ("hashlib", "hash"),
]
METHOD_EFFECTS = {
    "read_text": "filesystem", "write_text": "filesystem", "read_bytes": "filesystem",
    "write_bytes": "filesystem", "mkdir": "filesystem", "unlink": "filesystem", "iterdir": "filesystem",
    "glob": "filesystem", "rglob": "filesystem", "rmdir": "filesystem", "touch": "filesystem",
    "write": "output", "writelines": "output", "flush": "output", "read": "input", "readline": "input",
    "send": "network", "recv": "network", "sendall": "network",
}


def effect_of(dotted: str) -> str | None:
    for prefix, effect in EFFECTS:
        if dotted == prefix or dotted.startswith(prefix + "."):
            return effect
    return None


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _text(node: ast.AST, cap: int = TEXT_CAP) -> str:
    try:
        s = ast.unparse(node)
    except Exception:  # noqa: BLE001 - display text only
        s = type(node).__name__
    s = " ".join(s.split())
    return s if len(s) <= cap else s[: cap - 1] + "…"


# ---------------------------------------------------------------------------
# Symbol tables
# ---------------------------------------------------------------------------

@dataclass
class ModuleInfo:
    id: str
    rel: str
    tree: ast.Module
    package: str
    defs: dict = field(default_factory=dict)       # name -> node id (top-level function/class)
    imports: dict = field(default_factory=dict)    # alias -> ("mod", id) | ("sym", mod, name) | ("ext", dotted)
    var_types: dict = field(default_factory=dict)  # name -> class id (module-level X = Cls(...))
    aliases: dict = field(default_factory=dict)    # name -> value descriptor (module-level X = some.callable)


@dataclass
class FuncInfo:
    id: str
    node: ast.AST
    module: str
    cls: str | None       # owning class for methods
    parent: str | None    # enclosing function for nested defs
    params: list


@dataclass
class ClassInfo:
    id: str
    node: ast.ClassDef
    module: str
    methods: dict = field(default_factory=dict)     # name -> fn id
    bases: list = field(default_factory=list)       # project class ids
    fields: list = field(default_factory=list)      # dataclass-style annotated fields (params)
    is_dataclass: bool = False
    attr_types: dict = field(default_factory=dict)  # self.attr -> class id


class Ctx:
    """Name-resolution scope for one function body (or a module's top level when fn is None)."""

    def __init__(self, module: ModuleInfo, fn: FuncInfo | None = None, parent: "Ctx | None" = None):
        self.module, self.fn, self.parent = module, fn, parent
        self.cls = fn.cls if fn else None
        self.self_name = None
        self.local_names: set[str] = set()
        self.local_defs: dict[str, str] = {}
        self.local_imports: dict[str, tuple] = {}
        self.types: dict[str, tuple] = {}  # name -> ("inst", cls, conf) | ("ext", dotted)

    def chain(self):
        c = self
        while c is not None and c.fn is not None:
            yield c
            c = c.parent


def iter_params(fn: ast.AST) -> list[dict]:
    a = fn.args
    positional = list(a.posonlyargs) + list(a.args)
    defaults = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
    out = []
    for i, p in enumerate(positional):
        out.append({"n": p.arg, "a": _text(p.annotation, 60) if p.annotation else None,
                    "d": _text(defaults[i], 40) if defaults[i] is not None else None,
                    "k": "posonly" if i < len(a.posonlyargs) else "pos"})
    if a.vararg:
        out.append({"n": a.vararg.arg, "a": None, "d": None, "k": "var"})
    for p, d in zip(a.kwonlyargs, a.kw_defaults):
        out.append({"n": p.arg, "a": _text(p.annotation, 60) if p.annotation else None,
                    "d": _text(d, 40) if d is not None else None, "k": "kwonly"})
    if a.kwarg:
        out.append({"n": a.kwarg.arg, "a": None, "d": None, "k": "varkw"})
    return out


def _decorator_names(node) -> list[str]:
    out = []
    for d in getattr(node, "decorator_list", []):
        target = d.func if isinstance(d, ast.Call) else d
        out.append(_text(target, 60))
    return out


def iter_nested_defs(body):
    """Function/class definitions inside a body, not descending into other definitions."""
    for st in body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield st
            continue
        for name in ("body", "orelse", "finalbody", "handlers", "cases"):
            sub = getattr(st, name, None)
            if isinstance(sub, list):
                yield from iter_nested_defs(sub)


def target_names(t, self_name=None) -> list[str]:
    """Names written by an assignment target. Attribute/subscript targets mutate their base."""
    if isinstance(t, ast.Name):
        return [t.id]
    if isinstance(t, (ast.Tuple, ast.List)):
        return [n for e in t.elts for n in target_names(e, self_name)]
    if isinstance(t, ast.Starred):
        return target_names(t.value, self_name)
    if isinstance(t, (ast.Attribute, ast.Subscript)):
        base = t.value
        if isinstance(t, ast.Attribute) and isinstance(base, ast.Name) and base.id == self_name:
            return [f"{self_name}.{t.attr}"]
        while isinstance(base, (ast.Attribute, ast.Subscript)):
            if isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name) and base.value.id == self_name:
                return [f"{self_name}.{base.attr}"]
            base = base.value
        if isinstance(base, ast.Name):
            return [base.id]
    return []


def read_names(exprs, self_name=None) -> list[str]:
    out: list[str] = []
    seen = set()
    for e in exprs:
        if e is None:
            continue
        for n in ast.walk(e):
            name = None
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == self_name \
                    and isinstance(n.ctx, ast.Load):
                name = f"{self_name}.{n.attr}"
            elif isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load):
                name = n.id
            if name and name not in seen:
                seen.add(name)
                out.append(name)
    return out


# ---------------------------------------------------------------------------
# Analyzer
# ---------------------------------------------------------------------------

class Analyzer:
    def __init__(self, root: Path, paths: list[Path] | None = None):
        self.root = root.resolve()
        self.paths = [p.resolve() for p in (paths or [self.root])]
        self.modules: dict[str, ModuleInfo] = {}
        self.files: dict[str, dict] = {}
        self.funcs: dict[str, FuncInfo] = {}
        self.classes: dict[str, ClassInfo] = {}
        self.nodes: dict[str, dict] = {}
        self.ext_nodes: dict[str, dict] = {}
        self.edges: dict[tuple, dict] = {}
        self.registry: dict[str, set] = {}  # keyword/attribute name -> functions registered under it
        self.pending_unresolved: list[tuple[str, dict]] = []
        self.ctx_cache: dict[str, Ctx] = {}
        self.methods_by_name: dict[str, list[str]] = {}
        self.errors: list[dict] = []

    # -- discovery ----------------------------------------------------------
    def discover(self) -> list[Path]:
        found = []
        for base in self.paths:
            if base.is_file() and base.suffix == ".py":
                found.append(base)
                continue
            for p in sorted(base.rglob("*.py")):
                rel_parts = p.relative_to(self.root).parts
                if any(part in EXCLUDE_DIRS or part.startswith(".") for part in rel_parts[:-1]):
                    continue
                found.append(p)
        return sorted(set(found))

    @staticmethod
    def module_id(rel: Path) -> str:
        return ".".join(rel.with_suffix("").parts)

    def canonical_module(self, dotted: str | None) -> str | None:
        if not dotted:
            return None
        if dotted in self.modules:
            return dotted
        if dotted + ".__init__" in self.modules:
            return dotted + ".__init__"
        return None

    # -- pass 1: symbols ----------------------------------------------------
    def load(self):
        for path in self.discover():
            rel = path.relative_to(self.root)
            text = path.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(text, filename=str(rel))
            except SyntaxError as exc:
                self.errors.append({"file": rel.as_posix(), "error": f"line {exc.lineno}: {exc.msg}"})
                continue
            mid = self.module_id(rel)
            parts = mid.split(".")
            package = ".".join(parts[:-1]) if parts[-1] != "__init__" else ".".join(parts[:-1])
            self.modules[mid] = ModuleInfo(mid, rel.as_posix(), tree, package)
            self.files[rel.as_posix()] = {"path": rel.as_posix(), "abs": str(path), "module": mid,
                                          "lines": text.expandtabs(4).splitlines(), "sha": sha(text)}
        for m in self.modules.values():
            self.collect_imports(m, m.tree.body, m.imports)
            for st in iter_nested_defs(m.tree.body):
                if isinstance(st, ast.ClassDef):
                    self.register_class(st, m, None)
                else:
                    self.register_function(st, m, None, None, prefix=m.id)

    def resolve_from(self, m: ModuleInfo, module: str | None, level: int) -> str | None:
        if level == 0:
            return module
        base = m.package.split(".") if m.package else []
        if level > 1:
            base = base[: len(base) - (level - 1)] if len(base) >= level - 1 else None
            if base is None:
                return None
        parts = base + (module.split(".") if module else [])
        return ".".join(parts)

    def collect_imports(self, m: ModuleInfo, body, table: dict):
        for st in body:
            if isinstance(st, ast.Import):
                for a in st.names:
                    if a.asname:
                        name, target = a.asname, a.name
                    else:
                        name = target = a.name.split(".")[0]
                    mod = self.canonical_module(target)
                    table[name] = ("mod", mod) if mod else ("ext", target)
            elif isinstance(st, ast.ImportFrom):
                base = self.resolve_from(m, st.module, st.level)
                base_mod = self.canonical_module(base)
                for a in st.names:
                    if a.name == "*":
                        continue
                    name = a.asname or a.name
                    sub = self.canonical_module(f"{base}.{a.name}") if base else None
                    if sub:
                        table[name] = ("mod", sub)
                    elif base_mod:
                        table[name] = ("sym", base_mod, a.name)
                    else:
                        table[name] = ("ext", f"{base}.{a.name}" if base else a.name)
            elif isinstance(st, (ast.If, ast.Try)) and not isinstance(st, (ast.FunctionDef, ast.ClassDef)):
                for sub in ("body", "orelse", "finalbody"):
                    self.collect_imports(m, getattr(st, sub, []) or [], table)
                for h in getattr(st, "handlers", []):
                    self.collect_imports(m, h.body, table)

    def register_class(self, node: ast.ClassDef, m: ModuleInfo, parent_cls: str | None):
        cid = f"{parent_cls or m.id}.{node.name}"
        info = ClassInfo(cid, node, m.id)
        info.is_dataclass = any(d.split(".")[-1] == "dataclass" for d in _decorator_names(node))
        self.classes[cid] = info
        if parent_cls is None:
            m.defs[node.name] = cid
        for st in node.body:
            if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                ann = _text(st.annotation, 60)
                if "ClassVar" not in ann:
                    info.fields.append({"n": st.target.id, "a": ann,
                                        "d": _text(st.value, 40) if st.value is not None else None, "k": "pos"})
        for st in iter_nested_defs(node.body):
            if isinstance(st, ast.ClassDef):
                self.register_class(st, m, cid)
            else:
                fid = self.register_function(st, m, cid, None, prefix=cid)
                info.methods[st.name] = fid
                self.methods_by_name.setdefault(st.name, []).append(fid)

    def register_function(self, node, m: ModuleInfo, cls: str | None, parent: str | None, prefix: str) -> str:
        fid = f"{prefix}.{node.name}"
        self.funcs[fid] = FuncInfo(fid, node, m.id, cls, parent, iter_params(node))
        if cls is None and parent is None:
            m.defs[node.name] = fid
        for st in iter_nested_defs(node.body):
            if isinstance(st, ast.ClassDef):
                continue  # classes defined inside functions are shown as code only
            self.register_function(st, m, None, fid, prefix=fid)
        return fid

    # -- value resolution ---------------------------------------------------
    def entry_value(self, entry: tuple, depth: int = 0) -> tuple:
        kind = entry[0]
        if kind == "mod":
            return ("mod", entry[1])
        if kind == "ext":
            return ("ext", entry[1])
        if kind == "sym":
            return self.member(entry[1], entry[2], depth + 1)
        return ("unknown",)

    def member(self, mod_id: str, name: str, depth: int = 0) -> tuple:
        m = self.modules.get(mod_id)
        if m is None or depth > 8:
            return ("unknown",)
        if name in m.defs:
            target = m.defs[name]
            return ("cls", target) if target in self.classes else ("fn", target)
        if name in m.var_types:
            return ("inst", m.var_types[name], "inferred")
        if name in m.aliases:
            return m.aliases[name]
        if name in m.imports:
            return self.entry_value(m.imports[name], depth)
        sub = self.canonical_module(f"{m.package}.{name}" if mod_id.endswith(".__init__") else None)
        if sub:
            return ("mod", sub)
        return ("unknown",)

    def class_member(self, cid: str, name: str, seen=None) -> str | None:
        info = self.classes.get(cid)
        if info is None:
            return None
        if name in info.methods:
            return info.methods[name]
        seen = seen or set()
        seen.add(cid)
        for b in info.bases:
            if b not in seen:
                r = self.class_member(b, name, seen)
                if r:
                    return r
        return None

    def class_attr_type(self, cid: str, attr: str) -> str | None:
        info = self.classes.get(cid)
        while info is not None:
            if attr in info.attr_types:
                return info.attr_types[attr]
            info = self.classes.get(info.bases[0]) if info.bases else None
        return None

    def value(self, e, ctx: Ctx) -> tuple:
        if isinstance(e, ast.Name):
            n = e.id
            for c in ctx.chain():
                if n in c.local_defs:
                    return ("fn", c.local_defs[n])
                if n in c.types:
                    return c.types[n]
                if n in c.local_imports:
                    return self.entry_value(c.local_imports[n])
                if n == c.self_name and c.cls:
                    return ("inst", c.cls, "resolved")
                if n in c.local_names:
                    return ("unknown",)
            m = ctx.module
            if n in m.defs:
                t = m.defs[n]
                return ("cls", t) if t in self.classes else ("fn", t)
            if n in m.var_types:
                return ("inst", m.var_types[n], "inferred")
            if n in m.aliases:
                return m.aliases[n]
            if n in m.imports:
                return self.entry_value(m.imports[n])
            if n in BUILTIN_NAMES:
                return ("builtin", n)
            return ("unknown",)
        if isinstance(e, ast.Attribute):
            base = self.value(e.value, ctx)
            k = base[0]
            if k == "mod":
                return self.member(base[1], e.attr)
            if k == "cls":
                fid = self.class_member(base[1], e.attr)
                if fid:
                    return ("fn", fid)
                if f"{base[1]}.{e.attr}" in self.classes:
                    return ("cls", f"{base[1]}.{e.attr}")
                return ("unknown_attr", e.attr)
            if k == "inst":
                fid = self.class_member(base[1], e.attr)
                if fid:
                    return ("method", fid, base[2])
                t = self.class_attr_type(base[1], e.attr)
                if t:
                    return ("inst", t, "inferred")
                return ("unknown_attr", e.attr)
            if k == "multi":
                fids = [f for f in (self.class_member(c, e.attr) for c in base[1]) if f]
                return ("multi_method", sorted(set(fids)), base[1]) if fids else ("unknown_attr", e.attr)
            if k == "ext":
                return ("ext", f"{base[1]}.{e.attr}")
            if k == "builtin":
                return ("builtin", f"{base[1]}.{e.attr}")
            return ("unknown_attr", e.attr)
        if isinstance(e, ast.Call):
            f = self.value(e.func, ctx)
            if f[0] == "cls":
                return ("inst", f[1], "inferred")
            if f[0] == "ext":
                return ("ext", f[1])
            if f[0] in ("fn", "method"):
                ret = self.return_class(f[1])
                if ret:
                    return ("inst", ret, "inferred")
            if f[0] == "builtin" and isinstance(e.func, ast.Name):
                return ("builtin", f[1])
            return ("unknown",)
        if isinstance(e, ast.Constant) and isinstance(e.value, str):
            return ("builtin", "str")
        if isinstance(e, ast.JoinedStr):
            return ("builtin", "str")
        if isinstance(e, ast.Await):
            return self.value(e.value, ctx)
        return ("unknown",)

    def annotation_class(self, ann, ctx: Ctx) -> str | None:
        if ann is None:
            return None
        if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
            try:
                ann = ast.parse(ann.value, mode="eval").body
            except SyntaxError:
                return None
        if isinstance(ann, ast.BinOp):  # X | None
            return self.annotation_class(ann.left, ctx) or self.annotation_class(ann.right, ctx)
        if isinstance(ann, ast.Subscript) and _text(ann.value).split(".")[-1] == "Optional":
            return self.annotation_class(ann.slice, ctx)
        if isinstance(ann, (ast.Name, ast.Attribute)):
            v = self.value(ann, ctx)
            if v[0] == "cls":
                return v[1]
        return None

    def return_class(self, fid: str) -> str | None:
        info = self.funcs.get(fid)
        if info is None or getattr(info.node, "returns", None) is None:
            return None
        return self.annotation_class(info.node.returns, self.ctx_for(fid))

    # -- contexts -----------------------------------------------------------
    def ctx_for(self, fid: str) -> Ctx:
        if fid in self.ctx_cache:
            return self.ctx_cache[fid]
        info = self.funcs[fid]
        m = self.modules[info.module]
        parent = self.ctx_for(info.parent) if info.parent else None
        ctx = Ctx(m, info, parent)
        self.ctx_cache[fid] = ctx
        decos = [d.split(".")[-1] for d in _decorator_names(info.node)]
        if info.cls and info.params and "staticmethod" not in decos:
            ctx.self_name = info.params[0]["n"]
        ctx.local_names.update(p["n"] for p in info.params)
        self._scan_bindings(info.node.body, ctx)
        for st in iter_nested_defs(info.node.body):
            if not isinstance(st, ast.ClassDef):
                ctx.local_defs[st.name] = f"{fid}.{st.name}"
        # Types: parameter annotations, then constructor / annotated / typed-return assignments.
        a = info.node.args
        for p in list(a.posonlyargs) + list(a.args) + list(a.kwonlyargs):
            if p.arg == ctx.self_name or p.annotation is None:
                continue
            cls = self.annotation_class(p.annotation, ctx)
            if cls:
                ctx.types[p.arg] = ("inst", cls, "inferred")
                continue
            ann = p.annotation.value if isinstance(p.annotation, ast.Subscript) else p.annotation
            if isinstance(ann, (ast.Name, ast.Attribute)):
                v = self.value(ann, ctx)
                if v[0] == "ext":
                    ctx.types[p.arg] = ("ext", v[1] + "()")
                elif v[0] == "builtin":
                    ctx.types[p.arg] = ("builtin", v[1])
        self._scan_types(info.node.body, ctx)
        return ctx

    def _scan_bindings(self, body, ctx: Ctx):
        for st in body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                ctx.local_names.add(st.name)
                continue
            if isinstance(st, (ast.Import, ast.ImportFrom)):
                self.collect_imports(ctx.module, [st], ctx.local_imports)
            for n in ast.walk(st):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    ctx.local_names.add(n.id)
                elif isinstance(n, ast.ExceptHandler) and n.name:
                    ctx.local_names.add(n.name)

    def _scan_types(self, body, ctx: Ctx):
        for st in body:
            if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            if isinstance(st, ast.AnnAssign) and isinstance(st.target, ast.Name):
                cls = self.annotation_class(st.annotation, ctx)
                if cls:
                    ctx.types[st.target.id] = ("inst", cls, "inferred")
            elif isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                v = self.value(st.value, ctx) if isinstance(st.value, ast.Call) else ("unknown",)
                name = st.targets[0].id
                if v[0] == "inst":
                    prev = ctx.types.get(name)
                    if prev is None or prev == ("inst", v[1], "inferred"):
                        ctx.types[name] = ("inst", v[1], "inferred")
                    elif prev[0] in ("inst", "multi"):
                        classes = set(prev[1]) if prev[0] == "multi" else {prev[1]}
                        ctx.types[name] = ("multi", tuple(sorted(classes | {v[1]})))
                elif v[0] == "ext":
                    ctx.types.setdefault(name, ("ext", v[1] + "()"))
            elif isinstance(st, (ast.With, ast.AsyncWith)):
                for item in st.items:
                    if isinstance(item.optional_vars, ast.Name):
                        v = self.value(item.context_expr, ctx)
                        if v[0] == "ext":
                            ctx.types[item.optional_vars.id] = ("ext", v[1] + "()")
                        elif v[0] == "builtin" and v[1] == "open":
                            ctx.types[item.optional_vars.id] = ("ext", "builtins.open()")
            for sub in ("body", "orelse", "finalbody"):
                inner = getattr(st, sub, None)
                if isinstance(inner, list):
                    self._scan_types(inner, ctx)
            for h in getattr(st, "handlers", []) or []:
                if h.name and h.type is not None:
                    types = h.type.elts if isinstance(h.type, ast.Tuple) else [h.type]
                    v = self.value(types[0], ctx)
                    if v[0] == "ext":
                        ctx.types[h.name] = ("ext", v[1] + "()")
                    elif v[0] == "cls":
                        ctx.types[h.name] = ("inst", v[1], "inferred")
                self._scan_types(h.body, ctx)

    def module_ctx(self, m: ModuleInfo) -> Ctx:
        return Ctx(m, None, None)

    # -- pass 1.5: bases, module var types, self attribute types -------------
    def link_types(self):
        for info in self.classes.values():
            mctx = self.module_ctx(self.modules[info.module])
            for b in info.node.bases:
                v = self.value(b, mctx)
                if v[0] == "cls":
                    info.bases.append(v[1])
        for m in self.modules.values():
            mctx = self.module_ctx(m)
            for st in m.tree.body:
                if isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name):
                    name = st.targets[0].id
                    if isinstance(st.value, ast.Call):
                        v = self.value(st.value, mctx)
                        if v[0] == "inst":
                            m.var_types[name] = v[1]
                    elif isinstance(st.value, (ast.Name, ast.Attribute)):
                        v = self.value(st.value, mctx)
                        if v[0] in ("fn", "ext", "cls"):
                            m.aliases[name] = v
        for fid, info in self.funcs.items():
            if not info.cls:
                continue
            ctx = self.ctx_for(fid)
            if not ctx.self_name:
                continue
            for n in ast.walk(info.node):
                if isinstance(n, ast.Assign) and len(n.targets) == 1:
                    t = n.targets[0]
                    if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) and t.value.id == ctx.self_name:
                        v = self.value(n.value, ctx) if isinstance(n.value, (ast.Call, ast.Name)) else ("unknown",)
                        if v[0] == "inst":
                            self.classes[info.cls].attr_types.setdefault(t.attr, v[1])

    # -- pass 2: events and calls -------------------------------------------
    def params_for_target(self, target: str) -> tuple[list[dict], bool]:
        """(params, skip_first) for argument mapping into `target`."""
        if target in self.classes:
            info = self.classes[target]
            init = self.class_member(target, "__init__")
            if init:
                return self.funcs[init].params, True
            return (info.fields if info.is_dataclass else []), False
        info = self.funcs.get(target)
        if info is None:
            return [], False
        decos = [d.split(".")[-1] for d in _decorator_names(info.node)]
        skip = bool(info.cls) and "staticmethod" not in decos
        return info.params, skip

    @staticmethod
    def map_args(call: ast.Call, params: list[dict], skip_first: bool) -> list[str | None]:
        ps = params[1:] if skip_first else params
        positional = [p for p in ps if p["k"] in ("posonly", "pos")]
        var = next((p for p in ps if p["k"] == "var"), None)
        varkw = next((p for p in ps if p["k"] == "varkw"), None)
        names = {p["n"] for p in ps if p["k"] in ("pos", "kwonly")}
        out: list[str | None] = []
        for i, a in enumerate(call.args):
            if isinstance(a, ast.Starred):
                out.append(None)
            elif i < len(positional):
                out.append(positional[i]["n"])
            else:
                out.append(var["n"] if var else None)
        for kw in call.keywords:
            if kw.arg is None:
                out.append(None)
            elif kw.arg in names:
                out.append(kw.arg)
            else:
                out.append(varkw["n"] if varkw else None)
        return out

    def resolve_call(self, call: ast.Call, ctx: Ctx) -> dict:
        v = self.value(call.func, ctx)
        k = v[0]
        if k == "fn":
            return {"kind": "call", "target": v[1], "conf": "resolved"}
        if k == "method":
            return {"kind": "call", "target": v[1], "conf": v[2]}
        if k == "cls":
            return {"kind": "instantiate", "target": self.class_member(v[1], "__init__") or v[1],
                    "cls": v[1], "conf": "resolved"}
        if k == "ext":
            return {"kind": "external", "name": v[1].replace("()", ""), "on_instance": "()" in v[1]}
        if k == "builtin":
            name = v[1]
            if name in IO_BUILTINS:
                return {"kind": "external", "name": f"builtins.{name}"}
            return {"kind": "builtin", "name": name}
        if k == "multi_method":
            names = ", ".join(c.rsplit(".", 1)[-1] for c in v[2])
            if len(v[1]) == 1:
                return {"kind": "call", "target": v[1][0], "conf": "inferred"}
            return {"kind": "dynamic", "cands": v[1], "via": f"variable may hold {names}", "attr": ""}
        if k == "unknown_attr":
            attr = v[1]
            cands = [] if attr.startswith("__") else self.methods_by_name.get(attr, [])
            if 0 < len(cands) <= MAX_DYNAMIC_TARGETS:
                return {"kind": "dynamic", "cands": sorted(cands), "via": f"any project method named .{attr}()",
                        "attr": attr}
            return {"kind": "unresolved", "attr": attr}
        return {"kind": "unresolved", "attr": _text(call.func, 40)}

    def analyze_body(self, owner: dict, body, ctx: Ctx, src_lines: list[str]):
        events, calls = owner["events"], owner["calls"]
        sn = ctx.self_name

        def line_text(ln: int) -> str:
            return src_lines[ln - 1].strip() if 0 < ln <= len(src_lines) else ""

        def header(st) -> tuple[int, int, str]:
            first = st.lineno
            body0 = getattr(st, "body", None)
            if isinstance(body0, list) and body0 and not isinstance(st, ast.ClassDef):
                end = max(first, body0[0].lineno - 1)
            else:
                end = getattr(st, "end_lineno", first) or first
            t = line_text(first)
            if end > first and not isinstance(body0, list):
                t += " …"
            return first, end, t[:TEXT_CAP]

        def find_kw_line(start: int, stop: int, kw: str) -> int | None:
            pat = re.compile(rf"^\s*{kw}\b")
            for ln in range(start, min(stop, len(src_lines)) + 1):
                if pat.match(src_lines[ln - 1]):
                    return ln
            return None

        def add_calls(exprs, ev_index: int, assign_targets: list[str] | None, stmt) -> tuple[list[int], list[str]]:
            """Record call sites in `exprs`. Returns (call indices, extra written names from mutators)."""
            found = []
            func_nodes = set()
            for e in exprs:
                if e is None:
                    continue
                for n in ast.walk(e):
                    if isinstance(n, ast.Call):
                        found.append(n)
                        for sub in ast.walk(n.func):
                            func_nodes.add(id(sub))
            found.sort(key=lambda c: (c.lineno, c.col_offset))
            idxs, mut = [], []
            direct_value = getattr(stmt, "value", None)
            if isinstance(direct_value, ast.Await):
                direct_value = direct_value.value
            for c in found:
                r = self.resolve_call(c, ctx)
                arg_nodes = list(c.args) + [kw.value for kw in c.keywords]
                rec = {"l": c.lineno, "e": c.end_lineno or c.lineno, "ev": ev_index, "text": _text(c.func, 60),
                       "kind": r["kind"],
                       "args": [{"x": _text(a, 60), "kw": None, "r": read_names([a], sn)} for a in c.args] +
                               [{"x": _text(kw.value, 60), "kw": kw.arg, "r": read_names([kw.value], sn)}
                                for kw in c.keywords]}
                if isinstance(c.func, ast.Attribute):
                    rec["recv"] = {"x": _text(c.func.value, 60), "r": read_names([c.func.value], sn)}
                    if c.func.attr in MUTATORS:
                        mut += target_names(ast.Attribute(value=c.func.value, attr="_", ctx=ast.Store()), sn)
                if c is direct_value and assign_targets:
                    rec["to"] = assign_targets
                if isinstance(stmt, ast.Return) and c is direct_value:
                    rec["to"] = ["return"]
                if r["kind"] in ("call", "instantiate"):
                    rec["target"], rec["conf"] = r["target"], r["conf"]
                    params, skip = self.params_for_target(r["cls"] if r["kind"] == "instantiate" else r["target"])
                    rec["pm"] = {r["target"]: self.map_args(c, params, skip)}
                elif r["kind"] == "dynamic":
                    rec["cands"], rec["via"], rec["conf"] = r["cands"], r["via"], "dynamic"
                    rec["pm"] = {t: self.map_args(c, *self.params_for_target(t)) for t in r["cands"]}
                elif r["kind"] == "external":
                    rec["name"] = r["name"]
                    rec["effect"] = effect_of(r["name"])
                    if r.get("on_instance") and isinstance(c.func, ast.Attribute):
                        # e.g. args.func(args) where args came from an external parser: may be a registered callback
                        self.pending_unresolved.append((owner["id"], rec, c, ctx))
                elif r["kind"] == "builtin":
                    rec["name"] = r["name"]
                else:
                    rec["attr"] = r["attr"]
                    eff = METHOD_EFFECTS.get(r["attr"])
                    if eff:
                        rec["effect"] = eff
                    if isinstance(c.func, ast.Attribute):
                        self.pending_unresolved.append((owner["id"], rec, c, ctx))
                calls.append(rec)
                idxs.append(len(calls) - 1)
                # keyword-registered callables, e.g. parser.set_defaults(func=cmd_run)
                for kw in c.keywords:
                    if kw.arg and isinstance(kw.value, (ast.Name, ast.Attribute)):
                        kv = self.value(kw.value, ctx)
                        if kv[0] in ("fn", "method"):
                            self.registry.setdefault(kw.arg, set()).add(kv[1])
            # references: functions used as values (callbacks), not called here
            for e in exprs:
                if e is None:
                    continue
                for n in ast.walk(e):
                    if id(n) in func_nodes or not isinstance(n, (ast.Name, ast.Attribute)):
                        continue
                    if not isinstance(getattr(n, "ctx", None), ast.Load):
                        continue
                    v = self.value(n, ctx)
                    if v[0] in ("fn", "method") and v[1] != owner["id"]:
                        info = self.funcs.get(v[1])
                        if isinstance(n, ast.Attribute) and info and "property" in [
                                d.split(".")[-1] for d in _decorator_names(info.node)]:
                            # property access runs the getter: record it as a call
                            calls.append({"l": n.lineno, "e": n.end_lineno or n.lineno, "ev": ev_index,
                                          "text": _text(n, 60), "kind": "call", "target": v[1],
                                          "conf": v[2] if v[0] == "method" else "resolved", "prop": True,
                                          "args": [], "recv": {"x": _text(n.value, 60),
                                                               "r": read_names([n.value], sn)},
                                          "pm": {v[1]: []}})
                            idxs.append(len(calls) - 1)
                        else:
                            self.add_edge(owner["id"], v[1], "reference", "resolved", n.lineno)
            return idxs, mut

        def emit(kind, st, depth, reads_exprs, writes, text=None, line=None, end=None, assign_targets=None):
            ln, en, t = header(st) if st is not None else (line, end or line, text or "")
            if line is not None:
                ln, en = line, end or line
            if text is not None:
                t = text
            ev = {"k": kind, "l": ln, "e": en, "d": depth, "t": t,
                  "r": read_names(reads_exprs, sn), "w": list(dict.fromkeys(writes)), "c": []}
            events.append(ev)
            if reads_exprs:
                idxs, mut = add_calls(reads_exprs, len(events) - 1, assign_targets, st)
                ev["c"] = idxs
                for w in mut:
                    if w not in ev["w"]:
                        ev["w"].append(w)
            return ev

        def walk(stmts, depth, first_in_body=True):
            for i, st in enumerate(stmts):
                if i == 0 and first_in_body and isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant) \
                        and isinstance(st.value.value, str):
                    continue  # docstring
                if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if ctx.fn is not None:
                        emit("def", None, depth, [], [st.name], text=line_text(st.lineno),
                             line=st.lineno, end=st.lineno)
                    continue
                if isinstance(st, ast.If):
                    walk_if(st, depth, False)
                elif isinstance(st, (ast.For, ast.AsyncFor)):
                    emit("loop", st, depth, [st.iter], target_names(st.target, sn))
                    walk(st.body, depth + 1, False)
                    walk_else(st, depth)
                elif isinstance(st, ast.While):
                    emit("loop", st, depth, [st.test], [])
                    walk(st.body, depth + 1, False)
                    walk_else(st, depth)
                elif isinstance(st, (ast.Try, getattr(ast, "TryStar", ast.Try))):
                    emit("try", st, depth, [], [])
                    walk(st.body, depth + 1, False)
                    for h in st.handlers:
                        emit("except", h, depth, [h.type] if h.type else [], [h.name] if h.name else [])
                        walk(h.body, depth + 1, False)
                    walk_else(st, depth)
                    if st.finalbody:
                        ln = find_kw_line((st.handlers[-1].end_lineno if st.handlers else st.body[-1].end_lineno) or 0,
                                          st.finalbody[0].lineno, "finally") or st.finalbody[0].lineno
                        emit("try", None, depth, [], [], text="finally:", line=ln)
                        walk(st.finalbody, depth + 1, False)
                elif isinstance(st, (ast.With, ast.AsyncWith)):
                    writes = [w for item in st.items if item.optional_vars is not None
                              for w in target_names(item.optional_vars, sn)]
                    emit("with", st, depth, [item.context_expr for item in st.items], writes)
                    walk(st.body, depth + 1, False)
                elif isinstance(st, ast.Match):
                    emit("branch", st, depth, [st.subject], [])
                    for case in st.cases:
                        ln = case.pattern.lineno
                        emit("branch", None, depth + 1, [case.guard] if case.guard else [], [],
                             text=line_text(ln), line=ln)
                        walk(case.body, depth + 2, False)
                elif isinstance(st, ast.Return):
                    emit("return", st, depth, [st.value] if st.value else [], [])
                elif isinstance(st, ast.Raise):
                    emit("raise", st, depth, [st.exc, st.cause], [])
                elif isinstance(st, ast.Assign):
                    writes = [w for t in st.targets for w in target_names(t, sn)]
                    extra = [t.value for t in st.targets if isinstance(t, (ast.Subscript, ast.Attribute))]
                    emit("assign", st, depth, [st.value] + extra, writes, assign_targets=writes)
                elif isinstance(st, ast.AugAssign):
                    writes = target_names(st.target, sn)
                    emit("assign", st, depth, [st.value, st.target], writes, assign_targets=writes)
                elif isinstance(st, ast.AnnAssign):
                    writes = target_names(st.target, sn)
                    emit("assign", st, depth, [st.value] if st.value else [], writes, assign_targets=writes)
                elif isinstance(st, ast.Delete):
                    emit("assign", st, depth, [], [w for t in st.targets for w in target_names(t, sn)])
                elif isinstance(st, ast.Expr):
                    v = st.value.value if isinstance(st.value, ast.Await) else st.value
                    kind = "yield" if isinstance(v, (ast.Yield, ast.YieldFrom)) else \
                        "call" if isinstance(v, ast.Call) else "expr"
                    emit(kind, st, depth, [st.value], [])
                elif isinstance(st, ast.Assert):
                    emit("assert", st, depth, [st.test, st.msg], [])
                elif isinstance(st, (ast.Import, ast.ImportFrom)):
                    names = [a.asname or a.name.split(".")[0] for a in st.names]
                    emit("import", st, depth, [], names if ctx.fn is not None else [])
                elif isinstance(st, (ast.Break, ast.Continue)):
                    emit("jump", st, depth, [], [])
                # Pass / Global / Nonlocal: nothing to show

        def walk_if(st: ast.If, depth: int, is_elif: bool):
            emit("branch", st, depth, [st.test], [])
            walk(st.body, depth + 1, False)
            if not st.orelse:
                return
            first = st.orelse[0]
            if len(st.orelse) == 1 and isinstance(first, ast.If) and line_text(first.lineno).startswith("elif"):
                walk_if(first, depth, True)
                return
            ln = find_kw_line(st.body[-1].end_lineno or st.body[-1].lineno, first.lineno, "else")
            emit("branch", None, depth, [], [], text="else:", line=ln or first.lineno)
            walk(st.orelse, depth + 1, False)

        def walk_else(st, depth: int):
            if getattr(st, "orelse", None):
                start = (st.handlers[-1].end_lineno if getattr(st, "handlers", None) else st.body[-1].end_lineno)
                ln = find_kw_line(start or st.lineno, st.orelse[0].lineno, "else")
                emit("branch", None, depth, [], [], text="else:", line=ln or st.orelse[0].lineno)
                walk(st.orelse, depth + 1, False)

        walk(body, 0, True)

    def add_edge(self, src: str, dst: str, kind: str, conf: str, line: int | None, via: str | None = None):
        key = (src, dst, kind)
        rank = {"resolved": 3, "inferred": 2, "dynamic": 1}
        e = self.edges.get(key)
        if e is None:
            e = self.edges[key] = {"s": src, "t": dst, "kind": kind, "conf": conf, "lines": []}
            if via:
                e["via"] = via
        elif rank.get(conf, 0) > rank.get(e["conf"], 0):
            e["conf"] = conf
        if line is not None and line not in e["lines"]:
            e["lines"].append(line)

    def ext_node(self, name: str) -> str:
        nid = "ext:" + name
        if nid not in self.ext_nodes:
            top = name.split(".")[0]
            self.ext_nodes[nid] = {"id": nid, "kind": "external", "name": name, "group": top,
                                   "effect": effect_of(name),
                                   "stdlib": top in sys.stdlib_module_names or top == "builtins"}
        return nid

    def build_nodes(self):
        for mid, m in sorted(self.modules.items()):
            rel = m.rel
            src = self.files[rel]["lines"]
            pkg_id = "pkg:" + (m.package or ".")
            self.nodes[mid] = {"id": mid, "kind": "module", "name": rel.split("/")[-1], "qual": mid,
                               "module": mid, "file": rel, "line": 1, "end": len(src), "parent": pkg_id,
                               "doc": (ast.get_docstring(m.tree) or "").split("\n\n")[0][:400]}
            # Module top level (imports, constants, entry code) as its own code block.
            top_stmts = [st for st in m.tree.body if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef,
                                                                          ast.ClassDef))]
            if top_stmts and isinstance(top_stmts[0], ast.Expr) and isinstance(top_stmts[0].value, ast.Constant) \
                    and m.tree.body and top_stmts[0] is m.tree.body[0]:
                top_stmts = top_stmts[1:]
            if top_stmts:
                tid = f"{mid}:<module>"
                rows = []
                for st in top_stmts:
                    rows.extend(range(st.lineno, (st.end_lineno or st.lineno) + 1))
                node = {"id": tid, "kind": "toplevel", "name": "module top level", "qual": tid, "module": mid,
                        "file": rel, "line": top_stmts[0].lineno, "end": top_stmts[-1].end_lineno, "parent": mid,
                        "rows": rows, "events": [], "calls": [], "sig": None, "doc": None, "params": []}
                self.nodes[tid] = node
                self.analyze_body(node, top_stmts, self.module_ctx(m), src)
                node["sha"] = sha("\n".join(src[r - 1] for r in rows))
            for cid, info in self.classes.items():
                if info.module != mid:
                    continue
                n = info.node
                start = min([n.lineno] + [d.lineno for d in n.decorator_list])
                rows = list(range(start, n.lineno + 1))
                for st in n.body:
                    if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                        rows.extend(range(st.lineno, (st.end_lineno or st.lineno) + 1))
                parent = cid.rsplit(".", 1)[0]
                self.nodes[cid] = {"id": cid, "kind": "class", "name": n.name, "qual": cid[len(mid) + 1:],
                                   "module": mid, "file": rel, "line": start, "end": n.end_lineno,
                                   "parent": parent if parent in self.classes else mid, "rows": rows,
                                   "sig": f"class {n.name}" + (f"({', '.join(_text(b, 40) for b in n.bases)})"
                                                               if n.bases else ""),
                                   "doc": (ast.get_docstring(n) or "").split("\n\n")[0][:400],
                                   "decorators": _decorator_names(n), "bases": info.bases,
                                   "fields": info.fields, "events": [], "calls": [], "params": [],
                                   "sha": sha("\n".join(src[(n.lineno - 1):(n.end_lineno or n.lineno)]))}
            for fid, info in self.funcs.items():
                if info.module != mid:
                    continue
                n = info.node
                start = min([n.lineno] + [d.lineno for d in n.decorator_list])
                is_async = isinstance(n, ast.AsyncFunctionDef)
                sig = f"{'async ' if is_async else ''}def {n.name}({_text(n.args, 400)})" + \
                      (f" -> {_text(n.returns, 60)}" if n.returns else "")
                kind = "method" if info.cls else "function"
                node = {"id": fid, "kind": kind, "name": n.name, "qual": fid[len(mid) + 1:], "module": mid,
                        "file": rel, "line": start, "end": n.end_lineno, "def_line": n.lineno,
                        "parent": info.parent or info.cls or mid, "nested": bool(info.parent),
                        "rows": list(range(start, (n.end_lineno or n.lineno) + 1)),
                        "sig": sig, "doc": (ast.get_docstring(n) or "").split("\n\n")[0][:600],
                        "decorators": _decorator_names(n), "params": info.params, "events": [], "calls": [],
                        "sha": sha("\n".join(src[start - 1:(n.end_lineno or n.lineno)]))}
                self.nodes[fid] = node
                ctx = self.ctx_for(fid)
                self.analyze_body(node, n.body, ctx, src)
                if info.parent:
                    pctx = ctx.parent
                    bound = set()
                    c = pctx
                    while c is not None:
                        bound |= c.local_names
                        c = c.parent
                    reads = {r for ev in node["events"] for r in ev["r"]}
                    node["free"] = sorted((reads & bound) - ctx.local_names)
        for pkg in sorted({"pkg:" + (m.package or ".") for m in self.modules.values()}):
            name = pkg[4:]
            self.nodes[pkg] = {"id": pkg, "kind": "package", "name": name if name != "." else self.root.name,
                               "qual": name, "parent": None}

    def finish_edges(self):
        # Registry dispatch for calls like args.func(args) after all registrations are known.
        for owner_id, rec, call, ctx in self.pending_unresolved:
            attr = call.func.attr if isinstance(call.func, ast.Attribute) else rec.get("attr")
            regs = sorted(self.registry.get(attr, ()))
            if regs and len(regs) <= MAX_REGISTRY_TARGETS:
                rec.pop("name", None)
                rec.pop("effect", None)
                rec.update({"kind": "dynamic", "cands": regs, "conf": "dynamic",
                            "via": f"functions registered as `{attr}=` elsewhere in the project"})
                rec["pm"] = {t: self.map_args(call, *self.params_for_target(t)) for t in regs}
        for nid, node in list(self.nodes.items()):
            for rec in node.get("calls", []):
                k = rec["kind"]
                if k in ("call", "instantiate"):
                    self.add_edge(nid, rec["target"], k, rec["conf"], rec["l"])
                elif k == "dynamic":
                    for t in rec["cands"]:
                        self.add_edge(nid, t, "call", "dynamic", rec["l"], rec["via"])
                elif k == "external":
                    rec["target"] = self.ext_node(rec["name"])
                    self.add_edge(nid, rec["target"], "external", "resolved", rec["l"])
        for m in self.modules.values():
            for entry in m.imports.values():
                target = entry[1] if entry[0] in ("mod", "sym") else None
                if target and target != m.id:
                    self.add_edge(m.id, target, "import", "resolved", None)
        for cid, info in self.classes.items():
            for b in info.bases:
                self.add_edge(cid, b, "inherit", "resolved", info.node.lineno)

    def entry_points(self):
        main_mods = [mid for mid in self.modules if mid.endswith("__main__")]
        for mid in main_mods:
            tid = f"{mid}:<module>"
            if tid in self.nodes:
                pkg = mid.rsplit(".", 1)[0] if "." in mid else mid
                self.nodes[tid]["entry"] = f"python -m {pkg}"
        for m in self.modules.values():
            for st in m.tree.body:
                if isinstance(st, ast.If) and "__name__" in _text(st.test) and "__main__" in _text(st.test):
                    tid = f"{m.id}:<module>"
                    if tid in self.nodes:
                        self.nodes[tid]["entry"] = f"python {m.rel}"
        pyproject = self.root / "pyproject.toml"
        if pyproject.is_file():
            try:
                import tomllib
                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
                for name, spec in (data.get("project", {}).get("scripts", {}) or {}).items():
                    mod, _, fn = spec.partition(":")
                    mid = self.canonical_module(mod)
                    if mid and f"{mid}.{fn}" in self.nodes:
                        self.nodes[f"{mid}.{fn}"]["entry"] = f"console script `{name}`"
            except Exception:  # noqa: BLE001 - optional metadata
                pass
        for nid, n in self.nodes.items():
            if n["kind"] == "method" and n["name"].startswith("test"):
                n.setdefault("entry", "test")
            elif n["kind"] == "function" and not n.get("nested") and n["name"].startswith("test_") \
                    and "test" in n["file"]:
                n.setdefault("entry", "test")

    def run(self) -> dict:
        self.load()
        self.link_types()
        self.build_nodes()
        self.finish_edges()
        self.entry_points()
        nodes = list(self.nodes.values()) + sorted(self.ext_nodes.values(), key=lambda n: n["id"])
        return {
            "version": 1,
            "root": str(self.root),
            "repo": self.root.name,
            "files": self.files,
            "nodes": nodes,
            "edges": list(self.edges.values()),
            "errors": self.errors,
        }


def analyze(root: Path, paths: list[Path] | None = None) -> dict:
    return Analyzer(root, paths).run()


def source_fingerprint(root: Path, paths: list[Path] | None = None) -> str:
    """Cheap change detector: paths, sizes and mtimes of the analyzed files."""
    a = Analyzer(root, paths)
    h = hashlib.sha256()
    for p in a.discover():
        st = p.stat()
        h.update(f"{p}:{st.st_size}:{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:16]
