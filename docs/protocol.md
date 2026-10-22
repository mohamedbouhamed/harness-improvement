# Protocol: memory-guided repository repair

Protocol version: `memory-v1`. This document is written before the experiment is run. Changing
anything listed under [Frozen before held-out evaluation](#frozen-before-held-out-evaluation)
requires a new protocol version and a new held-out benchmark version.

## Research question

> Can reusable lessons extracted from previous repair attempts improve performance on unseen
> repository-repair tasks?

"Adaptation" here means retrieving stored lessons into the prompt. The agent's code, the model and
its weights never change.

## Conditions

| ID | Name | Additional context in the first prompt |
|---|---|---|
| A | `baseline` | none |
| B | `static_guidance` | a fixed, manually written repair checklist ([harness_mvp/guidance.py](../harness_mvp/guidance.py)) |
| C | `learned_memory` | lessons retrieved from the frozen memory bank for this task; possibly none |

B and C put their content under the same heading (`## Additional repair guidance`) in the same
position in the first user message, so only the content differs. Both share one budget of
`budget_tokens` estimated tokens. The actual size of each injection is recorded per episode, and
prompts are never padded to equal length.

**Token estimate.** Model-native token counting is not available for every endpoint, so budgets
use the conservative estimate `ceil(characters / 3)`. Real API token usage is reported separately
and is never mixed with this estimate.

## Fixed across conditions

The following are identical for all three conditions:

- **Model and decoding:** model ID, temperature and maximum output tokens, all taken from the
  environment and recorded in every manifest.
- **Agent limits** (`AgentLimits` in [harness_mvp/contracts.py](../harness_mvp/contracts.py)):
  - at most 8 model calls per episode;
  - at most 24 tool calls per episode, and at most 4 per model response;
  - at most 4 public-test runs;
  - caps on tool-result, read and write sizes;
  - a 10 s execution timeout per test run.
- **Docker execution limits:** no network, unprivileged user, read-only root, memory, CPU and
  process caps.
- **Tools and the system prompt.**

## Split roles

| Split | Role | Hidden tests used for |
|---|---|---|
| `calibration` | debugging the agent, checking difficulty | reporting only |
| `memory_source` | repair episodes from which lessons are extracted | reporting only, **never** extraction |
| `development` | choosing retrieval settings (optional selection step) | reporting only; selection uses **public** outcomes |
| `test` | final held-out evaluation, after everything is frozen | final scoring |

**Leakage rules:**

1. Splits are declared per task in `benchmark/manifest.json`.
2. Every task has a `family`. Near-duplicates, variants of one implementation, and repositories
   sharing source code must share a family, and a family belongs to exactly one split. This is
   checked automatically.
3. General principles (for example "check inclusive boundaries") may recur across independently
   authored tasks in different splits. That recurrence is what makes transfer measurable.
4. Each task records maintainer-only `principles` labels. They define which held-out tasks are
   *lesson-relevant* (they share a principle with some memory-source task) and which are
   *lesson-irrelevant*. Labels are used only for reporting, never by the agent, the extractor or
   retrieval.
5. Memories are extracted only from `memory_source` episodes. The bank builder rejects any other
   provenance, and evaluation rejects a bank containing any.
6. Extractor inputs are restricted to the task specification, the source changes, the agent's
   actions and the public-test feedback. Hidden-test contents and outcomes are excluded by
   construction (a whitelist of fields), and a test enforces this.
7. Held-out (`test`) attempts never feed memory construction or selection.
8. Retrieval may return no memory. The relevance threshold makes empty retrieval possible and
   expected for unrelated tasks.

## Episode

- **Fresh state.** Each (task, condition, repetition) gets a fresh workspace and a fresh
  conversation. Nothing is shared across conditions, including the first candidate: guidance
  changes the first prompt, so it may act from the first model call.
- **Tools:**
  - `list_files`, `read_file` and `search` work inside the episode workspace only;
  - `write_file` and `replace_in_file` work on declared `allowed_files` only;
  - `run_public_tests` runs in Docker;
  - `submit` ends the episode.
- **Tests stay pristine.** Every execution is assembled from the pristine repository plus the
  current contents of the allowed files.
- **Hidden tests are out of reach.** They are never in the workspace. They are scored in a
  separate container after the episode ends.
- **Public-test fallback.** If the final candidate fails the public tests and an earlier candidate
  passed the complete public suite, the latest public-passing candidate is submitted instead.
  Otherwise the final candidate is submitted. The pre-fallback candidate is kept for analysis.
  Hidden scores are never used to choose a candidate.
- **API errors.** An API error ends the episode. The current workspace state is submitted, with
  the fallback rule applied, and the error is recorded separately from code failures.

## Memory construction

1. **Memory-source episodes.** Run the agent under the `baseline` condition on `memory_source`
   tasks.
2. **Extraction.** One extractor call per episode, with whitelisted inputs only. It returns at most
   3 lessons with the fields `trigger`, `lesson` and `scope`.
3. **Validation:**
   - required fields and types;
   - length limits;
   - no code fences;
   - the lesson must not copy source lines.
4. **Flagging.** Lessons that name task-specific identifiers (function, class or module names from
   the source repository) are flagged and excluded from the bank.
5. **Bank.** Lessons are deduplicated: identical normalized text, or token Jaccard similarity ≥ 0.8
   on trigger plus lesson, merges their evidence. Each gets a content-derived `memory_id` and full
   provenance:
   - source episode IDs and split;
   - extractor configuration;
   - creation time;
   - task and candidate hashes.

   The bank is written once and identified by its SHA-256.
6. **Evidence is incomplete.** A generated lesson is a hypothesis, not a verified fact: public tests
   are incomplete evidence.

## Retrieval

- **Query.** The new task's title and statement.
- **Score.** Keyword overlap with fixed weights:
  `2 × |query ∩ trigger| + |query ∩ scope| + |query ∩ lesson|`, after lower-casing, stop-word
  removal and light suffix stripping (`-ies`→`-y`, `-ing`, `-ed`, `-es`, `-s`).
- **Selection.** Memories need `score ≥ min_score`. They are ordered by (−score, `memory_id`).
  Up to `max_memories` are taken, adding whole lessons while they fit the context budget.
- **Recording.** Each episode records the selected IDs, their scores and the exact injected text.
- **Defaults.** `max_memories = 3`, `min_score = 3`, `budget_tokens = 300`.
- **Optional development selection.** This step may choose `min_score` from `{2, 3, 4}`. The
  criterion is the number of development episodes whose *submitted* candidate passes the public
  tests. Ties go to fewer total API tokens if they are known for every setting, then to the higher
  threshold. Its model-call budget is printed before it runs.

## Frozen before held-out evaluation

`freeze` writes `frozen_protocol.json` containing:

- the protocol version;
- the memory-bank hash;
- the static-guidance hash;
- the retrieval settings and agent limits;
- the model settings;
- the test task IDs and hashes.

Evaluation on `test` refuses to start unless every item matches. Further rules:

- Memory is read-only during evaluation. Its hash is checked before and after every episode.
- A study may complete only one held-out evaluation. More tuning needs a new benchmark version.
- An aborted held-out evaluation, for example from API errors, may be resumed into a new
  directory. Both are reported.

## Primary comparison (predeclared)

The primary comparison is **learned memory (C) vs baseline (A) on final hidden-test success over
all held-out (task, repetition) pairs**, reported as raw counts per condition and per task.

- **Control.** Static guidance (B) controls for the value of generic additional instructions.
- **Secondary, descriptive measures:**
  - results on the lesson-relevant and lesson-irrelevant subsets;
  - public success, fallback activations, raw regressions, repair activity, model and tool calls,
    tokens, latency, timeouts and infrastructure errors.
- **No statistical claims.** No significance or generalization claim is made from a pilot. Null
  and negative results are valid.
- **Costs are reported separately:** memory construction (memory-source episodes, extraction,
  optional selection) versus evaluation.

## Tiny pilot dataset

| Split | Tasks | Principles (maintainer labels) |
|---|---|---|
| calibration | `cal_textstats` | boundary |
| memory_source | `ms_booking`, `ms_leaderboard` | boundary · tie-ordering |
| development | `dev_inventory` | boundary, state-propagation |
| test | `test_rate_window` (relevant), `test_standings` (relevant), `test_config_merge` (irrelevant) | boundary · tie-ordering · aliasing |

All tasks are independently authored, each is its own family, and each repository has 2–3
interacting source files with cross-function bugs. The dataset will be expanded only after the
complete pipeline works.
