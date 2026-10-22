# Harness MVP: black-box selection of a code-repair agent's harness

A tiny, runnable experiment. A fixed LLM repairs small buggy Python modules under a fixed budget of
**at most two LLM calls per task**. We do not change the model. We search over the *harness*
around it (the instructions and the retry-feedback format) and ask:

> With the same model and at most two calls per task, can automatically selecting the agent's
> instructions and error-feedback format improve held-out repair success or reduce token usage?

"Optimization" here means **automatic configuration selection by random search** and nothing more.
No weights are trained, and no harness code is modified automatically.

> **Small-sample limitation.** There are 5 tasks (3 optimization, 2 held-out). That is enough to
> validate the pipeline, not to establish generalizable performance. The report does not compute
> p-values or claim significance. A null or negative result is valid.

The repository holds two versioned experiments:

- **v1** (`run --experiment v1`, the default): instruction style × retry feedback on the 5 tasks above.
  Its procedure is unchanged so that earlier runs stay reproducible.
- **v2** (`calibrate`, then `run --experiment v2`): revision policy × repair context, on harder tasks.
  See [Experiment v2](#experiment-v2-revision-policy--repair-context) below.

## Requirements

- Python 3.11+ (standard library only, so no `pip install` is needed).
- Docker with a running daemon, for any run that executes model-generated code (live runs).
- An OpenAI-compatible chat-completions endpoint and API key, for live runs only.

## Quick start (offline, free)

```bash
python -m unittest discover -s tests -t .        # orchestration checks
python -m harness_mvp smoke --mock               # task-suite + runner + one synthetic episode
python -m harness_mvp run --mock --output runs/mock
python -m harness_mvp report runs/mock           # regenerate/print report.md
```

Mock mode uses a scripted model, and every result it produces is labeled **SYNTHETIC**. The mock
covers these cases: a failed first attempt followed by a successful repair, a permanently failing
episode, malformed output with no code, a syntax error, an unfenced reply, and a candidate that
passes the public tests but fails the hidden ones.

If Docker is running, mock mode executes candidates in Docker. If it is not, mock mode falls back
to a *trusted local runner*. That runner refuses any source whose SHA-256 is not on an allowlist
of repository-authored code (task files and the fixed mock outputs). Model output never runs on the host.

## Live experiment

```bash
cp .env.example .env            # then fill in HARNESS_MODEL and HARNESS_API_KEY (and base URL if not OpenAI)
python -m harness_mvp build-image                      # once; builds harness-mvp-runner:py311
python -m harness_mvp smoke                            # preflight: credentials + Docker runner checks, NO API calls
python -m harness_mvp run --seed 42 --max-model-calls 34 --output runs/live
python -m harness_mvp report runs/live
```

`run` does the whole experiment in one command. It optimizes, freezes the winner to
`selection.json`, evaluates the held-out tasks and writes `report.md`. It never starts
automatically. Run directories are never overwritten: if `--output` already holds a run, a suffix
(`-2`, `-3`, ...) is added and the new path is printed.

Environment variables (a `.env` in the working directory is loaded; values already in the shell win):

| Variable | Default | Notes |
|---|---|---|
| `HARNESS_BASE_URL` | `https://api.openai.com/v1` | `/chat/completions` is appended |
| `HARNESS_MODEL` | (required) | one fixed model per run |
| `HARNESS_API_KEY` | (required; falls back to `OPENAI_API_KEY`) | never logged or persisted |
| `HARNESS_TEMPERATURE` | `0` | `none` omits the field (some reasoning models reject it) |
| `HARNESS_MAX_TOKENS` | `1024` | conservative output cap |
| `HARNESS_MAX_TOKENS_PARAM` | `max_tokens` | use `max_completion_tokens` for models that require it |
| `HARNESS_REQUEST_TIMEOUT` | `90` | seconds |

For reasoning models, hidden reasoning tokens count against the output cap. If replies come back
truncated (`finish_reason=length`, shown as an unterminated fence or `no_code`), raise
`HARNESS_MAX_TOKENS` and say so when you report results.

## Cost bounds

| Item | Default |
|---|---|
| Optimization episodes | 3 configs × 3 tasks = 9 |
| Held-out episodes | 2 configs × 2 tasks × 2 reps = 8 (4 if the baseline is selected) |
| Max episodes | 17 |
| Max model calls | 34 (2 per episode); global cap `--max-model-calls`, checked **before every request** |
| Automatic API retries | 0 (`--api-retries N` opts in; retries still count against the global cap) |

**Rate limits and transient errors.** Hosted APIs can return HTTP 429 (rate limit or quota) or
503 (overloaded). Two options help:

- `--min-interval S` pauses S seconds after each request finishes before the next one starts. Requests are always sequential: each one waits for the previous to finish.
- `--api-retries N` re-sends a request after a *transient* error (429, 5xx or a network error)
  and nothing else. It waits `--retry-wait` seconds before the first re-send (default 10), doubles
  the wait each time up to 120s, and uses the server's suggested delay if that is longer.

Every re-send counts against `--max-model-calls`. The design already caps successful requests at
34, so extra budget is only spent on re-sends after errors. Deliberate waits are recorded as
`wait_s` and excluded from episode latency.

API or runner failures count as unsolved, so they can decide the ranking. When any optimization
episode ends that way, `selection.json` gets `"reliable": false`. The report then puts a
**SELECTION NOT RELIABLE** warning above the optimization table and a matching warning on the
held-out results.

For example, with a free-tier Gemini key:

```bash
python -m harness_mvp run --seed 42 --min-interval 7 --api-retries 3 --max-model-calls 50 --output runs/live2
```

The token numbers below are a rough upper-bound estimate, not a measurement. Each request carries
about 0.5–2.6k input tokens (a retry includes the earlier reply plus up to 4,000 characters of test
output) and at most `HARNESS_MAX_TOKENS` output tokens. That puts the worst case at roughly 90k
input and 35k output tokens per run. Multiply by your provider's prices.

## Method

**Tasks** (`tasks/manifest.json`, splits frozen in the file). Each task has a contract, `buggy.py`,
`public_tests.py` (shown to the agent), `hidden_tests.py` (used only for scoring; it repeats the
public cases and adds edge cases) and `reference.py` (used only for validation and the mock).

| Task | Split | Bug |
|---|---|---|
| `merge_intervals` | optimization | strict `<` means touching intervals don't merge |
| `stable_dedupe` | optimization | keeps the last element per key instead of the first |
| `top_k_words` | optimization | ties ordered by first appearance, not alphabetically |
| `saturating_add` | test | `INT32_MAX = 2**31` (boundary off-by-one) |
| `chunk_string` | test | range stop drops the final chunk |

**Agent loop** ([harness_mvp/agent.py](harness_mvp/agent.py)). One episode runs like this:

1. The prompt contains the contract, the buggy module and the public tests.
2. The agent extracts the code. The parser tolerates a single fence, several fences (it takes the
   longest Python one), an unterminated fence or a raw module.
3. A syntax check runs on the host with `ast.parse`. This only parses the code and never executes it.
4. The public tests run in Docker.
5. If they fail, the model gets one repair attempt, with feedback in the configured format.
6. The last syntactically valid candidate is scored against the hidden tests in a separate container.

A public pass stops the loop, and a hidden-test failure never triggers another attempt. If an
API request fails, the last valid candidate is kept and the failure is recorded. The categories
`no_code`, `syntax_error`, `public_test_failure`, `hidden_test_failure`, `timeout`, `api_error`,
`runner_error`, `auth_error` and `budget_exhausted` are all recorded explicitly. An auth error,
missing Docker or an exhausted budget stops the run; other failures do not.

**Search space** ([harness_mvp/search.py](harness_mvp/search.py)):

- `instruction_style`: `direct` (concise) or `structured` (privately check the contract, the tests
  and edge cases; return code only, with no written reasoning).
- `retry_feedback`: `full` (public-test stdout and stderr, capped at 4,000 characters) or `compact`
  (the failed test IDs plus at most 5 error lines).

The baseline is `direct+full`. Random search with `--seed` samples 2 distinct alternatives from the
other 3 configurations (`--enumerate` evaluates all 3). Each candidate runs once on each of the 3
optimization tasks.

**Selection** uses optimization episodes only and refuses any other input. Candidates are ranked by:

1. Most tasks solved (hidden tests).
2. Lowest total tokens, but only if usage is known for **every** candidate. Missing usage is never
   treated as zero.
3. `config_id`, ascending.

**Held-out evaluation.** The selection is written to `selection.json` before any held-out
episode runs. The baseline and the selected configuration each run on both test tasks, twice. The
order alternates between (rep, task) pairs to reduce ordering effects. If the baseline wins, its
held-out episodes are reused and the report says so.

## Execution isolation

[harness_mvp/runner.py](harness_mvp/runner.py) runs every test execution with:

```
docker run --rm --network none --user 10001:10001 --read-only
  --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m --memory 256m --memory-swap 256m --cpus 1
  --pids-limit 64 --ulimit nofile=64:64 --cap-drop ALL --security-opt no-new-privileges
  --mount type=bind,source=<dir with candidate.py + ONE test file>,target=/work,readonly
  harness-mvp-runner:py311 timeout -k 1 10 python -B -m unittest -v <public|hidden>_tests
```

- No host environment is forwarded into the container, and the Docker socket is not mounted.
- Public and hidden tests run in separate containers. The hidden test file is not present during
  public runs.
- A run counts as passed only if the exit code is 0 *and* unittest reports `OK` with the expected
  number of tests.
- The base `python` image defines a public `GPG_KEY` variable (the release-signing key ID). It is
  not a secret.

**This is not a hardened sandbox.** It suits controlled educational tasks. It is not built to
contain adversarial programs, and it is not robust against candidates that tamper with the test
process to forge a pass.

## Outputs (per run directory)

- `manifest.json` records the model and endpoint (without credentials), decoding settings, seed,
  candidates, task IDs, split and file hashes, the runner limits, timestamps, the code version
  (git revision, or a hash of the sources) and the status.
- `episodes.jsonl` has one record per episode. Each record holds the task, split, config,
  repetition, source hashes, per-attempt outcomes and requests, token usage (`null` when the API
  didn't report it), model and episode latency, the hidden result and the error category.
- `transcripts.jsonl` holds the prompts and responses for debugging. `--omit-prompts` disables it.
- `selection.json` holds the frozen selection, the full ranking and the rule used.
- `report.md` compares the optimization set and the held-out set separately and labels synthetic
  results.

Fixed seeds make candidate sampling reproducible. They do **not** make hosted-model outputs deterministic.

## Experiment v2: revision policy × repair context

v1 hit a ceiling: every task passed on the first attempt, so retry feedback never activated. v2
uses harder tasks and harness parameters that change what the agent does after attempt 1.

### Configurations

| | `minimal` repair context | `full` repair context |
|---|---|---|
| `on_failure` (revise only if public tests fail) | **baseline** | `on_failure+full` |
| `always_review` (also revise after a public pass) | `always_review+minimal` | `always_review+full` |

Attempt 1 is byte-identical for all four: the system prompt plus contract, buggy module and public
tests. The model, decoding settings, output limit, execution limits and the 2-call cap are the same
for every configuration.

The second request is a **fresh single-turn request**, not a continuation of the conversation. The
only difference between the two contexts is which blocks it contains:

| Block in the second request | minimal | full |
|---|---|---|
| Instruction: *fix* (public tests failed) or *review* (passed, `always_review` only) | ✓ | ✓ |
| Function interface (signature), "keep it unchanged" | ✓ | ✓ |
| Current candidate (the original module if the reply had no code) | ✓ | ✓ |
| Latest public-test result (output capped at 3,000 chars) | ✓ | ✓ |
| Contract statement, original buggy module, public test source | | ✓ |
| Previous attempts (≤ 3 entries × 300 chars: parse status, outcome, failed public tests, hash) | | ✓ |

The exact templates are in [harness_mvp/revision.py](harness_mvp/revision.py) and are copied into
every v2 run manifest (`prompt_templates`).

**Final candidate.** This is the latest syntactically valid candidate. It is scored even when a
review made it worse, and both candidates are saved under `candidates/<episode_id>/`.

**Hidden-test isolation.** Hidden tests run only after the episode's last model call. They score
the attempt-1 candidate (*first-attempt hidden success*) and the final candidate. They never reach
a prompt, the revision decision or the choice of final candidate; tests check all three.

### Step 1: calibration (development-only tasks)

`tasks_calibration/` holds 5 tasks with interacting bugs: a TTL cache, a retry scheduler, a
dependency resolver, event deduplication and duration parsing. The public tests expose one bug;
the hidden tests cover the rest of the spec. These tasks are never used in the final evaluation,
and the benchmark freeze rejects any overlap in task id or family.

```bash
python -m harness_mvp calibrate --mock                       # synthetic, free
python -m harness_mvp calibrate --configs baseline --reps 1  # smallest live pilot: ≤ 10 model calls
python -m harness_mvp calibrate --configs all --reps 1       # repair behavior, 4 configs: ≤ 40 calls
```

The calibration report labels each task's first-attempt hidden success as *too easy*, *in range*
or *too hard*. The rough target is 30–70%, a guide rather than a requirement.

### Step 2: freeze the benchmark

Not done yet: the 12 benchmark tasks (6 optimization + 6 held-out, in `tasks_v2/`) will be written
after the live calibration, so their difficulty can be informed by it. Once they exist, run:

```bash
python -m harness_mvp freeze-benchmark   # validates splits/families, writes tasks_v2/frozen.json
```

The freeze rejects splits other than optimization/test, families that cross splits, and overlap
with calibration tasks. `run --experiment v2` refuses a benchmark that is not frozen, or whose
task files or manifest entries changed after freezing.

### Step 3: main experiment

```bash
python -m harness_mvp run --experiment v2 --preset pilot --min-interval 5 --api-retries 3
python -m harness_mvp run --experiment v2 --preset full  --min-interval 5 --api-retries 3
```

| Preset | Optimization | Held-out | Max episodes | Max model calls (before re-sends) |
|---|---|---|---|---|
| `pilot` | 4 configs × 6 tasks × 1 rep | 2 configs × 6 tasks × 1 rep | 36 | 72 |
| `full` | 4 configs × 6 tasks × 2 reps | 2 configs × 6 tasks × 3 reps | 84 | 168 |

- **Budget.** `--max-model-calls` defaults to the planned maximum and counts every request,
  including API re-sends, so with `--api-retries` you may need to raise it.
- **Selection.** Optimization episodes only, ranked by most final hidden-test successes, then
  fewest total tokens (only if known for all configurations), then `config_id`. The result is
  frozen to `selection.json` before the held-out phase.
- **Held-out phase.** Baseline versus the selected configuration. If the baseline wins, its
  observations are reused.
- **Ordering.** Configurations are rotated per (repetition, task) in the optimization phase and
  alternated in the held-out phase, to reduce ordering effects.

### v2 metrics

Each metric is reported per configuration as k/n. N/A means no episode was eligible. Failed
episodes count in every denominator.

| Metric | Denominator | Numerator |
|---|---|---|
| First-attempt public pass | all episodes | attempt 1 passed public tests |
| First-attempt hidden success | all episodes | attempt-1 candidate passes hidden tests (scored after the episode) |
| Final hidden success | all episodes | final candidate passes hidden tests |
| Revision activation | all episodes | a second attempt was requested |
| Recovery | revised episodes whose first candidate failed hidden tests | final candidate passes |
| Review benefit | revised episodes whose first candidate passed public but failed hidden tests | final candidate passes |
| Regression | revised episodes whose first candidate passed hidden tests | final candidate fails |

The report also lists model calls, input/output/total tokens, model and episode latency, waits,
failed requests and infrastructure errors, separately for each phase. It writes `metrics.json`
next to `report.md`. All v2 conclusions are exploratory.

## Layout

```
harness_mvp/  main.py (CLI + orchestration)  agent.py (v1 loop + shared request/parse helpers)
              revision.py (v2 configs, prompt templates, episode loop)  metrics.py (v2 metrics)
              model.py  runner.py  search.py  report.py  tasks.py (loading + benchmark freeze)
tasks/        v1 benchmark: manifest.json + <task_id>/{buggy,reference,public_tests,hidden_tests}.py
tasks_calibration/  v2 development-only tasks (+ mock_*.py variants used only by the mock model)
docker/       runner.Dockerfile
codemap/      analyze.py (ast graph)  layout.py  walkthroughs.py (AI + checks)  cli.py  viewer.html
tests/        parsing, sampling/selection, hidden-feedback isolation, budget, API-error handling,
              v2 revision behavior, metric denominators, benchmark freeze, end-to-end v2 mock run
```

## Code map (`codemap/`)

An interactive, zoomable map of this codebase for understanding and navigating it. It is a separate
standard-library package and does not change how the harness runs.

```bash
python -m codemap build harness_mvp tests --open   # static, self-contained .codemap/index.html
python -m codemap serve --open                     # live map at http://127.0.0.1:8765 (+ AI on demand)
python -m codemap explain                          # ask the coding agent (claude -p) for walkthroughs
python -m codemap explain --node harness_mvp.agent.run_episode
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

## Possible next steps (not implemented)

20–50 tasks; more repetitions with uncertainty estimates; a second optimizer under the same budget;
a frozen failure-memory ablation built only from optimization tasks; security-oriented repair tasks
in an authorized local benchmark.
