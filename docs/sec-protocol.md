# Protocol: self-improving vulnerability-detection agent (`sec-v1`)

Written and committed **before** any benchmark task, together with the frozen control texts
(`secagent/controls/`). Later changes go in the change log at the end, with a new version.

## Task

The agent audits a small local Python repository and reports security weaknesses. It does **not**
repair code. It has read-only tools (list, read, search) and one final action, `submit_findings`.
It has no network access and never sees the answer key, the witness tests, the task id, the split
or any advisory.

Each benchmark item is a **pair** from one family:

- `vulnerable`: contains exactly one planted, documented weakness (the ground truth, "GT");
- `patched` (twin): the same program with that weakness fixed by a minimal change. It is designed
  to contain no weakness. A finding on a twin is a **false alarm** for synthetic tasks. For real
  cases (later), the patched version may contain unknown weaknesses, so findings on it are reported
  as **unverified alarms**, not as false alarms.

The two members of a pair are always in the same split.

## Finding format

```json
{"file": "pkg/storage.py", "function": "resolve", "lines": [40, 47], "cwe": "CWE-22",
 "source": "where untrusted input enters, e.g. api.handle_download(name)",
 "path": ["handle_download", "fetch", "resolve"], "reason": "why validation is insufficient"}
```

`source`, `path` and `reason` are required in the output. Only structured fields are scored;
`reason` and `source` are kept for manual audit (no LLM judge, to avoid adding judge noise).

## Matching rule (fixed)

A finding `f` **matches** the GT `g` iff all three hold:

1. **file**: `f.file` equals `g.file` after normalising (`./` stripped, POSIX separators);
2. **location**: `f.function` equals `g.function` (a `Class.method` form matches on the last
   component), **or** `f.lines` intersects the line span of `g.function`;
3. **type**: `f.cwe` is in the accepted set of `g`'s weakness class (table below).

The GT is matched at most once (first matching finding in submission order). Every other finding is
an alarm. A finding that satisfies 1 and 2 but not 3 is additionally counted as **misclassified**.

| Class | Accepted CWE ids |
|---|---|
| path traversal | 22, 23, 36, 73 |
| SQL injection | 89, 943 |
| OS command injection | 78, 77, 88 |
| unsafe deserialization | 502 |
| cross-site scripting | 79, 80 |
| missing authorization | 862, 863, 639, 285, 284 |
| open redirect | 601 |
| code injection | 94, 95 |

## Metrics

Per episode (one agent run on one program):

- vulnerable program: `detected` (GT matched), `extra_alarms` (unmatched findings);
- twin: `clean` (zero findings), `alarms` (number of findings);
- `misclassified`; `path_coverage` = |f.path ∩ g.path| / |g.path| for the matching finding;
- model calls, tool calls, parse errors, billed input/output tokens, cost, latency.

**Primary score** (fixed): per pair and repetition, `pair_score = (detected + clean) / 2`, in
{0, 0.5, 1}. Flagging everything gives 0.5 and flagging nothing gives 0.5, so the score rewards
discrimination, not volume. Reported alongside: detection rate, twin false-alarm rate, precision
(matched findings / all findings), misclassified, path coverage.

## Repetitions and uncertainty

- `k` means **independent repetitions** of the same episode with the same settings, not best-of-k.
  Best-of-N is a separate control condition with a selection rule that never sees the answer key.
- Calibration uses 2 repetitions. Phase 1 uses the `k` fixed after calibration.
- Uncertainty: 95% percentile bootstrap over **pairs** (families), 10,000 resamples, with the
  repetitions of a pair kept together. Condition comparisons are **paired** by pair.
- A difference whose 95% interval contains 0 is reported as **inconclusive**. No p-values.

## Splits (by weakness class and by family)

| Split | Content | Role |
|---|---|---|
| `calibration` | 8 pairs, one per class | difficulty and noise check only; never used for lessons or selection |
| `train` | seen classes, 2 pairs each | the only traces the Phase 2 proposer sees |
| `gate` | seen classes, 1 pair each | scores only, used to accept/reject edits (a validation set) |
| `test_seen` | seen classes, 1 new pair each | final: do gains generalise to new programs? |
| `test_unseen` | unseen classes, 2 pairs each | final: do gains transfer to new classes? |
| `real` | 3–5 recent real fixes (later) | final only; reported per case, never pooled |

Seen classes: path traversal, SQL injection, OS command injection, unsafe deserialization, XSS.
Unseen classes: missing authorization, open redirect, code injection.

## Conditions (Phase 1)

All conditions use the same `harness/instructions.md`, tools, limits and model settings. Only the
content under the heading `## Additional guidance` in the first message differs:

| Condition | Guidance |
|---|---|
| `none` | empty |
| `checklist` | `secagent/controls/checklist.md` (generic) |
| `lessons` | `secagent/controls/lessons.md` (hand-written, seen classes only) |

The control texts were written from public CWE/OWASP descriptions before any benchmark task was
written. Disclosure: the author had already outlined the calibration tasks (not their code) when
writing them, and writes all tasks and answer keys.

## Difficulty rule

Target: `none` detection rate between 30% and 70% on calibration. At most **one** documented round
of difficulty adjustment; the result is reported whatever it is.

## Phase 2 (draft, frozen before Phase 2 starts)

- Editable: `harness/instructions.md` and `harness/memory.md` only. Everything else is read-only
  and is checked with a diff before any edit is accepted.
- The proposer sees `train` traces only; never gate/test traces, answer keys or witness tests.
- At most 10 proposals. Acceptance rule to be fixed before the first proposal.

## Model and cost

- Model: `gemini-3.5-flash-lite`, the only (stable) code for this model; API `version` field
  `3.5-flash-lite-07-2026`, released 2026-07-21, knowledge cutoff March 2026 (model card).
  Google states stable models "usually don't change": this is not a guarantee of fixed weights.
- Every request records the requested model id, the returned model id and the raw usage.
- Cost is metered from billed usage with a reservation before each request; see `secagent/llm_ext.py`.

## Change log

- `sec-v1`: initial protocol.
