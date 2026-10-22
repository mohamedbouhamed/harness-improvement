# Rescoring under scorer v2 (no model calls)

**Label: post-hoc rescoring.** Historical results in other documents keep their original (v1)
scores. This file only reports what scorer v2 would have said about the same stored findings.

## Why v2

A check of scorer v1 (sec-v1) confirmed that a finding with the right file and CWE received
detection credit with a nonexistent function and lines `[1, 9999]`, and even with the **wrong,
existing** function and a broad range, because any line overlap with a ground-truth function
counted. v1 also counted identical findings as separate alarms and did not validate structure.

Scorer v2 (`secagent/scoring.py`, `score_episode_v2`):
- structural validation (file, CWE, function required; `lines` must be `[first, last]` positive
  integers); a malformed finding is an alarm and never matches;
- duplicates (same file, function, CWE number) are collapsed before scoring;
- localisation: the named function must be a ground-truth function; naming another real
  function of that file never matches; only if the named function does not exist may a line
  range localise, and then it must cover at most 20 lines and overlap a ground-truth function;
- detection is reported separately from evidence quality: **path recall** (share of the
  ground-truth path named) and **path precision** (share of the named path that is on the
  ground-truth path).

## Result on all 109 stored live episodes

| | v1 | v2 |
|---|---|---|
| Detections on vulnerable versions | 28 | 28 |
| Clean twins | 47 | 47 |
| Episodes whose detection or clean status changes | – | 0 |
| Episodes whose alarm count changes | – | 2 (cal_prefsync vulnerable, runs …898a0a and …0943c9: a duplicated finding counted as an extra alarm under v1) |

No pair-solved outcome, acceptance decision or reported conclusion depends on the v1 loophole.
Evidence quality of the 28 detections: path recall mean 0.85, path precision mean
0.92 (min 0.5).

Reproduce (needs the local, git-ignored run folders): `python -m secagent.rescore`.
