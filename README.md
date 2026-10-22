# Memory-guided repository repair

A small research prototype. A fixed LLM agent repairs bugs in small Python repositories through a
bounded tool interface. We ask:

> **Can reusable lessons extracted from previous repair attempts improve performance on unseen
> repository-repair tasks?**

The study compares three conditions on the same held-out tasks:

| Condition | Additional context in the first prompt |
|---|---|
| `baseline` | none |
| `static_guidance` | a fixed, manually written repair checklist (control for "generic extra instructions") |
| `learned_memory` | lessons retrieved from a frozen memory bank built from earlier repair attempts (possibly none) |

Adaptation happens only through retrieved memory: the model, its weights and the agent's code never
change. The full predeclared protocol is in [docs/protocol.md](docs/protocol.md).

> **Exploratory pilot.** The benchmark is tiny: 7 tasks, of which 3 are held out. Results cannot
> establish significance or generalization. Null and negative results are valid outcomes.

## Status

| Implemented and verified offline | Planned (not implemented) |
|---|---|
| Bounded tool-using repair agent in per-episode workspaces; Docker execution | A larger benchmark (20–50 tasks) with more repetitions and uncertainty estimates |
| Public-test fallback without any access to hidden scores | Embedding retrieval or other memory variants |
| Whitelisted lesson extraction (LLM and deterministic mock), validation, flagging, dedup, hashed bank | Memory selection beyond the retrieval threshold |
| Keyword retrieval with a threshold and context budget; fixed static checklist | Security-oriented repair tasks |
| Freezing, held-out evaluation, study report; mock end-to-end demo | |

**No live result exists yet.** All numbers produced so far come from mock runs and are labelled SYNTHETIC.

## Architecture

```
benchmark/manifest.json + tasks/<id>/{repo, hidden, solution, mock}
        │
        ▼
repo_agent.run_episode ── tools.py (list/read/search/write/replace/run_public_tests/submit)
   │       │                └─ workspace.py: fresh copy of repo/, path + write restrictions
   │       └─ llm.request ── model.py (OpenAI-compatible adapter, CallBudget)  |  mock.py
   └─ runner.py: every execution in Docker (fresh read-only mount of pristine repo + candidate)

experiment.py (study stages)
  memory_source episodes ─► memory.py extraction (whitelist) ─► validated, deduplicated, hashed bank
  ─► [development selection] ─► freeze ─► evaluation: guidance.py builds the context per condition
  ─► study_report.py
```

| Module | Role |
|---|---|
| `contracts.py` | protocol version, fixed limits, retrieval settings, token estimate, validators |
| `benchmark.py` | loads and validates tasks (splits, families, file layout) |
| `workspace.py`, `tools.py`, `repo_agent.py` | the bounded agent and its episode loop |
| `memory.py`, `guidance.py` | extraction, the memory bank, retrieval and the static checklist |
| `experiment.py`, `study_report.py`, `main.py` | stages, freezing, the CLI and reports |
| `model.py`, `llm.py`, `runner.py`, `mock.py` | model adapter, request budget and retries, Docker runner, mock backends |

## Setup

- Python 3.11+, standard library only (no `pip install`).
- Docker, for anything that executes model-generated code.
- For live runs: an OpenAI-compatible endpoint. Copy `.env.example` to `.env` and set
  `HARNESS_BASE_URL`, `HARNESS_MODEL` and `HARNESS_API_KEY`. `.env` is git-ignored and credentials
  are never logged.

## Commands

```bash
python -m unittest discover -s tests -t .              # all offline tests
python -m harness_mvp smoke --mock                     # benchmark + runner + fallback checks, no model calls
python -m harness_mvp pipeline --mock --output runs/study-mock     # full SYNTHETIC workflow
python -m harness_mvp report runs/study-mock           # regenerate the study report

python -m harness_mvp smoke                            # live preflight: credentials + Docker, no model calls
python -m harness_mvp plan [--select] [--test-reps N]  # worst-case request budgets, no model calls
```

Every workflow command requires `--mock` or `--live`; nothing paid starts by accident. The live
workflow can run in one command or in stages:

```bash
python -m harness_mvp pipeline --live --output runs/study-pilot --min-interval 5 --api-retries 2 --max-model-calls 200
# or, stage by stage:
python -m harness_mvp build-memory --live --study runs/study-pilot
python -m harness_mvp select       --live --study runs/study-pilot     # optional, development split
python -m harness_mvp freeze       --live --study runs/study-pilot --test-reps 2
python -m harness_mvp evaluate     --live --study runs/study-pilot --split test
```

