# Protocol: memory-guided repository repair

Protocol version: `memory-v4`. The protocol was first written before any experiment ran
(`memory-v1`); every later change is listed in the [change log](#change-log). Changing anything
listed under [Frozen before held-out evaluation](#frozen-before-held-out-evaluation) requires a
new protocol version and a decision about the held-out benchmark, recorded in the change log.

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
- **First message.** It contains the task, the writable-file list, the guidance block (if any)
  and the pristine contents of the writable files and the public test file, up to
  `initial_context_chars` (12,000). A file that does not fit is omitted whole and must be read
  with `read_file`. The message is identical across conditions except for the guidance block.
  Hidden tests and reference solutions are never included.
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

## Change log

### memory-v4 (after the first memory-v3 build-memory attempt, `runs/study-pilot3`)

memory-v3 fixed the empty replies: every turn returned `finish=tool_calls` with no unparseable
reply. With native function calls, though, Gemini requested **one action per turn**. In the
`ms_booking` episode it spent all 8 model calls on these actions, in order:

1. list the files;
2. read `intervals.py`;
3. read `calendar.py`;
4. read the public tests;
5. run the tests;
6. run them again;
7. read `intervals.py` again;
8. read `calendar.py` again.

It never edited anything. The failure measures reading overhead, not repair or the effect of
memory.

Changes:

- **Files inlined.** The first message now includes the pristine writable files and public tests,
  up to `initial_context_chars` = 12,000 characters. That is everything the agent could already
  read with `read_file`, so no new information is exposed. In this benchmark every first message
  is about 700–1,100 estimated tokens.
- **Several actions per reply.** The system prompt states that several actions, or several
  function calls, can go in one reply.

**Trade-off.** Repository exploration is no longer measured for files that fit. Larger
repositories fall back to reading through tools. Conditions, limits, retrieval, tasks and scoring
are unchanged. The held-out decision below still applies.

### memory-v3 (after the first memory-v2 build-memory attempt, `runs/study-pilot2`)

memory-v2 never reached an evaluation. Its first live memory-source episode still received an empty
reply with `finish_reason` `function_call_filter: MALFORMED_FUNCTION_CALL`. That happened on turn 2,
after the first action results. So renaming the JSON keys was not enough: the model itself chooses
to emit a native function call, and with no functions declared the endpoint rejects it.

Changes:

- Every agent request now declares the seven actions as OpenAI-format functions (the `tools`
  field), generated from the same specification as the text protocol.
- A native function call returned by the API is converted by the adapter into the same text
  action format. The parser and every bound apply unchanged, and the number of native calls is
  recorded per request.
- The JSON text reply remains valid, and the system prompt allows either form.
- The extractor's requests declare no functions.

Conditions, limits, retrieval, tasks and scoring are unchanged. The open decision about the
held-out tasks (below) still applies.

### memory-v2 (after live pilot 1, `runs/study-pilot`)

Pilot 1 (`memory-v1`, `gemini-3.5-flash-lite`, 2026-10-22) was **invalid because of harness
defects**, not because of anything the conditions did:

- **Rejected replies.** The agent's replies were rejected when arguments sat next to the tool
  name instead of under `"args"`. The error message did not show the expected form.
- **Empty replies.** The protocol used the key `"tool_calls"`. Gemini's OpenAI-compatible
  endpoint answered many turns with empty content (`finish_reason`
  `function_call_filter: MALFORMED_FUNCTION_CALL`).
- **Consequences:**
  - 86 of 143 agent turns were unparseable;
  - both memory-source episodes ended without edits, so the memory bank was empty;
  - `learned_memory` therefore sent the same prompts as `baseline`.

Changes in memory-v2. Only the action format and reporting changed; no condition, limit,
retrieval setting or task changed:

- **New format.** Replies use `{"actions": [{"action": "read_file", "path": "..."}]}`. Arguments
  sit next to `"action"`.
- **Tolerant parser.** It also accepts `tool`/`name`, nested `args`/`arguments`, `tool_calls`, a
  single action object or a bare list.
- **Clearer prompt and errors.** The system prompt avoids "tool_calls" and "function". Error
  messages show an example reply.
- **Report warnings.** The report marks the primary comparison *not informative* in three cases:
  - the memory bank is empty;
  - no memory was retrieved for any `learned_memory` episode;
  - more than 25% of agent turns are unparseable.

The fix came from the failure *mode* (reply format), which was identical on the memory-source and
held-out tasks. No setting was tuned on held-out *outcomes*.

**Open decision: what to do with the held-out tasks.**

- **Reuse them.** Keep the 3 test tasks under memory-v2 and disclose pilot 1 (its data stays in
  `runs/study-pilot`).
- **Replace them.** Write new held-out tasks.

