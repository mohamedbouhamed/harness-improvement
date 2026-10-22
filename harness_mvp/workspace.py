"""Per-episode workspace with strict path and write rules.

The workspace is a fresh copy of a task's repo/ (source + public tests); hidden tests are never in it.
- Paths must be relative, inside the workspace, without `..`, and may not pass through symlinks.
- Only the task's `allowed_files` can be written; nothing can be created or deleted.
- Executions never use the workspace's test files: they are rebuilt from the pristine repository
  plus the current contents of the allowed files.
"""
from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from .benchmark import RepoTask
from .contracts import canonical_hash


class WorkspaceError(Exception):
    """A rejected tool operation (reported back to the agent as a tool error)."""


def normalize_rel(rel) -> str:
    if not isinstance(rel, str) or not rel or "\x00" in rel or "\\" in rel:
        raise WorkspaceError(f"invalid path {rel!r}")
    p = PurePosixPath(rel)
    if p.is_absolute() or ".." in p.parts or rel.startswith("~"):
        raise WorkspaceError(f"path escapes the workspace: {rel!r}")
    parts = [x for x in p.parts if x != "."]
    if not parts:
        raise WorkspaceError(f"invalid path {rel!r}")
    return "/".join(parts)


class Workspace:
    def __init__(self, root: Path, task: RepoTask):
        self.root = Path(root)
        if self.root.exists() and any(self.root.iterdir()):
            raise WorkspaceError(f"workspace {self.root} is not empty")
        self.task = task
        self.pristine = task.repo_files()
        self.allowed = frozenset(task.allowed_files)
        for rel, content in self.pristine.items():
            target = self.root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        self.real_root = os.path.realpath(self.root)

    def _resolve(self, rel) -> tuple[str, Path]:
        rel = normalize_rel(rel)
        current = self.root
        for part in rel.split("/"):
            current = current / part
            if current.is_symlink():
                raise WorkspaceError(f"symlinks are not allowed: {rel!r}")
        real = os.path.realpath(current)
        if real != self.real_root and not real.startswith(self.real_root + os.sep):
            raise WorkspaceError(f"path escapes the workspace: {rel!r}")
        return rel, current

    def list_files(self) -> list[str]:
        out = []
        for dirpath, dirnames, filenames in os.walk(self.root, followlinks=False):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__" and not os.path.islink(os.path.join(dirpath, d)))
            for name in sorted(filenames):
                full = Path(dirpath) / name
                if not full.is_symlink():
                    out.append(full.relative_to(self.root).as_posix())
        return out

    def read(self, rel) -> str:
        rel, path = self._resolve(rel)
        if not path.is_file():
            raise WorkspaceError(f"no such file: {rel}")
        return path.read_text(encoding="utf-8", errors="replace")

    def write(self, rel, content: str) -> str:
        rel, path = self._resolve(rel)
        if rel not in self.allowed:
            raise WorkspaceError(f"{rel} is not writable; writable files: {sorted(self.allowed)}")
        if not isinstance(content, str):
            raise WorkspaceError("content must be a string")
        path.write_text(content, encoding="utf-8")
        return rel

    def search(self, query: str, max_matches: int) -> list[str]:
        if not isinstance(query, str) or not query:
            raise WorkspaceError("query must be a non-empty string")
        matches = []
        for rel in self.list_files():
            for i, line in enumerate(self.read(rel).splitlines(), 1):
                if query in line:
                    matches.append(f"{rel}:{i}: {line.strip()[:200]}")
                    if len(matches) >= max_matches:
                        return matches
        return matches

    def candidate(self) -> dict[str, str]:
        """Current contents of the allowed (writable) files."""
        return {rel: self.read(rel) for rel in sorted(self.allowed)}


def candidate_hash(candidate: dict[str, str]) -> str:
    return canonical_hash(candidate)


def execution_files(task: RepoTask, candidate: dict[str, str], which: str) -> tuple[dict[str, str], str]:
    """Files for one test execution, rebuilt from the PRISTINE repository plus the candidate.

    which="public": pristine repo + candidate, run the public test module.
    which="hidden": same without the public test, plus the hidden test (scoring only).
    """
    from .benchmark import HIDDEN_TEST_PATH
    if set(candidate) - set(task.allowed_files):
        raise WorkspaceError("candidate contains non-writable files")
    files = task.repo_files() | candidate
    if which == "public":
        return files, task.public_tests
    if which == "hidden":
        files = {k: v for k, v in files.items() if k != task.public_tests}
        files[HIDDEN_TEST_PATH] = task.hidden_test_source()
        return files, HIDDEN_TEST_PATH
    raise ValueError(which)