### Live request-budget bounds

There is a hard cap: `--max-model-calls` limits **every** model request, including API re-sends,
and is checked before each request. The worst case below assumes 8 model calls per episode and no
re-sends:

| Part (default pilot) | Requests |
|---|---|
| memory-source episodes: 2 tasks × 1 rep × 8 | 16 |
| extraction: 1 call per memory-source episode | 2 |
| development selection (`--select`): 3 thresholds × 1 task × 1 rep × 8 | 24 (optional) |
| held-out evaluation: 3 tasks × 3 conditions × 2 reps × 8 | 144 |
| **total** | **162** (186 with `--select`) |

With `--api-retries N`, re-sends count against the same cap, so the command above can never send
more than 200 requests. Each request is limited to `HARNESS_MAX_TOKENS` output tokens. Input size
grows with the conversation; tool results are capped at 4,000 characters each. The report gives
the actual API token usage, kept separate from the prompt-budget estimates.

## Split roles and leakage prevention

| Split | Tasks | Use |
|---|---|---|
| `calibration` | `cal_textstats` | agent debugging, smoke tests |
| `memory_source` | `ms_booking`, `ms_leaderboard` | the only source of lessons |
| `development` | `dev_inventory` | optional retrieval-threshold selection, on **public** outcomes |
| `test` | `test_rate_window`, `test_standings`, `test_config_merge` | final evaluation after freezing |

These rules are enforced in code, and the tests check them:

- **Families.** Every task has a family. A family never crosses splits.
- **Maintainer labels.** `principles`, `bug_note` and mock fields are never shown to the agent,
  the extractor or retrieval. `principles` only defines the lesson-relevant and lesson-irrelevant
  subsets in the report.
- **Workspaces:**
  - an episode's workspace contains only `repo/`;
  - paths are relative and confined, and symlinks are rejected;
  - writes are limited to the declared files;
  - executions rebuild the pristine tests every time.
- **Hidden tests.** They run in a separate container after the last model call, and never reach a
  prompt, a tool, the fallback or the extractor.
- **Extraction.** Its input is a whitelist of the task spec, the source diff, the agent's actions
  and public-test feedback. Only `memory_source` episodes are accepted, and the bank and the
  evaluation reject any other provenance.
- **Freezing.** `freeze` pins the bank hash, the checklist hash, the retrieval settings, the agent
  limits, the model settings and the test-task hashes. Held-out evaluation refuses anything that
  differs.
  - The bank hash is checked before every episode and after the run.
  - A study allows one completed held-out evaluation. An aborted one may be resumed into
    `eval-test-2`.

## Memory construction and retrieval

1. **Memory-source episodes.** Run the agent under `baseline`.
2. **Extraction.** One extractor call per episode returns at most 3 lessons
   (`trigger`, `lesson`, `scope`).
   - Malformed lessons are rejected: wrong schema or length, or code in the text.
   - Task-specific lessons are flagged and excluded: they name the task's identifiers in code form,
     or copy source lines.
3. **Bank.** Near-duplicates merge their evidence. Each memory gets a content-derived ID and
   provenance:
   - source episodes, split and tasks;
   - extractor configuration;
   - creation time;
   - task and candidate hashes.

   Memories are labelled as *unverified hypotheses*, because public tests are incomplete evidence.
4. **Retrieval.**
   - **Query:** the new task's title and statement.
   - **Score:** `2·|Q∩trigger| + |Q∩scope| + |Q∩lesson|`, after stop-word removal and light suffix
     stripping.
   - **Selection:** keep memories with `score ≥ min_score` (default 3), order by (−score,
     `memory_id`), and add up to 3 whole lessons within a 300-estimated-token budget, shared with
     the static checklist. Estimated tokens are `ceil(chars/3)`. Empty retrieval is allowed.

## Metrics

Each metric is reported per condition, per task and per episode, as raw counts:

- final hidden-test success (**primary: learned_memory vs baseline**, predeclared);
- final success excluding infrastructure-failed episodes;
- public-test success of the submitted candidate, and whether the first test run passed;
- fallback activations (equal to raw revision regressions), and whether the pre-fallback candidate
  would have passed;
- repair activity, model calls, tool calls and parse errors;
- API input/output tokens and latency;
- timeouts and API/infrastructure errors;
- the injected context size, and which memories were retrieved.

