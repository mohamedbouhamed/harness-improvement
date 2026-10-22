"""File inspection through standard command-line tools."""
import os
import subprocess

FORBIDDEN = (";", "|", "&", "\n", ">", "<")


class ProbeError(Exception):
    pass


def _check_name(name: str) -> None:
    if not name or any(ch in name for ch in FORBIDDEN):
        raise ProbeError("file name contains forbidden characters")


def _shell(command: str, timeout: int) -> str:
    proc = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise ProbeError(proc.stderr.strip() or "probe failed")
    return proc.stdout.strip()


def describe(settings, name: str) -> dict:
    _check_name(name)
    path = os.path.join(settings.library_dir, name)
    kind = _shell(f'file --brief "{path}"', settings.probe_timeout_s)
    return {"name": name, "type": kind}


def disk_usage(settings) -> str:
    return _shell(f"df -h {settings.library_dir} | tail -1", settings.probe_timeout_s)
