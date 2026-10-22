"""Rescore stored findings under scorer v2 (no model calls). Historical v1 scores are not modified."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .scoring import score_episode_v2
from .tasks import load_sec_benchmark


def rescore(paths: list[Path]) -> list[dict]:
    tasks = {t.task_id: t for t in load_sec_benchmark()}
    rows = []
    for p in paths:
        for line in p.read_text().splitlines():
            r = json.loads(line)
            if r.get("synthetic"):
                continue
            v2 = score_episode_v2(tasks[r["task_id"]], r["variant"], r["findings"])
            v1 = r["score"]
            key = "detected" if r["variant"] == "vulnerable" else "clean"
            rows.append({"run": p.parent.name, "episode": r["episode_id"], "variant": r["variant"],
                         "v1": v1[key], "v2": v2[key], "v1_alarms": v1["alarms"], "v2_alarms": v2["alarms"],
                         "duplicates": v2["duplicates"], "malformed": v2["malformed"],
                         "path_recall": v2["path_recall"], "path_precision": v2["path_precision"]})
    return rows


if __name__ == "__main__":
    rows = rescore(sorted(Path("runs/sec").glob("**/episodes.jsonl")))
    changed = [r for r in rows if r["v1"] != r["v2"] or r["v1_alarms"] != r["v2_alarms"]]
    print(json.dumps({"episodes": len(rows), "changed": changed,
                      "detections_v1": sum(1 for r in rows if r["variant"] == "vulnerable" and r["v1"]),
                      "detections_v2": sum(1 for r in rows if r["variant"] == "vulnerable" and r["v2"]),
                      "duplicates": sum(r["duplicates"] for r in rows), "malformed": sum(r["malformed"] for r in rows),
                      "path_precision": [r["path_precision"] for r in rows if r["path_precision"] is not None]}, indent=1))
    sys.exit(0)
