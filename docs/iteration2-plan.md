# Iteration 2 — one proposal with feedback from the rejected edit

Status: **prepared and verified offline; blocked by budget, no paid call sent.**
Protocol `sec-v1.2`. Everything below was fixed and committed before any generation.

## 1. Inspection of the previous experiment (training pairs only)

All episodes on cal_docshare, cal_miniwiki, cal_teamnotes, cal_mediakit so far:

| Harness / condition | T | Episodes | True positives | False negatives | Twin false positives (episodes) | Twin clean | Non-submissions |
|---|---|---|---|---|---|---|---|
| original, no guidance | 0 | 18 | 4 | 5 | 0 | 9 | 0 |
| original, no guidance | 0.7 | 12 | 3 | 2 | 0 | 5 | 2 (reply limit; budget stop) |
| original + checklist | 0 | 8 | 2 | 2 | 0 | 4 | 0 |
| original + lessons | 0 | 8 | 2 | 2 | 0 | 4 | 0 |
| rejected edit (`0d31a7c`) | 0 | 8 | 2 | 2 | **2** | 2 | 0 |

- False negatives are always the same two programs (path containment, attribute escaping), under
  every harness tried. In each, the agent read the file containing the weakness and submitted an
  empty list.
- The rejected edit's only measurable effect on training was two new false positives on fixed
  twins (miniwiki `render_inline`, mediakit `describe`). The `reason` text of both findings
  contains the agent's own doubts ("Wait, let's examine…", "…uses _run which passes args as a list
  (shell=False). But wait…"). This is recorded as an observation; no cause is inferred.
- Short visible outputs are not evidence about reasoning. Until this iteration, assistant text that
  accompanied native tool calls was not logged at all; it is now logged (never sent back).

## 2. Allowed edit surface (bounded)

The proposer chooses one coherent change, starting from the original harness (`harness-v0`), to:
- `harness/instructions.md` (≤ 2,500 characters): reviewer instructions, e.g. a finding-verification
  instruction or structured checklist; and/or
- `harness/verification.md` (≤ 1,200 characters, empty in the original): a bounded verification
  step. If non-empty, the first `submit_findings` becomes a draft; the agent receives a fixed
  message (template in `secagent/agent.py`, read-only) with this text and its draft, may use the
  same read-only tools, and its next `submit_findings` is final. The step consumes the same
  shared limits (8 replies, 30 actions); if they run out after a draft, the draft counts and the
  episode ends as `submitted_draft`.

Unchanged and read-only: tasks, labels, witnesses, scoring, model and decoding settings, tool
permissions, sandbox, and every limit shared by baseline and candidate.

**Baseline identity check:** with `verification.md` empty, all 30 historical none/T = 0 episodes
were replayed through the new loop with their recorded replies: every request's messages, the
findings and the end reasons are identical (`secagent/replay.py`).

**Admissibility (automatic, no manual veto or correction):** the change must touch only the two
files, be within the limits, differ from the original, name no identifier from the training
programs (file stems, functions, classes, program names), contain no code fence, and share no
6-word sequence with the training answer explanations or READMEs. Judging "task-specific rules"
beyond these checks is not automated; the proposal is reported verbatim for review.

## 3. Proposer inputs (training only)

Original harness; the rejected line and its stated pattern; per training pair, original vs
rejected results (detection, twin findings, solved); the README threat model and planted weakness
of each training program; episode traces (actions, findings with the agent's own reasons) for the
original harness (`…898a0a`, repetition 1) and the rejected edit. No transfer program appears in
the prompt (asserted in code). It is asked for a plausible cause, one change, the predicted
improvement and the predicted risks. One request (up to 2 retries on transport errors only).

## 4. Evaluation design and acceptance rule (fixed)

- Training set: the 4 pairs above, temperature 0, one run per program.
- **Pair solved**: vulnerable detected and twin submitted with zero findings.
- **Accept iff all hold** (vs the baseline on the training pairs):
  1. solved pairs ≥ baseline + 1;
  2. no pair solved by the baseline becomes unsolved;
  3. total findings on twins ≤ baseline;
  4. non-submissions (episodes not ending in `submitted` or `submitted_draft`) ≤ baseline;
  5. all 8 candidate episodes complete.
  `submitted_draft` endings are reported separately.
- This is a **training-set improvement check**, not independent validation.
- If accepted: the frozen candidate on pairs excluded from proposer inputs (exploratory; they were
  previously calibrated and two were seen with the rejected edit by the experimenter, not by the
  proposer). If rejected: `git revert` of the frozen patch; evidence kept.

**Budget tiers**, highest that fits the conservative worst case is used:

| Tier | Training baseline | Transfer if accepted | Worst case (EUR, upper-bound basis) |
|---|---|---|---|
| A | fresh (8 episodes) | 4 pairs (invoicer, pricer, prefsync_v2, ssohub), candidate + fresh baseline | 3.367 |
| B | fresh | 3 pairs with a historical baseline (invoicer, pricer, ssohub), candidate only | 2.327 |
| C | historical (`…898a0a`, rep 1) | same 3 pairs, candidate only | 1.495 |
| D | historical | none (transfer deferred) | 0.871 |

Per-episode worst case €0.104 (8 requests at full 4,096-token output); proposer €0.039 (3 attempts).

## 5. Blocker

Upper-bound usage so far €4.2946 of the €5 project limit → allowance **€0.705**. The smallest
complete tier (D) needs €0.871. `python -m secagent.iter2 --live` was run and stopped before
creating a model client (`runs/sec/iter2-live-20261022T222638Z-5674c2`); the ledger is unchanged.
For reference only (the policy is unchanged): at the reported cost per episode observed so far
(≈ €0.004), tier A would cost about €0.13.

## 6. Verified offline

- Dry run `python -m secagent.iter2 --mock` exercised every step with a mock model and a scripted
  proposal (tier A), made no commits and restored the harness (hashes equal `harness-v0`).
- Unit tests: verification step (draft, fallback, empty = original), admissibility rules,
  acceptance rule. 119 tests pass.
