"""Fixed matching rule and episode metrics (docs/sec-protocol.md, "Matching rule" and "Metrics")."""
from __future__ import annotations

import re

from .tasks import CWE_CLASSES, SecTask

_CWE = re.compile(r"(\d+)")


def cwe_number(value) -> int | None:
    m = _CWE.search(str(value or ""))
    return int(m.group(1)) if m else None


def norm_file(value) -> str:
    s = str(value or "").strip().replace("\\", "/")
    while s.startswith("./"):
        s = s[2:]
    return s.lstrip("/")


def norm_function(value) -> str:
    s = str(value or "").strip().removesuffix("()")
    return s.rsplit(".", 1)[-1]


def line_range(value) -> tuple[int, int] | None:
    if isinstance(value, (list, tuple)) and value:
        nums = [v for v in value if isinstance(v, int) and not isinstance(v, bool)]
        if nums:
            return min(nums), max(nums)
    if isinstance(value, int) and not isinstance(value, bool):
        return value, value
    return None


def location_ok(f: dict, answer: dict, spans: list[tuple[int, int]]) -> bool:
    if norm_file(f.get("file")) != answer["file"]:
        return False
    if norm_function(f.get("function")) in answer["functions"]:
        return True
    rng = line_range(f.get("lines"))
    return bool(rng) and any(rng[0] <= hi and lo <= rng[1] for lo, hi in spans)


def score_episode(task: SecTask, variant: str, findings: list | None) -> dict:
    """Score one episode. findings=None means the agent never submitted."""
    submitted = findings is not None
    findings = findings or []
    malformed = sum(1 for f in findings if not isinstance(f, dict) or not f.get("file") or cwe_number(f.get("cwe")) is None)
    out = {"submitted": submitted, "n_findings": len(findings), "malformed": malformed}
    if variant == "patched":
        return out | {"detected": None, "clean": submitted and not findings, "alarms": len(findings),
                      "misclassified": 0, "path_coverage": None, "matched_index": None}
    answer, spans = task.answer, task.gt_spans()
    accepted = CWE_CLASSES[answer["class"]]
    matched, misclassified = None, 0
    for i, f in enumerate(findings):
        if not isinstance(f, dict) or not location_ok(f, answer, spans):
            continue
        if cwe_number(f.get("cwe")) in accepted:
            if matched is None:
                matched = i
                continue
        else:
            misclassified += 1
    coverage = None
    if matched is not None:
        path = findings[matched].get("path") or []
        names = {norm_function(p) for p in path} if isinstance(path, list) else set()
        coverage = round(len(names & set(answer["path"])) / len(answer["path"]), 3)
    return out | {"detected": matched is not None, "clean": None,
                  "alarms": len(findings) - (matched is not None), "misclassified": misclassified,
                  "path_coverage": coverage, "matched_index": matched}
