# Calibration results (`sec-v1`, condition `none` only)

Date 2026-10-22. Model `gemini-3.5-flash-lite` (returned id identical on all 275 requests), default
thinking, max output 4,096 tokens. Calibration split only: 8 pairs, one per weakness class. Run
folders: `runs/sec/calibration-live-*` (git-ignored); every request is in `runs/sec/cost_ledger.jsonl`.

## What ran

| Run | Temperature | Episodes | Note |
|---|---|---|---|
| `…203726Z-03d1e6` | 0 | 2 | token-accounting check (cal_docshare pair) |
| `…204455Z-898a0a` | 0 | 32 | 8 pairs × 2 variants × 2 reps |
| `…205712Z-0943c9` | 0.7 | 16 | 8 pairs × 2 variants × 1 rep |
| `…210206Z-096a04` | 0.7 | 9 | second rep, **stopped by the budget guard** (last episode truncated) |

## Results

| | T = 0 | T = 0.7 |
|---|---|---|
| Detection recall (vulnerable variants) | 8/17 (47%) | 6/13 (46%) |
| Twins with no finding | 15/17 (88%) | 10/11 (91%) |
| False alarms on twins | 2 (both `cal_prefsync`) | 1 (`cal_prefsync`) |
| Non-submissions (reply limit) | 1 | 1 |
| Mean input / output tokens per episode | 9,461 / 178 | 10,107 / 178 |
| Model calls per episode | 4.6 | 4.8 |
| Model latency per episode | 18.2 s | 15.0 s |
| Reported cost per episode | €0.0033 | €0.0035 |

The budget-truncated episode is excluded from the table (it is a `budget_exhausted` no-submission).

Per task, the outcome was **identical in every repetition observed** (2–5 runs per program, at both temperatures):

| Task | Class | Witness | Vulnerable | Twin |
|---|---|---|---|---|
| cal_teamnotes | SQL injection | reachability | always found | always clean |
| cal_mediakit | OS command injection | reachability | always found | always clean |
| cal_prefsync | unsafe deserialization | reachability | always found | **always flagged** (pickle after HMAC check) |
| cal_pricer | code injection | reachability | always found | always clean |
| cal_docshare | path traversal | exploit | never found | always clean |
| cal_miniwiki | XSS | exploit | never found | always clean |
| cal_invoicer | missing authorization | exploit | never found | always clean |
| cal_ssohub | open redirect | exploit | never found | always clean |

`exploit` witnesses observe the harmful effect on planted data; `reachability` witnesses only show
that untrusted input reaches the dangerous call. Here the witness kind is confounded with the
weakness style: every found weakness is a visible dangerous call (SQL string, shell string,
pickle, eval); every missed one is incomplete validation or a missing check.

## Interpretation

- Difficulty: 47% recall for `none` is inside the 30–70% target; no adjustment round needed.
- **Outcomes were stable in the repetitions observed** (2–5 runs per program, T = 0 and 0.7), so
  in this small sample the variation between tasks was much larger than the variation between
  runs. That does not show that repetitions add nothing: a few runs cannot rule out rarer
  run-to-run flips, longer or different tasks may behave differently, and other uncertainty
  sources (task selection, labels, endpoint or model changes over time) remain. With 8 pairs,
  adding pairs is the more informative next step, and repetitions remain useful to check
  stability. Best-of-N sampling was not tested; its value is unknown.
- `cal_prefsync` twin: the fix moves `pickle.loads` after the signature check. Flagging signed
  pickle is a defensible reviewer opinion (a leaked key gives code execution), so this twin is
  ambiguous rather than clean. Proposed: replace pickle with JSON in that twin (documented as the
  one allowed label round), or keep it and report it as ambiguous.

## Token accounting

- Neither the OpenAI-compatible endpoint nor a native `generateContent` replay reported thinking
  tokens: `total = prompt + completion` in all 275 requests and no `thoughtsTokenCount`.
- Some short replies still take 10–23 s, which is unexplained.
- The cap is therefore enforced on an upper bound (every request billed at its full 4,096-token
  output allowance). Final ledger: **reported €0.198, upper bound €2.988** (cap €3, at $1 = €1).
  Provider billing has not been reconciled yet.

## Correction (2026-10-23)

An earlier version of the interpretation said that noise was "task-level, not run-level" and that
"more repetitions per task buy almost nothing". That overstated what 2–5 stable repetitions can
show; the paragraph above replaces it. The data are unchanged.
