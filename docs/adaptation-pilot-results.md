# One-proposal adaptation pilot — results

Run folder: `runs/sec/pilot-20261022T220809Z-93365a` (git-ignored; `pilot_log.json` holds the proposer prompt, raw response, hashes,
decisions). Plan: docs/adaptation-pilot-plan.md. Decision (committed before transfer):
docs/adaptation-pilot-decision.md. All 13 requests returned `gemini-3.5-flash-lite`. Temperature 0,
one run per program. The budget guard was not reached; every planned episode ran.

## Proposal (one request, admissible, frozen in `0d31a7c`)

- Target: `harness/instructions.md`; one added line: "Carefully verify all security-critical
  assumptions and sanitization routines: check whether prefix checks, string containment checks,
  escaping functions (e.g., handling of quotes or special characters), or character blocklists can
  be bypassed or fail to fully neutralize malicious input."
- Proposer's pattern: "The agent fails to notice subtle input sanitization bypasses or encoding
  assumptions in security checks, such as incomplete path containment checks or unescaped HTML
  attributes." It names no training identifier, but its wording mirrors the two missed training flaws.
- Hashes: original instructions `63aae510…`, proposed `b5ce363c…`; memory.md unchanged (empty).

## Training-set improvement check (not independent validation) → **REJECT**

| Pair | Vulnerable detected (ref → proposed) | Twin false positives (ref → proposed) | Solved (ref → proposed) |
|---|---|---|---|
| cal_docshare | no → no | 0 → 0 | no → no |
| cal_miniwiki | no → no | 0 → **1** (render_inline, CWE-79) | no → no |
| cal_teamnotes | yes → yes | 0 → 0 | yes → yes |
| cal_mediakit | yes → yes | 0 → **1** (describe, CWE-78, on the list-argument fix) | yes → **no** |

Solved 1/4 vs reference 2/4. The edit did not recover either targeted miss and added two false
alarms on fixed twins. Rule: accept iff ≥ 3/4 solved and both reference-solved pairs stay solved.

## Exploratory transfer (excluded from proposer inputs; previously calibrated, not a pristine test)

| Pair | Vulnerable detected (ref → proposed) | Twin (ref → proposed) | Solved (ref → proposed) |
|---|---|---|---|
| cal_invoicer (missing authorization, exploit witness) | no (2/2 runs) → **yes** (handle_export, CWE-285) | clean → **no submission** (reply limit) | no → no |
| cal_pricer (code injection, reachability witness) | yes → yes | clean → clean | yes → yes |

One run each: the invoicer detection is a single observation, under an edit that was rejected.

## Cost and latency

Pilot: reported €0.047, upper bound €0.642. Cumulative: reported €0.2915, upper bound €4.2946
(limit €5). Mean per episode: train 9,576 in / 245 out tokens, 4.8 calls, 4.0 s model latency;
transfer 10,447 / 194, 5.0 calls, 3.9 s (earlier runs the same evening took 9–18 s per episode;
the cause is unknown).

## Harness state

The rejected patch was reverted (`git revert`); restored file hashes equal the original
(`harness-v0`). The patch and its evaluation stay in history.
