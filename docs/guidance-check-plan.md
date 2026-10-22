# Guidance check (exploratory pilot) — recorded before running

Status: **planned, not run.** Committed before any guidance-check request is sent.

## Question

On this model, can extra text in the first message (generic checklist, hand-written per-class
lessons) change detection outcomes at all? This is a feasibility check before an automatic
adaptation pilot. A negative result would **not** show that memory cannot help; it would only
say that these two texts did not move these four pairs.

## Selection (fixed here, before running)

| Pair | Class | No-guidance outcome at T = 0 (reference) | Lessons cover the class? |
|---|---|---|---|
| cal_docshare | path traversal | vulnerable missed 3/3, twin clean 3/3 | yes |
| cal_miniwiki | cross-site scripting | vulnerable missed 2/2, twin clean 2/2 | yes |
| cal_teamnotes | SQL injection | vulnerable found 2/2, twin clean 2/2 | yes |
| cal_mediakit | OS command injection | vulnerable found 2/2, twin clean 2/2 | yes |

Rule: two previously missed and two previously detected **seen** classes (the lessons cover only
seen classes). The choice is forced, not picked from among alternatives: docshare and miniwiki are
the only missed seen classes; teamnotes and mediakit are the only detected seen classes whose task
version is unchanged (`cal_prefsync` is excluded because its twin was replaced by
`cal_prefsync_v2`).

## Design

- Conditions: `checklist`, `lessons`. Temperature 0, one run per program, seed 4:
  4 pairs × 2 variants × 2 conditions = **16 episodes**.
- Reference: the existing `none` episodes at T = 0 (runs `…03d1e6`, `…898a0a`). Their task hashes
  equal the current hashes for all four pairs and every request returned `gemini-3.5-flash-lite`.
  The reference was collected earlier the same day, so it is an **exploratory** comparison, not a
  concurrent control. Only unchanged task versions are compared.
- Reported per condition and pair: detection, twin false alarms, non-submissions, tokens, latency,
  cost, returned model id, witness kind.
- Bias disclosure: the lessons were written after the calibration tasks were outlined, so for
  docshare and miniwiki they describe the planted flaw closely. A positive result here is a
  ceiling (can the agent use near-explicit guidance), not evidence of transfer.

## Budget

| Item | EUR (upper-bound basis) |
|---|---|
| Spent so far (upper bound, not reset; reported €0.198) | 2.988 |
| Project cap | 5.000 |
| Remaining allowance | 2.012 |
| Guidance check: expected (16 × €0.050) | 0.80 |
| Guidance check: absolute worst (every episode 8 requests, full output) | 1.61 |
| **Guard for this run: stop at cumulative €3.90** (this run can use at most €0.91) | |
| Reserved for one automatic proposal + small separate evaluation | 1.10 |

If the guard truncates the run, the remaining episodes are not rerun without approval.

## Reserved: adaptation pilot (sketch, needs separate approval)

- Proposer: one model call that sees only the four selected pairs' traces and scores, and
  proposes one edit to `harness/memory.md` tied to a failure pattern (≤ €0.05 bound).
- Evaluation on the other calibration pairs (cal_invoicer, cal_ssohub, cal_pricer,
  cal_prefsync_v2): 8 episodes with the edit, plus 1 `none` reference episode for the new
  prefsync_v2 twin: about €0.45 expected, €0.91 worst on the upper-bound basis.

## Command

```bash
python -m secagent run --live --split calibration \
  --tasks cal_docshare,cal_miniwiki,cal_teamnotes,cal_mediakit \
  --conditions checklist,lessons --reps 1 --temperature 0 --seed 4 \
  --cap-eur 3.90 --usd-to-eur 1.0 --max-model-calls 140 --min-interval 1
```

## Result (run `calibration-live-20261022T212341Z-d31dba`, 16/16 episodes, all returned `gemini-3.5-flash-lite`)

| Pair | Witness | none (reference, T = 0) | checklist | lessons |
|---|---|---|---|---|
| cal_docshare (path traversal) | exploit | missed 3/3, twin clean | missed, twin clean | missed, twin clean |
| cal_miniwiki (XSS) | exploit | missed 2/2, twin clean | missed, twin clean | missed, twin clean |
| cal_teamnotes (SQL injection) | reachability | found 2/2, twin clean | found, twin clean | found, twin clean |
| cal_mediakit (command injection) | reachability | found 2/2, twin clean | found, twin clean | found, twin clean |

No outcome changed: 0 of 8 programs per condition differ from the reference. No false alarms and no
non-submissions. Mean per episode: checklist 7,666 in / 158 out tokens, 3.75 calls, 10.3 s;
lessons 9,155 / 165, 3.88 calls, 8.6 s. Cost of the run: reported €0.0355, upper bound €0.665.

Observation from the traces: in every missed episode the agent read the file containing the
planted weakness (storage.py, render.py) and then submitted no findings. Short visible outputs do
not show whether or how the model reasoned (thinking is not reported by the endpoint), so no cause
is inferred here. This says only that these two texts did not move these four pairs on this
model; it does not show that memory cannot help.