The report also separates lesson-relevant from lesson-irrelevant tasks, and memory-construction
cost from evaluation cost.

## Outputs (one study directory, never overwritten)

```
study.json  memory_source/  extraction/  memory_bank.json  [selection/]  frozen_protocol.json
eval-test/{manifest.json, episodes.jsonl, transcripts.jsonl, candidates/<episode>/<snapshot>/...}
report.md
```

`--omit-prompts` skips transcripts. Run directories are git-ignored.

## Limitations

- The benchmark is tiny and hand-written: one or two tasks per principle.
- Keyword retrieval misses relevant lessons and sometimes retrieves irrelevant ones. In the mock
  demo, `test_standings` gets none and `test_config_merge` gets an irrelevant one, and the report
  shows both.
- Mock runs verify orchestration only. Their outcomes are scripted.
- Hosted models are nondeterministic even at temperature 0.
- Docker isolation is for controlled tasks. It is **not** a hardened sandbox for adversarial code,
  and a candidate could forge test output inside its own container. Each container runs with:
  - `--network none`, a non-root user, a read-only root and `cap-drop ALL`;
  - memory, CPU and process limits and a timeout;
  - a fresh read-only mount, with no secrets in its environment.

## History

The earlier v1 experiment (instruction style × retry feedback) and v2 experiment (revision policy ×
repair context) are retired. They remain reproducible from commit `3291f89`; see
[docs/experiment-history.md](docs/experiment-history.md).

## Code map (`codemap/`)

An interactive, zoomable map of this codebase for understanding and navigating it. It is a separate
standard-library package and does not change how the harness runs.

```bash
python -m codemap build harness_mvp tests --open   # static, self-contained .codemap/index.html
python -m codemap serve --open                     # live map at http://127.0.0.1:8765 (+ AI on demand)
python -m codemap explain                          # ask the coding agent (claude -p) for walkthroughs
python -m codemap explain --node harness_mvp.repo_agent.run_episode
```

The paths you pass are remembered in `.codemap/config.json`.

**What you can do on the map**

- **Zoom from architecture to code.** Packages, then modules in dependency layers (callers on the
  left, callees on the right, external libraries on the far right), then function blocks. Keep
  zooming and each block shows its real source with line numbers. Double-click a block to read it.
- **Select to light up relationships.** Callers are blue, callees green, external calls amber.
  Wires leave a block at the exact line that makes the call. Gutter markers show calls, external
  calls, branches, loops, returns, raises and data-transforming assignments. ⌘/Ctrl-click a line
  to jump to the function it calls.
- **Trace execution.** From any function (entry points are listed on the left), step through a
  static walk of what it calls, in source order. A numbered gold trail on the map shows each hop,
  and each step lists how arguments map to parameters.
- **Trace data.** Click a parameter chip to follow that input through assignments and call
  arguments into callees. You see which lines touch it, which branches depend on it, what returns
  it, and which external effects (filesystem, network, subprocess, output…) receive it.
- **AI walkthroughs.** Guided tours that fly the camera step by step, with prose pinned next to
  the code each step describes.
- **Source links.** Every block, list item and walkthrough step links to `file:line` and opens in
  VS Code.

**Verified vs. AI-generated.** Blocks, wires, events and traces come only from parsing the source
with `ast`: nothing is executed and no model is involved. Each call edge records how it was
resolved:

- `resolved`: follows from names and imports.
- `inferred`: the receiver type comes from an annotation or a constructor.
- `dynamic`: the receiver is unknown, so the candidates are matched by method name or by
  registration, such as argparse `set_defaults(func=...)`.

Walkthroughs are written by the coding agent, drawn in purple on a separate layer, and
machine-checked on every build:

- the step's node exists;
- its lines lie inside that node;
- the code at the anchor is unchanged since generation (otherwise it is flagged **stale**);
- there is a static call path from an earlier step (otherwise it is flagged as the AI's claim).

The agent sees the verified graph and source but never changes it. Use `--agent-cmd "<cmd>"` to
use a different agent that reads a prompt on stdin and prints JSON.

**Limits.** Python only. Static analysis cannot know runtime types, so `dynamic` edges are
candidates, not proofs. Execution traces list every branch rather than choosing one. Data tracing
works per statement and over-approximates (a statement that reads a traced value taints everything
it writes). `.codemap/walkthroughs.json` is worth committing; `index.html` and `graph.json` are
regenerated.

