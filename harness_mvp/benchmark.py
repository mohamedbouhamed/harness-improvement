"""Repository-repair tasks (benchmark/manifest.json).

Task layout:
    benchmark/tasks/<id>/repo/      copied into the agent workspace (source + public tests)
    benchmark/tasks/<id>/hidden/    test_hidden.py, used only for scoring after an episode
    benchmark/tasks/<id>/solution/  reference versions of the allowed files (validation, mock)
    benchmark/tasks/<id>/mock/<v>/  synthetic candidate variants (mock backend only)
"""
from __future__ import annotations

import ast
import json
from dataclasses import dataclass
from pathlib import Path

from .contracts import (SPLITS, ContractError, canonical_hash, check_split_families, sha256_text,
                        validate_task_entry)

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARK_DIR = REPO_ROOT / "benchmark"
HIDDEN_TEST_PATH = "tests/test_hidden.py"  # where the hidden test is placed in the scoring container
MAINTAINER_ONLY = ("principles", "bug_note", "mock_scenario", "mock_api_error")


def read_tree(root: Path) -> dict[str, str]:
    """All regular files under root (no symlinks, no caches), keyed by POSIX relative path."""
    files = {}
    if not root.is_dir():
        return files
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ContractError(f"symlink in task tree: {path}")
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            files[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
    return files


@dataclass(frozen=True)
class RepoTask:
    task_id: str
    split: str
    family: str
    title: str
    statement: str
    allowed_files: tuple[str, ...]
    public_tests: str
    principles: tuple[str, ...]
    entry: dict
    path: Path

    @property
    def mock_scenario(self) -> str:
        return self.entry.get("mock_scenario", "fix")

    @property
    def mock_api_error(self) -> dict | None:
        return self.entry.get("mock_api_error")

    def repo_files(self) -> dict[str, str]:
        return read_tree(self.path / "repo")

    def hidden_test_source(self) -> str:
        return (self.path / "hidden" / "test_hidden.py").read_text(encoding="utf-8")

    def solution_files(self) -> dict[str, str]:
        base = self.original_candidate()
        return base | {k: v for k, v in read_tree(self.path / "solution").items() if k in self.allowed_files}

    def original_candidate(self) -> dict[str, str]:
        repo = self.repo_files()
        return {rel: repo[rel] for rel in self.allowed_files}

    def mock_variant(self, name: str) -> dict[str, str] | None:
        tree = read_tree(self.path / "mock" / name)
        if not tree:
            return None
        return self.original_candidate() | {k: v for k, v in tree.items() if k in self.allowed_files}

    def public_view(self) -> dict:
        """The task as the agent may see it (no maintainer-only metadata)."""
        return {"id": self.task_id, "title": self.title, "statement": self.statement,
                "allowed_files": list(self.allowed_files), "public_tests": self.public_tests}

    def identifiers(self) -> set[str]:
        """Function, class and module names defined in the task's source (for lesson flagging)."""
        names = set()
        for rel in self.allowed_files:
            names.add(Path(rel).stem)
            try:
                tree = ast.parse(self.repo_files()[rel])
            except SyntaxError:
                continue
            names |= {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        return {n for n in names if len(n) >= 4 and not n.startswith("__")}

    def hashes(self) -> dict[str, str]:
        return {"entry": canonical_hash(self.entry), "repo": canonical_hash(self.repo_files()),
                "hidden": sha256_text(self.hidden_test_source()),
                "solution": canonical_hash(read_tree(self.path / "solution"))}

    def all_sources(self) -> list[str]:
        """Every repo-authored file content of this task (allowlist for the trusted local runner)."""
        out = list(self.repo_files().values()) + [self.hidden_test_source()]
        out += list(read_tree(self.path / "solution").values())
        for d in sorted((self.path / "mock").glob("*")) if (self.path / "mock").is_dir() else []:
            out += list(read_tree(d).values())
        return out


def load_benchmark(root: Path = BENCHMARK_DIR) -> list[RepoTask]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    entries = manifest["tasks"]
    for e in entries:
        validate_task_entry(e)
    check_split_families(entries)
    tasks = []
    for e in entries:
        t = RepoTask(e["id"], e["split"], e["family"], e["title"], e["statement"], tuple(e["allowed_files"]),
                     e["public_tests"], tuple(e["principles"]), e, root / "tasks" / e["id"])
        repo = t.repo_files()
        missing = [p for p in (*t.allowed_files, t.public_tests) if p not in repo]
        if missing:
            raise ContractError(f"task {t.task_id}: files missing from repo/: {missing}")
        if any(rel.startswith("hidden") or "test_hidden" in rel for rel in repo):
            raise ContractError(f"task {t.task_id}: hidden tests must not be inside repo/")
        if not (t.path / "hidden" / "test_hidden.py").is_file():
            raise ContractError(f"task {t.task_id}: missing hidden/test_hidden.py")
        tasks.append(t)
    return tasks


def by_split(tasks: list[RepoTask], split: str) -> list[RepoTask]:
    if split not in SPLITS:
        raise ValueError(f"unknown split {split!r}")
    return [t for t in tasks if t.split == split]
