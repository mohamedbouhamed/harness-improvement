# Automatic harness adaptation for vulnerability detection

A small, honest pilot of a security agent that improves its own harness. A fixed LLM agent
(`gemini-3.5-flash-lite`) audits small local Python programs with read-only tools and reports
weaknesses. An automatic proposer edits the agent's instructions (and, later, a verification
step) from training traces only; each edit is frozen before evaluation, accepted or rejected by
a rule fixed in advance, and recorded with its evidence.

> **Scale warning.** Everything here is a pilot on 9 hand-written program pairs. One run per
> program at temperature 0. Results are reported per task; none of them establishes a general
> improvement.

## Results so far

| Experiment | Status | Outcome |
|---|---|---|
| Calibration (8 pairs, original harness) | done | Detection 47% at T = 0; outcomes stable in the 2–5 repetitions observed |
| Guidance check (checklist / hand-written lessons, 4 pairs) | done | No outcome changed |
| Automatic proposal 1 (instructions edit) | done, **rejected** | Training pairs solved 2 → 1; added two false positives on fixed twins |
| Automatic proposal 2 (feedback from proposal 1; instructions + verification step) | done, **accepted on training only** | Training pairs solved 2 → 3 vs a fresh baseline, no new false positives or non-submissions |
| Transfer of proposal 2 (3 pairs excluded from proposer inputs, previously calibrated) | done | Unchanged, 1 → 1: no evidence of transfer |
| A/B/C ablation on a fresh evaluation set | **planned, not run** | See "Planned" below |

Total cost so far: **$0.372 reported provider usage** (reconciled with the user's billing at two
points); the conservative upper-bound ledger (every request at full output allowance) is €5.40 and
is kept separately (docs/cost-reconciliation.md).

### Completed experiments in more detail

- **Task.** Each benchmark item is a pair: a vulnerable program with exactly one planted weakness
  and a minimally fixed twin. A pair is *solved* when the weakness is reported (fixed matching
  rule) **and** nothing is reported for the twin. Detection, twin false positives and
  non-submissions are always reported separately.
- **Labels are executed, not asserted.** Every pair is checked in Docker (no network, read-only,
  unprivileged): functional tests pass on both versions; a witness test fails on the vulnerable
  version through an assertion and passes on the fixed one. Witnesses are either `exploit`
  (the harmful effect is observed on planted test data) or `reachability` (untrusted input is
  shown to reach the dangerous call); they contain no attack payloads and the agent never sees them.
- **Calibration** (docs/calibration-results.md). The original harness found weaknesses that
  sit in a visible dangerous call and missed those that require judging incomplete validation or
  a missing check. Outcomes did not change across the repetitions observed; this does not show
  that repetitions are useless, and best-of-N was not tested.
- **Guidance check** (docs/guidance-check-plan.md). Neither a generic checklist nor hand-written
  per-class lessons changed any of 8 outcomes, even where the lessons nearly described the flaw.
- **Proposal 1** (docs/adaptation-pilot-*.md). One proposal from the same model; it named the
  missed flaw categories, recovered neither miss, and added two false positives. Rejected by the
  predeclared rule, reverted, kept in history.
- **Proposal 2** (docs/iteration2-*.md). The proposer received the rejected edit and its
  before/after training results. It rewrote the instructions toward source-to-sink tracing and
  added a verification step. Accepted on training (2 → 3 pairs, fresh baseline, same session).
  Caveats found afterwards (decision unchanged, as fixed in advance): the instructions name the
  categories of the training misses; the verification text mentioned "fixed or patched versions"
  (benchmark-design vocabulary); the verification step changed no finding, so the gain came from
  the instructions. Transfer on three excluded pairs was unchanged.

## Planned: A/B/C ablation (not run)

Frozen so far: three immutable harness variants (docs/harness-variants.md) — **A** original,
**B** proposal-2 instructions without verification, **C** proposal-2 instructions plus a
human-edited verification step with benchmark vocabulary removed — and scorer v2.

Still open: a fresh evaluation set of six pairs (three categories seen in training, three
excluded), the comparison runner, and a paid run within a €1 batch ceiling. Writing new
vulnerable programs in-house was stopped by the assistant's safety filter. Reusing an existing
dataset is being investigated: CodeSecEval's dataset licence is "under review" (not usable);
CWEval (Apache-2.0) has Python pairs with functionality and security tests but needs
adaptation and is probably in the model's training data.

