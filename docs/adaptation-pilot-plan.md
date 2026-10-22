# One-proposal adaptation pilot — recorded before any generation

Status: **planned, not run.** Approved by the user with a cumulative €4.95 guard under the
conservative (upper-bound) accounting policy; project limit €5 including all previous usage.
Exploratory pilot on calibration pairs (protocol `sec-v1.1`), not a Phase 2 result.

## Original harness (preserved)

Git tag `harness-v0` marks the commit with the original editable surfaces.

| File | SHA-256 (original) |
|---|---|
| harness/instructions.md | `63aae5104e830009c6dbde2b010722000846013d306e6709086a2f6ce58ec727` |
| harness/memory.md (empty) | `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855` |

## Steps (each fixed in advance)

1. **Proposer** (one request, same model `gemini-3.5-flash-lite`, temperature 0). Inputs: the
   current editable files and deterministic summaries of every existing episode on the four
   training pairs (34 episodes: conditions none, checklist, lessons): outcome, files read,
   findings, and that training pair's planted weakness (class, file, functions, explanation).
   Nothing from other pairs, no witness tests. The prompt states the observed outcomes only and
   prescribes no diagnosis.
2. **Validation, then freeze.** An inadmissible proposal (wrong target, too long, names a training
   identifier, contains code, unparsable) is logged as rejected and the pilot ends: no second
   proposal, no manual correction. An admissible proposal is written, a diff check confirms that
   only `harness/instructions.md` or `harness/memory.md` changed, and the patch is committed with
   its pattern, rationale and both file hashes **before** it is run.
3. **Training-set improvement check** (not independent validation): the proposed harness on the
   4 training pairs, both variants, temperature 0, one run: 8 episodes.
4. **Decision**, committed before any transfer episode runs.
5. **Exploratory transfer** (run whether accepted or rejected, original decision kept): the
   proposed harness on 2 other calibration pairs, both variants: 4 episodes. These pairs were
   excluded from proposer inputs but were previously calibrated, so this is not a pristine test.
6. If rejected, the harness is restored to `harness-v0` afterwards (revert commit); the rejected
   patch stays in history and in the run log.

## Definitions

- **Vulnerable detection**: the planted weakness is matched under the fixed matching rule.
- **Twin false positive**: the fixed twin's episode submits one or more findings.
- **Pair solved** (one run): vulnerable detected **and** twin submitted with zero findings.
  A non-submission on either variant means not solved.
- Detection and twin false positives are always reported separately; all raw episode outcomes
  are kept in the run folders.

**Reference** (existing `none` runs at T = 0, unchanged task versions, all returned
`gemini-3.5-flash-lite`): training pairs solved: cal_teamnotes, cal_mediakit; unsolved:
cal_docshare, cal_miniwiki (vulnerable missed, twin clean).

**Acceptance rule (training-set improvement):** accept iff the proposed harness solves at least
3 of the 4 training pairs **and** both cal_teamnotes and cal_mediakit remain solved. Otherwise
reject.

## Transfer subset (predeclared)

Rule: one previously missed and one previously detected pair, from calibration pairs not used as
training, with an unchanged task version and an existing T = 0 `none` reference.
- Previously detected: **cal_pricer** (the only candidate; cal_prefsync_v2 has a changed twin and
  no reference).
- Previously missed: **cal_invoicer**, chosen by alphabetical order among {cal_invoicer, cal_ssohub}.
  Its reference: vulnerable missed in both runs (one by reaching the reply limit), twin clean.

Run order is fixed (training, then transfer), and the budget check below covers the worst case,
so completion does not depend on execution order. If the guard still stops the run, the
incomplete results are reported as incomplete.

## Conservative budget check (before generation)

| Item | EUR |
|---|---|
| Upper-bound usage so far | 3.6528 |
| Guard | 4.95 → allowance 1.2972 |
| Proposer worst case (11,910 conservative input tokens + full output, ×3 for retries) | 0.0414 |
| Episode worst case (8 requests, each ≤ 8,600 input tokens and full 4,096 output) | 0.103 |
| Training 8 + transfer 4 episodes, worst case | 1.236 |
| **Total worst case** | **1.277 ≤ 1.2972** |

The full transfer set (4 pairs + 1 reference episode) would need 1.79 and does not fit; hence the
2-pair subset.
