"""Deterministic spatial layout. The same code always produces the same map, so positions can be learned.

World units are pixels at zoom 1. Code blocks are sized so their source is readable at zoom 1:
one row per source line. Packages are laid out left to right (dependents first); modules inside a
package are placed in dependency layers (callers left, callees right); externals sit at the far right.
"""
from __future__ import annotations

import math

LINE_H = 14
CHAR_W = 6.6
HEADER = 30
GUTTER = 50
PAD = 10
MIN_W, MAX_W = 300, 880
CLASS_HEADER = 34
CLASS_INSET = 14
MODULE_HEADER = 48
MODULE_PAD = 22
ITEM_GAP = 26
LAYER_GAP = 260
MODULE_GAP = 70
PACKAGE_HEADER = 70
PACKAGE_PAD = 40
PACKAGE_GAP = 320
EXT_W, EXT_H, EXT_GAP = 250, 30, 8

CONSTANTS = {"LINE_H": LINE_H, "CHAR_W": CHAR_W, "HEADER": HEADER, "GUTTER": GUTTER, "PAD": PAD,
             "CLASS_HEADER": CLASS_HEADER}


def _rows_with_gaps(rows: list[int]) -> list[int | None]:
    out: list[int | None] = []
    for r in rows:
        if out and out[-1] is not None and r > out[-1] + 1:
            out.append(None)
        out.append(r)
    return out


def _code_width(rows, lines) -> float:
    longest = max((len(lines[r - 1].rstrip()) for r in rows if r and 0 < r <= len(lines)), default=20)
    return max(MIN_W, min(MAX_W, GUTTER + longest * CHAR_W + 2 * PAD))