## Setup and commands

Python 3.11+, standard library only; Docker for label checks and for any model-generated code.
Live runs need an OpenAI-compatible endpoint in `.env` (see `.env.example`; never committed).

```bash
python -m unittest discover -s tests -t .          # offline tests
python -m secagent.reproduce                       # 10 checks of the historical decisions, no model calls
python -m secagent validate --split calibration    # label checks in Docker (builds the runner image if missing)
python -m secagent estimate --split calibration --conditions none --reps 1
python -m secagent run --mock --split calibration --conditions none --reps 1   # SYNTHETIC dry run
```

Paid runs require `--live`, prices confirmed in `secagent/prices.json`, and a euro cap; every
request records the requested and returned model id and the provider-reported usage.

## Safeguards

- Protocol and control texts committed before any task existed (docs/sec-protocol.md, versioned).
- The proposer sees training data only; transfer and evaluation programs are asserted absent
  from its prompt. Edits may touch only `harness/instructions.md` and `harness/verification.md`;
  a diff check enforces it. Inadmissible proposals (identifiers, code, copied answer text) are
  rejected automatically, with no manual correction.
- Patch frozen and committed before evaluation; decision committed before transfer; rejected
  patches reverted but kept.
- Scorer v2 closes a v1 loophole (broad line ranges); rescoring all 109 stored episodes changed
  no outcome (docs/rescoring-v2.md).
- All live runs are committed under `results/live/` and replayable without model calls.

## Limitations

- Nine small, hand-written programs by the same author as the answer keys; one model; one run
  per program; four training pairs. A one-pair difference is weak evidence.
- Proposal 2 is plausibly fitted to its training misses; transfer is unshown.
- Calibration pairs reused for pilots are previously seen by the experimenter.
- Thinking tokens are not reported by the endpoint; billing matched reported usage at two points.
- Docker isolation is for controlled tasks, not a hardened sandbox.

## Related work (claims checked against the arXiv abstracts)

- **Meta-Harness** (Lee et al., arXiv:2603.28052, 2026): searches over harness code with an
  agentic proposer that reads the source, scores and execution traces of all prior candidates.
  Our proposer sees far less: summaries of a few training episodes.
- **Self-Harness** (Zhang et al., arXiv:2606.09498, 2026): an agent improves its own harness
  through weakness mining, harness proposal and regression-tested validation, without stronger
  external agents. Our loop follows this shape at a much smaller scale with the same model.
- **Agentic Harness Engineering, AHE** (Lin et al., arXiv:2604.25850, 2026): observability for
  harness evolution — reversible component edits, condensed experience, and decisions written as
  predictions checked against outcomes; reports that factual harness structure transfers while
  prose-level strategy does not. Our proposals are prose-level, and did not show transfer.
- **ACE** (Zhang et al., arXiv:2510.04618, ICLR 2026): contexts as evolving playbooks built by
  generation, reflection and curation, with incremental updates to avoid context collapse.
- **SelfOp** (Ullah et al., arXiv:2609.22792, 2026): improves a frozen security agent's task
  context (instructions, skills, reference documents) without changing its harness or weights,
  using a textual-gradient method; the closest setting to ours (security agents, no expert
  demonstrations).

## Provenance

The code, documents and experiment design were written with an AI coding assistant under the
author's direction; the history was rewritten once to remove commit trailers, which does not
change how the work was produced (docs/provenance.md, docs/commit-hash-map.md). The earlier
memory-guided repair study in `harness_mvp/` is described in docs/memory-repair-study.md.

## Repository map

| Path | Content |
|---|---|
| `secagent/` | detection agent, scorers, cost meter, proposers, runners, reproduce |
| `benchmark_sec/` | program pairs, answer keys, witness tests |
| `harness/`, `harness_variants/` | current (accepted) harness; frozen A/B/C variants |
| `results/live/` | every live run: episodes, transcripts, logs, cost ledger |
| `docs/` | protocol, plans, results, decisions, reconciliation, provenance |
| `harness_mvp/`, `benchmark/` | earlier repair study and shared infrastructure |
