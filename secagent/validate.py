"""Label validation in Docker (no model calls).

For every pair: functional tests must pass on both variants; the exploit witness must FAIL on the
vulnerable variant (the attack works) and PASS on the patched one (the attack is blocked). Witness
tests run offline against the local program, with the existing DockerRunner limits (no network,
read-only, unprivileged).
"""
from __future__ import annotations

from .tasks import SecTask

EXPECTED = {("vulnerable", "functional"): "passed", ("patched", "functional"): "passed",
            ("vulnerable", "exploit"): "failed", ("patched", "exploit"): "passed"}


def validate_labels(task: SecTask, runner) -> dict:
    witness = task.witness_files()
    out = {}
    for variant in ("vulnerable", "patched"):
        for kind in ("functional", "exploit"):
            files = task.variant_files(variant) | {"witness/__init__.py": ""}
            files[f"witness/test_{kind}.py"] = witness[f"test_{kind}.py"]
            res = runner.run_files(files, f"witness/test_{kind}.py", label=f"{task.task_id}-{variant}-{kind}")
            out[(variant, kind)] = res
    ok = all(out[k].outcome == v for k, v in EXPECTED.items())
    # An exploit test that fails for a reason other than its assertion (e.g. an import error) proves nothing.
    vuln_exploit = out[("vulnerable", "exploit")]
    assertion_failure = "AssertionError" in vuln_exploit.stdout + vuln_exploit.stderr
    return {"task_id": task.task_id, "ok": ok and assertion_failure, "assertion_failure": assertion_failure,
            "outcomes": {f"{v}/{k}": r.outcome for (v, k), r in out.items()},
            "details": {f"{v}/{k}": (r.stdout + r.stderr)[-1500:] for (v, k), r in out.items()
                        if r.outcome != EXPECTED[(v, k)]}}
