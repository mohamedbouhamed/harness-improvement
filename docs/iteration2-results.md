# Iteration 2 — results (Tier B)

Run `runs/sec/iter2-live-20261022T222908Z-f01c21` (git-ignored; `iter2_log.json` holds the proposer
prompt and raw reply, the patch diff, hashes and every decision). Plan and fixed rules:
docs/iteration2-plan.md. 101 requests, all returned `gemini-3.5-flash-lite`, all succeeded.
Temperature 0, one run per program, same limits for baseline and candidate.

## The proposal (one request, admissible, frozen in `8bebe79` before evaluation)

Proposer's stated cause: the previous broad warning "induced hallucinations on fixed versions",
and the remaining misses come from "surface-level keyword/grep searches … rather than
systematically tracing untrusted data flow". Predicted risk: the verification step may make the
agent drop valid findings, or still produce false positives.

Exact diff against `harness-v0`:

```diff
--- a/harness/instructions.md
+++ b/harness/instructions.md
-- Read the README first to learn which inputs are untrusted, then read the code that handles them.
-- Follow untrusted data across functions and files to the operation where it is used.
-- The repository may contain no vulnerability at all. Report only weaknesses with a concrete path from an untrusted input to a sensitive operation; do not report style issues, missing hardening, or risky calls that only receive trusted data.
+- Read the README first to learn which inputs are untrusted, then systematically trace every untrusted input parameter across functions and files to its sink (file system operations, command execution, database queries, template/HTML rendering, or redirection).
+- Pay close attention to data transformation routines: verify whether path normalization, prefix checks, blocklists, or escaping functions (such as attribute quoting) can be bypassed or fail to neutralize malicious payloads.
+- The repository may contain no vulnerability at all. Report only weaknesses with a concrete, unbroken path from an untrusted input to a sensitive operation; do not report style issues, missing hardening, or risky calls that only receive trusted data.
--- a/harness/verification.md   (empty before)
+++ b/harness/verification.md
+Your findings are not final yet. Verification step:
+Review your draft findings against the repository code. For each reported finding, explicitly re-verify:
+1. Is the data source definitely untrusted according to the README threat model?
+2. Is there a complete, unconstrained path from input to sink without intermediate sanitization or validation that blocks the attack?
+3. On fixed or patched versions, double-check whether the code already correctly neutralizes the input.
+
+If any finding relies on assumptions that do not hold, remove it. If the findings are robust, confirm them.
```

Hashes: instructions `3685c971…` (original `63aae510…`), verification `ebc388e2…` (original empty).

## Training-set improvement check (not independent validation) → **ACCEPT**

Fresh baseline (original harness) and candidate, same session, baseline first.

| Pair | Vulnerable detected (baseline → candidate) | Twin findings (baseline → candidate) | Solved (baseline → candidate) |
|---|---|---|---|
| cal_docshare (path traversal) | no → no | 0 → 0 | no → no |
| cal_miniwiki (XSS) | no → **yes** (`attr`, CWE-79) | 0 → 0 | no → **yes** |
| cal_teamnotes (SQL injection) | yes → yes | 0 → 0 | yes → yes |
| cal_mediakit (command injection) | yes → yes | 0 → 0 | yes → yes |
| **Total** | 2 → 3 | 0 → 0 | **2 → 3** |

Non-submissions 0 → 0. All five fixed criteria hold. The fresh baseline reproduced the
historical baseline exactly.

Verification step behaviour: in all 7 candidate training episodes that reached it, the final
findings equal the draft; one episode (teamnotes twin) first submitted on its last allowed reply,
so no verification turn was possible. **The gained detection (miniwiki) was already in the draft**:
it came from the rewritten instructions, not from the verification step.

| | Baseline | Candidate |
|---|---|---|
| Input / output tokens per episode | 7,938 / 176 | 12,693 / 261 |
| Model calls per episode | 3.75 | 5.38 |
| Model latency per episode | 2.5 s | 3.8 s |
| Reported cost (8 episodes) | €0.0226 | €0.0357 |

## Exploratory transfer (candidate only; historical baseline `…898a0a` rep 1)

Pairs excluded from proposer inputs but previously calibrated; not a pristine test.

| Pair | Vulnerable detected (historical → candidate) | Twin findings | Solved (historical → candidate) |
|---|---|---|---|
| cal_invoicer (missing authorization) | no → no | 0 → 0 | no → no |
| cal_pricer (code injection) | yes → yes | 0 → 0 | yes → yes |
| cal_ssohub (open redirect) | no → no | 0 → 0 | no → no |
| **Total** | 1 → 1 | 0 → 0 | **1 → 1** |

No change on transfer: no gain, no new false positive, no non-submission. 4.5 calls, 9,399 input /
176 output tokens and 3.2 s per episode.

## Interpretation

- **Demonstrated:** on the four training pairs, the accepted harness solved one more pair than a
  fresh run of the original harness (2 → 3) without new false positives or non-submissions, under
  identical limits. This is a training-set result from one run per program (n = 4 pairs); a
  one-pair difference is weak evidence on its own.
- **Not demonstrated:** transfer. On three excluded pairs the result is unchanged (1/3 → 1/3), so
  transfer is **inconclusive / no evidence of improvement**.
- **Caveats found after the run (acceptance unchanged, as fixed in advance):**
  - The new instruction names the categories of the training misses ("prefix checks", "escaping
    functions (such as attribute quoting)"), and the gained pair is the attribute-quoting one. It
    passed the automatic checks (no identifiers, no copied 6-word phrases), but it is plausibly
    fitted to the training set.
  - The verification text mentions "fixed or patched versions", which reveals the benchmark's pair
    design to the agent. The admissibility checks did not cover this; they should reject
    benchmark-design vocabulary in future iterations.
  - The verification step changed no finding in this run; its effect is untested.
- **Hypotheses:** that systematic source-to-sink tracing helps beyond this training set; that the
  verification step reduces false positives (no false positives occurred to reduce).

## Cost

| | Reported (provider usage) | Conservative upper bound |
|---|---|---|
| Iteration 2 (101 requests, 226,390 input / 5,157 output tokens) | €0.0808 | €1.1022 |
| Proposer request alone | €0.0030 | — |
| **Cumulative project** | **€0.3723** | **€5.3968** (guard €6.63 for this iteration) |

## Harness state

Accepted: the harness at `8bebe79` (tag `harness-v1`) is the new current harness. The original is
tag `harness-v0`. No further iterations were run.