def layout(graph: dict) -> dict:
    nodes = {n["id"]: n for n in graph["nodes"]}
    files = graph["files"]
    children: dict[str, list[str]] = {}
    for n in graph["nodes"]:
        if n.get("parent"):
            children.setdefault(n["parent"], []).append(n["id"])

    def size_leaf(n):
        lines = files[n["file"]]["lines"]
        n["rows"] = _rows_with_gaps(n["rows"])
        n["w"] = _code_width(n["rows"], lines)
        n["h"] = HEADER + len(n["rows"]) * LINE_H + PAD

    def size_class(c):
        lines = files[c["file"]]["lines"]
        c["rows"] = _rows_with_gaps(c["rows"])
        code_h = len(c["rows"]) * LINE_H
        methods = sorted((nodes[k] for k in children.get(c["id"], []) if nodes[k]["kind"] in ("method", "class")),
                         key=lambda x: x["line"])
        for m in methods:
            size_class(m) if m["kind"] == "class" else size_leaf(m)
        inner_w = max([_code_width(c["rows"], lines)] + [m["w"] for m in methods])
        c["w"] = inner_w + 2 * CLASS_INSET
        c["code_h"] = code_h
        c["h"] = CLASS_HEADER + code_h + sum(m["h"] + CLASS_INSET for m in methods) + CLASS_INSET
        c["_items"] = [m["id"] for m in methods]

    def place_class(c, x, y):
        c["x"], c["y"] = x, y
        c["cx"], c["cy"] = x + CLASS_INSET, y + CLASS_HEADER
        cy = y + CLASS_HEADER + c["code_h"] + CLASS_INSET
        for mid in c.pop("_items"):
            m = nodes[mid]
            if m["kind"] == "class":
                place_class(m, x + CLASS_INSET, cy)
            else:
                place_leaf(m, x + CLASS_INSET, cy)
            cy += m["h"] + CLASS_INSET

    def place_leaf(n, x, y):
        n["x"], n["y"] = x, y
        n["cx"], n["cy"] = x, y + HEADER
        place_nested(n, n)

    def place_nested(host, n):
        """Nested functions are drawn as regions over their lines inside the host block."""
        index = {r: i for i, r in enumerate(host["rows"]) if r is not None}
        for kid in children.get(n["id"], []):
            k = nodes[kid]
            if k["kind"] not in ("function", "method"):
                continue
            first, last = index.get(k["line"]), index.get(k["end"])
            if first is None or last is None:
                continue
            indent = len(files[k["file"]]["lines"][k["def_line"] - 1]) - \
                len(files[k["file"]]["lines"][k["def_line"] - 1].lstrip())
            k["host"] = host["id"]
            k["x"] = host["x"] + GUTTER + indent * CHAR_W - 6
            k["y"] = host["cy"] + first * LINE_H - 2
            k["w"] = host["w"] - (k["x"] - host["x"]) - 6
            k["h"] = (last - first + 1) * LINE_H + 4
            place_nested(host, k)

    # --- modules -----------------------------------------------------------
    modules = [n for n in graph["nodes"] if n["kind"] == "module"]
    for m in modules:
        items = []
        for kid in children.get(m["id"], []):
            k = nodes[kid]
            if k["kind"] == "toplevel" or (k["kind"] == "function" and not k.get("nested")):
                size_leaf(k)
                items.append(k)
            elif k["kind"] == "class":
                size_class(k)
                items.append(k)
        items.sort(key=lambda k: (k["kind"] != "toplevel", k["line"]))
        area = sum(k["w"] * k["h"] for k in items)
        target_h = max([k["h"] for k in items] + [math.sqrt(area / 1.5) if area else 0, 400])
        cols, col, col_h = [], [], 0.0
        for k in items:
            if col and col_h + k["h"] > target_h:
                cols.append(col)
                col, col_h = [], 0.0
            col.append(k)
            col_h += k["h"] + ITEM_GAP
        if col:
            cols.append(col)
        m["_cols"] = cols
        widths = [max(k["w"] for k in c) for c in cols]
        heights = [sum(k["h"] for k in c) + ITEM_GAP * (len(c) - 1) for c in cols]
        m["w"] = max(340, sum(widths) + ITEM_GAP * max(0, len(cols) - 1) + 2 * MODULE_PAD)
        m["h"] = MODULE_HEADER + (max(heights) if heights else 40) + MODULE_PAD

    def place_module(m, x, y):
        m["x"], m["y"] = x, y
        cx = x + MODULE_PAD
        for col in m.pop("_cols"):
            cy = y + MODULE_HEADER
            for k in col:
                if k["kind"] == "class":
                    place_class(k, cx, cy)
                else:
                    place_leaf(k, cx, cy)
                cy += k["h"] + ITEM_GAP
            cx += max(k["w"] for k in col) + ITEM_GAP

    # --- module dependency layers within each package -----------------------
    def owner_module(nid: str) -> str | None:
        n = nodes.get(nid)
        return n["module"] if n and "module" in n else None

    deps: dict[str, set[str]] = {m["id"]: set() for m in modules}
    for e in graph["edges"]:
        if e["kind"] in ("call", "instantiate", "import", "reference", "inherit"):
            a, b = owner_module(e["s"]), owner_module(e["t"])
            if a and b and a != b:
                deps[a].add(b)

    packages = sorted((n for n in graph["nodes"] if n["kind"] == "package"), key=lambda p: p["id"])
    pkg_of = {m["id"]: m["parent"] for m in modules}
    pkg_deps: dict[str, set[str]] = {p["id"]: set() for p in packages}
    for a, bs in deps.items():
        for b in bs:
            if pkg_of[a] != pkg_of[b]:
                pkg_deps[pkg_of[a]].add(pkg_of[b])

    def layers(ids: list[str], dep: dict[str, set[str]]) -> dict[str, int]:
        """Longest-path layering from sources (nothing depends on them) toward leaves; cycles tolerated."""
        indeg = {i: 0 for i in ids}
        for a in ids:
            for b in dep.get(a, ()):
                if b in indeg:
                    indeg[b] += 1
        layer = {i: 0 for i in ids}
        order = sorted(i for i in ids if indeg[i] == 0) or sorted(ids)[:1]
        visiting = set()

        def push(i, depth):
            if depth <= layer[i] and depth != 0:
                return
            if i in visiting:
                return
            visiting.add(i)
            layer[i] = max(layer[i], depth)
            for b in sorted(dep.get(i, ())):
                if b in layer:
                    push(b, layer[i] + 1)
            visiting.discard(i)

        for i in order:
            push(i, 0)
        return layer

    pkg_layer = layers([p["id"] for p in packages], pkg_deps)
    # test packages go last so the product code reads left to right from its entry points
    packages.sort(key=lambda p: (p["name"].split(".")[0] in ("tests", "test"), pkg_layer[p["id"]], p["id"]))

    px, max_pkg_h = 0.0, 0.0
    for p in packages:
        mods = sorted((m for m in modules if m["parent"] == p["id"]), key=lambda m: m["id"])
        ml = layers([m["id"] for m in mods], deps)
        n_layers = max(ml.values(), default=0) + 1
        columns: list[list[dict]] = [[] for _ in range(n_layers)]
        for m in mods:
            columns[ml[m["id"]]].append(m)
        # one barycenter sweep so modules sit near the modules that use them
        pos: dict[str, float] = {}
        for li, col in enumerate(columns):
            if li:
                def bary(m):
                    preds = [pos[a] for a, bs in deps.items() if m["id"] in bs and a in pos]
                    return sum(preds) / len(preds) if preds else 1e9
                col.sort(key=lambda m: (bary(m), m["id"]))
            for i, m in enumerate(col):
                pos[m["id"]] = i
        x = px + PACKAGE_PAD
        top = PACKAGE_HEADER
        col_heights = []
        for col in columns:
            if not col:
                continue
            y = top
            for m in col:
                place_module(m, x, y)
                y += m["h"] + MODULE_GAP
            col_heights.append(y - MODULE_GAP)
            x += max(m["w"] for m in col) + LAYER_GAP
        p["x"], p["y"] = px, 0.0
        p["w"] = max(400, x - LAYER_GAP + PACKAGE_PAD - px)
        p["h"] = max(col_heights, default=200) + PACKAGE_PAD
        max_pkg_h = max(max_pkg_h, p["h"])
        px += p["w"] + PACKAGE_GAP

    # --- externals -----------------------------------------------------------
    ext = sorted((n for n in graph["nodes"] if n["kind"] == "external"),
                 key=lambda n: (not n.get("stdlib"), n["group"], n["name"]))
    groups: dict[str, list[dict]] = {}
    for n in ext:
        groups.setdefault(n["group"], []).append(n)
    ext_groups = []
    gx, gy = px, PACKAGE_HEADER
    col_limit = max(max_pkg_h, 1200)
    for name, members in groups.items():
        h = 40 + len(members) * (EXT_H + EXT_GAP) + 6
        if gy + h > col_limit and gy > PACKAGE_HEADER:
            gx += EXT_W + 40 + 60
            gy = PACKAGE_HEADER
        g = {"id": f"extgroup:{name}", "kind": "extgroup", "name": name, "x": gx, "y": gy,
             "w": EXT_W + 24, "h": h, "stdlib": members[0].get("stdlib")}
        for i, n in enumerate(members):
            n["x"], n["y"], n["w"], n["h"] = gx + 12, gy + 40 + i * (EXT_H + EXT_GAP), EXT_W, EXT_H
        ext_groups.append(g)
        gy += h + 30
    graph["ext_groups"] = ext_groups

    all_boxes = [n for n in graph["nodes"] if "x" in n] + ext_groups
    x0, y0 = min(b["x"] for b in all_boxes) - 200, min(b["y"] for b in all_boxes) - 200
    graph["bounds"] = {"x": x0, "y": y0, "w": max(b["x"] + b["w"] for b in all_boxes) + 200 - x0,
                       "h": max(b["y"] + b["h"] for b in all_boxes) + 200 - y0}
    graph["layout"] = CONSTANTS
    return graph
