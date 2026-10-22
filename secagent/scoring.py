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


# ---------------------------------------------------------------------------------------------
# Scorer v2 (protocol sec-v1.3). v1 above is kept unchanged for historical scores.
# ---------------------------------------------------------------------------------------------

SCORER_V2 = "v2"
MAX_LINE_SPAN = 20  # a line range used for localisation may cover at most this many lines


def finding_problems(f) -> list[str]:
    """Structural validation. A malformed finding is an alarm and can never match."""
    if not isinstance(f, dict):
        return ["not an object"]
    probs = []
    if not isinstance(f.get("file"), str) or not norm_file(f.get("file")):
        probs.append("file missing")
    if cwe_number(f.get("cwe")) is None:
        probs.append("cwe missing or unparsable")
    if not isinstance(f.get("function"), str) or not norm_function(f.get("function")):
        probs.append("function missing")
    lines = f.get("lines")
    if lines is not None:
        ok = (isinstance(lines, (list, tuple)) and 1 <= len(lines) <= 2
              and all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in lines)
              and lines[0] <= lines[-1])
        if not ok:
            probs.append("lines must be [first, last] positive integers")
    return probs


def dedupe(findings: list) -> tuple[list, int]:
    """Collapse findings with the same (file, function, CWE number); keep the first."""
    seen, out = set(), []
    for f in findings:
        key = (norm_file(f.get("file")), norm_function(f.get("function")), cwe_number(f.get("cwe"))) if isinstance(f, dict) else id(f)
        if key in seen:
            continue
        seen.add(key)
        out.append(f)
    return out, len(findings) - len(out)


def location_ok_v2(f: dict, answer: dict, spans: list[tuple[int, int]], defined: set[str]) -> bool:
    if norm_file(f.get("file")) != answer["file"]:
        return False
    fn = norm_function(f.get("function"))
    if fn in answer["functions"]:
        return True
    if fn in defined:  # names a real function of that file that is not a GT location
        return False
    rng = line_range(f.get("lines"))
    if not rng or rng[1] - rng[0] + 1 > MAX_LINE_SPAN:
        return False
    return any(rng[0] <= hi and lo <= rng[1] for lo, hi in spans)


def score_episode_v2(task: SecTask, variant: str, findings: list | None) -> dict:
    from .tasks import function_spans
    submitted = findings is not None
    unique, duplicates = dedupe(findings or [])
    malformed = sum(1 for f in unique if finding_problems(f))
    base = {"scorer": SCORER_V2, "submitted": submitted, "n_findings": len(unique), "duplicates": duplicates,
            "malformed": malformed}
    if variant == "patched":
        return base | {"detected": None, "clean": submitted and not unique, "alarms": len(unique), "misclassified": 0,
                       "path_recall": None, "path_precision": None, "matched_index": None}
    answer, spans = task.answer, task.gt_spans()
    defined = set(function_spans(task.variant_files("vulnerable")[answer["file"]]))
    accepted = CWE_CLASSES[answer["class"]]
    matched, misclassified = None, 0
    for i, f in enumerate(unique):
        if finding_problems(f) or not location_ok_v2(f, answer, spans, defined):
            continue
        if cwe_number(f.get("cwe")) in accepted:
            if matched is None:
                matched = i
                continue
        else:
            misclassified += 1
    recall = precision = None
    if matched is not None:
        path = unique[matched].get("path")
        names = {norm_function(p) for p in path} if isinstance(path, list) else set()
        names.discard("")
        hit = len(names & set(answer["path"]))
        recall = round(hit / len(answer["path"]), 3)
        precision = round(hit / len(names), 3) if names else None
    return base | {"detected": matched is not None, "clean": None, "alarms": len(unique) - (matched is not None),
                   "misclassified": misclassified, "path_recall": recall, "path_precision": precision,
                   "matched_index": matched}
