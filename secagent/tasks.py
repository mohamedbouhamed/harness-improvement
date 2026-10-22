"""Detection benchmark (benchmark_sec/manifest.json): vulnerable/patched pairs with an answer key.

Task layout:
    benchmark_sec/tasks/<id>/vulnerable/   the program as the agent sees it (README + source)
    benchmark_sec/tasks/<id>/patched/      overlay: only the files changed by the minimal fix
    benchmark_sec/tasks/<id>/witness/      test_functional.py, test_exploit.py (label checks only)
    benchmark_sec/tasks/<id>/answer.json   ground truth (never shown to the agent)

An episode runs on one *variant* of a pair: "vulnerable" or "patched". The agent only ever receives
`variant_files(...)`: no task id, split, class, answer key or witness test.
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

from harness_mvp.benchmark import read_tree
from harness_mvp.contracts import ContractError, canonical_hash

REPO_ROOT = Path(__file__).resolve().parent.parent
SEC_BENCHMARK_DIR = REPO_ROOT / "benchmark_sec"
SPLITS = ("calibration", "train", "gate", "test_seen", "test_unseen", "real")
VARIANTS = ("vulnerable", "patched")

# Weakness class -> accepted CWE numbers (docs/sec-protocol.md, "Matching rule").
CWE_CLASSES = {
    "path traversal": {22, 23, 36, 73},
    "SQL injection": {89, 943},
    "OS command injection": {78, 77, 88},
    "unsafe deserialization": {502},
    "cross-site scripting": {79, 80},
    "missing authorization": {862, 863, 639, 285, 284},
    "open redirect": {601},
    "code injection": {94, 95},
}
SEEN_CLASSES = ("path traversal", "SQL injection", "OS command injection", "unsafe deserialization",
                "cross-site scripting")
ANSWER_REQUIRED = ("class", "cwe", "file", "functions", "source", "path", "summary")


def function_spans(source: str) -> dict[str, list[tuple[int, int]]]:
    """Function name -> [(first line, last line)] for every def (methods included)."""
    spans: dict[str, list[tuple[int, int]]] = {}
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            start = min([node.lineno] + [d.lineno for d in node.decorator_list])
            spans.setdefault(node.name, []).append((start, node.end_lineno))
    return spans


@dataclass(frozen=True)
class SecTask:
    task_id: str
    split: str
    family: str
    weakness_class: str
    path: Path
    entry: dict

    @property
    def answer(self) -> dict:
        return json.loads((self.path / "answer.json").read_text(encoding="utf-8"))

    def variant_files(self, variant: str) -> dict[str, str]:
        if variant not in VARIANTS:
            raise ValueError(variant)
        files = read_tree(self.path / "vulnerable")
        if variant == "patched":
            overlay = read_tree(self.path / "patched")
            unknown = sorted(set(overlay) - set(files))
            if unknown:
                raise ContractError(f"{self.task_id}: patched overlay adds files {unknown}")
            files |= overlay
        return files

    def witness_files(self) -> dict[str, str]:
        return read_tree(self.path / "witness")

    def gt_spans(self) -> list[tuple[int, int]]:
        """Line spans of the GT functions in the vulnerable variant."""
        ans = self.answer
        spans = function_spans(self.variant_files("vulnerable")[ans["file"]])
        return [s for fn in ans["functions"] for s in spans.get(fn, [])]

    def hashes(self) -> dict[str, str]:
        return {v: canonical_hash(self.variant_files(v)) for v in VARIANTS} | {
            "answer": canonical_hash(self.answer), "witness": canonical_hash(self.witness_files())}


def validate_task(t: SecTask) -> None:
    ans = t.answer
    missing = [k for k in ANSWER_REQUIRED if k not in ans]
    if missing:
        raise ContractError(f"{t.task_id}: answer.json missing {missing}")
    if ans["class"] != t.weakness_class or ans["class"] not in CWE_CLASSES:
        raise ContractError(f"{t.task_id}: class mismatch {ans['class']!r} / {t.weakness_class!r}")
    if int(str(ans["cwe"]).upper().removeprefix("CWE-")) not in CWE_CLASSES[ans["class"]]:
        raise ContractError(f"{t.task_id}: GT cwe not in its class")
    vuln, patched = t.variant_files("vulnerable"), t.variant_files("patched")
    if ans["file"] not in vuln:
        raise ContractError(f"{t.task_id}: GT file {ans['file']} not in program")
    spans = function_spans(vuln[ans["file"]])
    absent = [f for f in ans["functions"] if f not in spans]
    if absent:
        raise ContractError(f"{t.task_id}: GT functions not defined in {ans['file']}: {absent}")
    if vuln == patched:
        raise ContractError(f"{t.task_id}: patched variant is identical to vulnerable")
    if "README.md" not in vuln:
        raise ContractError(f"{t.task_id}: program needs a README.md stating the trust boundary")
    if t.entry.get("witness_kind") not in ("exploit", "reachability"):
        raise ContractError(f"{t.task_id}: witness_kind must be 'exploit' or 'reachability'")
    if set(t.witness_files()) != {"test_functional.py", "test_exploit.py"}:
        raise ContractError(f"{t.task_id}: witness/ must hold exactly test_functional.py and test_exploit.py")
    leaks = [rel for rel, src in vuln.items() for word in ("vuln", "exploit", "CWE", "answer", "witness")
             if word.lower() in src.lower() or word.lower() in rel.lower()]
    if leaks:
        raise ContractError(f"{t.task_id}: program text contains hint words: {sorted(set(leaks))}")


def load_sec_benchmark(root: Path = SEC_BENCHMARK_DIR) -> list[SecTask]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    tasks, families = [], {}
    for e in manifest["tasks"]:
        if e.get("split") not in SPLITS:
            raise ContractError(f"task {e.get('id')}: invalid split {e.get('split')!r}")
        families.setdefault(e["family"], set()).add(e["split"])
        t = SecTask(e["id"], e["split"], e["family"], e["class"], root / "tasks" / e["id"], e)
        validate_task(t)
        tasks.append(t)
    crossing = sorted(f for f, s in families.items() if len(s) > 1)
    if crossing:
        raise ContractError(f"families cross splits: {crossing}")
    if len({t.task_id for t in tasks}) != len(tasks):
        raise ContractError("duplicate task ids")
    return tasks
