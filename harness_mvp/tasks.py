"""Task loading. Splits come from tasks/manifest.json and are fixed before any run."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_DIR = REPO_ROOT / "tasks"                         # experiment v1 benchmark
CALIBRATION_DIR = REPO_ROOT / "tasks_calibration"       # v2 development-only tasks
BENCHMARK_V2_DIR = REPO_ROOT / "tasks_v2"               # v2 benchmark (frozen before use)
SPLITS = ("optimization", "test", "calibration")
TASK_FILES = ("buggy.py", "public_tests.py", "hidden_tests.py", "reference.py")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Task:
    task_id: str
    split: str
    signature: str
    statement: str
    bug_note: str  # maintainer documentation only; never sent to the model
    path: Path
    family: str = ""         # near-duplicate variants share a family; families never cross splits
    mock_scenario: str = ""  # which synthetic scenario the mock model plays (mock mode only)
    spec_hash: str = ""      # hash of the manifest entry (statement, signature, split, ...)

    def read(self, name: str) -> str:
        return (self.path / name).read_text(encoding="utf-8")

    @property
    def buggy_source(self) -> str:
        return self.read("buggy.py")

    @property
    def public_tests_source(self) -> str:
        return self.read("public_tests.py")

    @property
    def reference_source(self) -> str:
        return self.read("reference.py")

    def tests_path(self, which: str) -> Path:
        if which not in ("public", "hidden"):
            raise ValueError(f"unknown test set: {which}")
        return self.path / f"{which}_tests.py"

    def file_hashes(self) -> dict[str, str]:
        return {"spec": self.spec_hash, **{name: sha256_text(self.read(name)) for name in TASK_FILES}}

    def mock_variant(self, name: str) -> str | None:
        """Repo-authored mock candidate (e.g. 'partial', 'regressed'), if the task provides one."""
        path = self.path / f"mock_{name}.py"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def mock_sources(self) -> list[str]:
        return [p.read_text(encoding="utf-8") for p in sorted(self.path.glob("mock_*.py"))]


def load_tasks(tasks_dir: Path = TASKS_DIR) -> list[Task]:
    manifest = json.loads((tasks_dir / "manifest.json").read_text(encoding="utf-8"))
    tasks = []
    for entry in manifest["tasks"]:
        if entry["split"] not in SPLITS:
            raise ValueError(f"task {entry['id']}: invalid split {entry['split']!r}")
        task = Task(entry["id"], entry["split"], entry["signature"], entry["statement"],
                    entry.get("bug_note", ""), tasks_dir / entry["id"],
                    family=entry.get("family", entry["id"]), mock_scenario=entry.get("mock_scenario", ""),
                    spec_hash=sha256_text(json.dumps(entry, sort_keys=True)))
        for name in TASK_FILES:
            if not (task.path / name).is_file():
                raise FileNotFoundError(f"task {task.task_id}: missing {name}")
        tasks.append(task)
    ids = [t.task_id for t in tasks]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate task ids in manifest")
    return tasks


def split_tasks(tasks: list[Task]) -> tuple[list[Task], list[Task]]:
    return ([t for t in tasks if t.split == "optimization"],
            [t for t in tasks if t.split == "test"])


# ---------------------------------------------------------------------------
# Benchmark freezing (experiment v2). The freeze file pins task ids, splits, families and
# hashes of every task file and manifest entry; `verify_frozen` refuses any change.
# ---------------------------------------------------------------------------

FREEZE_FILE = "frozen.json"


class BenchmarkError(Exception):
    pass


def check_benchmark(tasks: list[Task], calibration: list[Task]) -> None:
    splits = {t.split for t in tasks}
    if splits != {"optimization", "test"}:
        raise BenchmarkError(f"benchmark needs exactly optimization and test splits, found {sorted(splits)}")
    family_splits: dict[str, set] = {}
    for t in tasks:
        family_splits.setdefault(t.family, set()).add(t.split)
    crossing = sorted(f for f, s in family_splits.items() if len(s) > 1)
    if crossing:
        raise BenchmarkError(f"task families cross splits: {crossing}")
    cal_ids, cal_families = {t.task_id for t in calibration}, {t.family for t in calibration}
    overlap = sorted({t.task_id for t in tasks} & cal_ids | {t.family for t in tasks} & cal_families)
    if overlap:
        raise BenchmarkError(f"benchmark overlaps calibration tasks (ids/families): {overlap}")


def freeze_benchmark(tasks_dir: Path, calibration_dir: Path = CALIBRATION_DIR, now: str = "") -> Path:
    path = tasks_dir / FREEZE_FILE
    if path.exists():
        raise BenchmarkError(f"{path} already exists; a frozen benchmark is never re-frozen in place")
    tasks = load_tasks(tasks_dir)
    check_benchmark(tasks, load_tasks(calibration_dir) if (calibration_dir / "manifest.json").exists() else [])
    data = {"benchmark": tasks_dir.name, "frozen_at": now,
            "tasks": {t.task_id: {"split": t.split, "family": t.family, "hashes": t.file_hashes()} for t in tasks}}
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def verify_frozen(tasks_dir: Path) -> tuple[list[Task], dict]:
    path = tasks_dir / FREEZE_FILE
    if not (tasks_dir / "manifest.json").exists():
        raise BenchmarkError(f"no benchmark at {tasks_dir} (missing manifest.json)")
    if not path.exists():
        raise BenchmarkError(f"benchmark {tasks_dir} is not frozen; run: python -m harness_mvp freeze-benchmark")
    frozen = json.loads(path.read_text(encoding="utf-8"))
    tasks = load_tasks(tasks_dir)
    current = {t.task_id: {"split": t.split, "family": t.family, "hashes": t.file_hashes()} for t in tasks}
    if current != frozen["tasks"]:
        changed = sorted(set(current) ^ set(frozen["tasks"])
                         | {k for k in set(current) & set(frozen["tasks"]) if current[k] != frozen["tasks"][k]})
        raise BenchmarkError(f"benchmark changed since it was frozen (tasks: {changed})")
    return tasks, frozen
