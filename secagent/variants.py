"""Immutable harness variant bundles (harness_variants/<name>/): exact effective texts + recorded hashes.

The ablation runner passes these texts to the agent explicitly; it never reads harness/ and never
infers a harness from a condition name. Loading fails if a file no longer matches its recorded hash.
"""
from __future__ import annotations

import json
from pathlib import Path

from harness_mvp.contracts import sha256_text

VARIANTS_DIR = Path(__file__).resolve().parent.parent / "harness_variants"
FILES = ("instructions.md", "verification.md")


def effective_text(path: Path) -> str:
    """The text the agent receives (the agent strips surrounding whitespace)."""
    return path.read_text(encoding="utf-8").strip()


def load_variant(name: str, root: Path = VARIANTS_DIR) -> dict:
    d = root / name
    meta = json.loads((d / "bundle.json").read_text(encoding="utf-8"))
    texts = {f: effective_text(d / f) for f in FILES}
    for f in FILES:
        if sha256_text(texts[f]) != meta["effective_sha256"][f]:
            raise ValueError(f"variant {name}: {f} does not match its recorded hash")
    return {"name": name, "instructions": texts["instructions.md"], "verification": texts["verification.md"],
            "hashes": meta["effective_sha256"], "provenance": meta["provenance"]}


def write_bundle(name: str, provenance: dict, root: Path = VARIANTS_DIR) -> dict:
    d = root / name
    meta = {"name": name, "effective_sha256": {f: sha256_text(effective_text(d / f)) for f in FILES},
            "provenance": provenance}
    (d / "bundle.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return meta
