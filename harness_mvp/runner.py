"""Isolated test execution.

DockerRunner is the only runner allowed for model-generated code. TrustedLocalRunner exists so
mock mode works without Docker: it refuses any source whose hash is not in an allowlist of
repository-authored candidates (task files and fixed mock outputs).

This is NOT a hardened sandbox for adversarial programs; see README.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from .tasks import Task, sha256_text

DEFAULT_IMAGE = "harness-mvp-runner:py311"
DOCKERFILE = Path(__file__).resolve().parent.parent / "docker" / "runner.Dockerfile"
OUTPUT_CAP = 20_000  # characters kept per stream


class RunnerUnavailable(Exception):
    pass


@dataclass
class ExecResult:
    outcome: str  # passed | failed | timeout | runner_error
    returncode: int | None
    stdout: str
    stderr: str
    duration_s: float
    tests_run: int | None = None
    expected_tests: int | None = None
    failed_tests: list[str] = field(default_factory=list)
    note: str = ""

    def to_record(self) -> dict:
        return {"outcome": self.outcome, "returncode": self.returncode,
                "duration_s": round(self.duration_s, 3), "tests_run": self.tests_run,
                "expected_tests": self.expected_tests, "failed_tests": self.failed_tests,
                "note": self.note, "stdout": self.stdout[-4000:], "stderr": self.stderr[-4000:]}


_RAN = re.compile(r"^Ran (\d+) tests? in", re.MULTILINE)
_FAILED_ID = re.compile(r"^(?:FAIL|ERROR): (\S+) \(([^)]+)\)", re.MULTILINE)


def parse_unittest_output(text: str) -> tuple[int | None, list[str]]:
    ran = _RAN.findall(text)
    failed = []
    for name, where in _FAILED_ID.findall(text):
        test_id = where if where.endswith(name) else f"{where}.{name}"
        if test_id not in failed:
            failed.append(test_id)
    return (int(ran[-1]) if ran else None), failed


def count_tests(test_source: str) -> int:
    return len(re.findall(r"^\s+def (test_\w+)\(", test_source, re.MULTILINE))


def classify(returncode: int | None, output: str, expected: int, timed_out: bool) -> tuple[str, int | None, list[str], str]:
    tests_run, failed = parse_unittest_output(output)
    if timed_out:
        return "timeout", tests_run, failed, ""
    ok_line = bool(re.search(r"^OK\b", output, re.MULTILINE))
    if returncode == 0 and ok_line and tests_run == expected:
        return "passed", tests_run, failed, ""
    if returncode == 0:
        # Exit 0 without the expected unittest summary (e.g. os._exit in candidate).
        return "failed", tests_run, failed, "exit 0 without expected unittest summary"
    if returncode == 137:
        return "failed", tests_run, failed, "killed (possibly memory limit)"
    return "failed", tests_run, failed, ""


def _prepare_workdir(root: Path, source: str, task: Task, which: str) -> Path:
    """Directory holding ONLY candidate.py and the requested test file."""
    root.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=f"{task.task_id}-{which}-", dir=root))
    (workdir / "candidate.py").write_text(source, encoding="utf-8")
    shutil.copyfile(task.tests_path(which), workdir / f"{which}_tests.py")
    os.chmod(workdir, 0o755)
    for f in workdir.iterdir():
        os.chmod(f, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return workdir


def _module_for(test_rel: str) -> str:
    if not test_rel.endswith(".py"):
        raise ValueError(f"test file must be a .py file: {test_rel}")
    return test_rel[:-3].replace("/", ".")


def _prepare_files_dir(root: Path, files: dict[str, str], label: str) -> Path:
    """Directory holding exactly `files` (relative paths), read-only for everyone."""
    root.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix=f"{label}-", dir=root))
    for rel, content in files.items():
        parts = Path(rel).parts
        if Path(rel).is_absolute() or ".." in parts or not parts:
            raise ValueError(f"unsafe execution path: {rel!r}")
        target = workdir.joinpath(*parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    for d, _, names in os.walk(workdir):
        os.chmod(d, 0o755)
        for n in names:
            os.chmod(os.path.join(d, n), stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    return workdir


def _cap(text: str) -> str:
    if len(text) <= OUTPUT_CAP:
        return text
    return text[:2000] + f"\n...[{len(text) - OUTPUT_CAP} chars truncated]...\n" + text[-(OUTPUT_CAP - 2000):]


# ---------------------------------------------------------------------------
# Docker
# ---------------------------------------------------------------------------

def docker_status() -> tuple[bool, str]:
    if shutil.which("docker") is None:
        return False, "docker CLI not found on PATH"
    try:
        proc = subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"],
                              capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return False, "docker version timed out"
    if proc.returncode != 0:
        return False, (proc.stderr.strip() or "docker daemon not reachable")[:300]
    return True, proc.stdout.strip()


def image_exists(image: str) -> bool:
    proc = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True)
    return proc.returncode == 0


def build_image(image: str = DEFAULT_IMAGE) -> None:
    print(f"Building runner image {image} from {DOCKERFILE} ...", flush=True)
    proc = subprocess.run(["docker", "build", "-t", image, "-f", str(DOCKERFILE), str(DOCKERFILE.parent)])
    if proc.returncode != 0:
        raise RunnerUnavailable(f"docker build failed for {image}")


class DockerRunner:
    name = "docker"

    def __init__(self, work_root: Path, image: str = DEFAULT_IMAGE, timeout_s: int = 10,
                 memory: str = "256m", cpus: str = "1", pids: int = 64):
        self.work_root = Path(work_root).resolve()
        self.image, self.timeout_s = image, timeout_s
        self.memory, self.cpus, self.pids = memory, cpus, pids

    def describe(self) -> dict:
        return {"runner": self.name, "image": self.image, "timeout_s": self.timeout_s,
                "memory": self.memory, "cpus": self.cpus, "pids_limit": self.pids,
                "network": "none", "user": "10001:10001", "read_only_rootfs": True,
                "tmpfs": "/tmp size=16m noexec", "cap_drop": "ALL", "no_new_privileges": True,
                "mounts": "fresh per-execution copy of the candidate files + one test module, read-only"}

    def ensure_ready(self, build: bool = True) -> str:
        ok, detail = docker_status()
        if not ok:
            raise RunnerUnavailable(f"Docker is unavailable: {detail}")
        if not image_exists(self.image):
            if not build:
                raise RunnerUnavailable(f"image {self.image} missing; run: python -m harness_mvp build-image")
            build_image(self.image)
        return detail

    def command(self, workdir: Path, module: str, container_name: str) -> list[str]:
        return [
            "docker", "run", "--rm", "--name", container_name,
            "--network", "none",
            "--user", "10001:10001",
            "--read-only",
            "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m",
            "--memory", self.memory, "--memory-swap", self.memory,
            "--cpus", self.cpus, "--pids-limit", str(self.pids),
            "--ulimit", "nofile=64:64",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--mount", f"type=bind,source={workdir},target=/work,readonly",
            "--workdir", "/work",
            self.image,
            "timeout", "-k", "1", str(self.timeout_s),
            "python", "-B", "-m", "unittest", "-v", module,
        ]

    def run(self, source: str, task: Task, which: str) -> ExecResult:
        workdir = _prepare_workdir(self.work_root, source, task, which)
        expected = count_tests(task.tests_path(which).read_text(encoding="utf-8"))
        return self._execute(workdir, f"{which}_tests", expected)

    def run_files(self, files: dict[str, str], test_rel: str, label: str = "exec") -> ExecResult:
        """Run the unittest module `test_rel` over exactly `files` (a fresh read-only mount)."""
        workdir = _prepare_files_dir(self.work_root, files, label)
        return self._execute(workdir, _module_for(test_rel), count_tests(files[test_rel]))

    def _execute(self, workdir: Path, module: str, expected: int) -> ExecResult:
        name = f"harness-mvp-{uuid.uuid4().hex[:12]}"
        start = time.perf_counter()
        try:
            # Host-side guard is generous: container start-up is not part of the test timeout.
            proc = subprocess.run(self.command(workdir, module, name), capture_output=True,
                                  text=True, timeout=self.timeout_s + 60, env=_docker_cli_env())
        except subprocess.TimeoutExpired:
            subprocess.run(["docker", "rm", "-f", name], capture_output=True)
            return ExecResult("timeout", None, "", "", time.perf_counter() - start,
                              expected_tests=expected, note="host-side timeout; container removed")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        duration = time.perf_counter() - start
        out, err = _cap(proc.stdout), _cap(proc.stderr)
        if proc.returncode in (125, 126, 127) and "Ran " not in err:
            return ExecResult("runner_error", proc.returncode, out, err, duration,
                              expected_tests=expected, note="docker failed to start the container")
        timed_out = proc.returncode == 124
        outcome, ran, failed, note = classify(proc.returncode, out + "\n" + err, expected, timed_out)
        return ExecResult(outcome, proc.returncode, out, err, duration, ran, expected, failed, note)


def _docker_cli_env() -> dict:
    """Environment for the docker CLI itself; secrets are stripped. The container gets none of it."""
    drop = ("KEY", "TOKEN", "SECRET", "PASSWORD")
    return {k: v for k, v in os.environ.items() if not any(d in k.upper() for d in drop)}


# ---------------------------------------------------------------------------
# Trusted local runner (mock mode only)
# ---------------------------------------------------------------------------

class TrustedLocalRunner:
    """Runs only allowlisted, repository-authored sources on the host. Never model output."""

    name = "local-trusted"

    def __init__(self, work_root: Path, trusted_sources: list[str], timeout_s: int = 10):
        self.work_root = Path(work_root).resolve()
        self.allowed = {sha256_text(s) for s in trusted_sources}
        self.timeout_s = timeout_s

    def describe(self) -> dict:
        return {"runner": self.name, "timeout_s": self.timeout_s,
                "note": "host subprocess; refuses any source not in the repo-authored allowlist"}

    def run(self, source: str, task: Task, which: str) -> ExecResult:
        if sha256_text(source) not in self.allowed:
            raise PermissionError("TrustedLocalRunner refuses non-allowlisted code; use Docker for model output")
        workdir = _prepare_workdir(self.work_root, source, task, which)
        expected = count_tests(task.tests_path(which).read_text(encoding="utf-8"))
        return self._execute(workdir, f"{which}_tests", expected)

    def run_files(self, files: dict[str, str], test_rel: str, label: str = "exec") -> ExecResult:
        bad = sorted(rel for rel, content in files.items() if sha256_text(content) not in self.allowed)
        if bad:
            raise PermissionError(f"TrustedLocalRunner refuses non-allowlisted files {bad}; use Docker for model output")
        workdir = _prepare_files_dir(self.work_root, files, label)
        return self._execute(workdir, _module_for(test_rel), count_tests(files[test_rel]))

    def _execute(self, workdir: Path, module: str, expected: int) -> ExecResult:
        env = {"PATH": os.defpath, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1"}
        start = time.perf_counter()
        try:
            proc = subprocess.run([sys.executable, "-B", "-m", "unittest", "-v", module],
                                  cwd=workdir, capture_output=True, text=True,
                                  timeout=self.timeout_s, env=env)
        except subprocess.TimeoutExpired as exc:
            return ExecResult("timeout", None, _as_text(exc.stdout), _as_text(exc.stderr),
                              time.perf_counter() - start, expected_tests=expected)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        out, err = _cap(proc.stdout), _cap(proc.stderr)
        outcome, ran, failed, note = classify(proc.returncode, out + "\n" + err, expected, False)
        return ExecResult(outcome, proc.returncode, out, err, time.perf_counter() - start,
                          ran, expected, failed, note)


def _as_text(value) -> str:
    if value is None:
        return ""
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else value
