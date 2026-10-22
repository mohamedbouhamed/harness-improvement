# Experiment history

Earlier experiments are retired from the active codebase but stay reproducible through git.
Results below are sanitized summaries of runs saved under `runs/`, which is git-ignored. They are
small exploratory pilots, not evidence of generalizable effects.

## Checkpoint

| Commit | Contents |
|---|---|
| `3291f89` | Harness with experiments v1 and v2, as found when version control started. No earlier git history existed, so v1 and v2 could not be split into separate commits honestly. |
| `7d55017` | Unrelated `codemap/` tool and `docs/codebase-workflow.md` (a walkthrough of the code at `3291f89`). |

To reproduce v1/v2, check out the checkpoint in a separate worktree:

```bash
git worktree add ../harness-v1v2 3291f89
cd ../harness-v1v2
python -m unittest discover -s tests -t .
python -m harness_mvp smoke --mock
python -m harness_mvp run --experiment v1 --mock                 # v1, synthetic
python -m harness_mvp calibrate --mock                           # v2 calibration, synthetic
python -m harness_mvp run --experiment v1 --seed 42 --min-interval 7 --api-retries 3 --max-model-calls 50
python -m harness_mvp calibrate --configs all --reps 1 --min-interval 5 --api-retries 3 --max-model-calls 60
```

## v1: instruction style × retry feedback (5 single-function tasks)

**Question.** With at most two calls per task, does selecting instructions (`direct` or
`structured`) and retry feedback (`full` or `compact`) improve held-out repair or reduce tokens?

**Live result** (`gemini-3.5-flash-lite`, 2026-10-22, 13 calls, 0 API errors):

- All 13 episodes passed on the first attempt, so retry feedback never activated.
- The baseline `direct+full` was selected by the token tie-breaker, by an 18-token margin. That
  margin came from output nondeterminism: the prompts were identical.
- `structured` cost about 11% more input tokens at equal success.
- Held-out: 4/4 for the baseline. **Ceiling effect; null result.**

Earlier live attempts with `gemini-3.8-flash` and `gemini-3.5-flash` were dominated by HTTP 429
(free-tier daily quota) and 503 errors. Their run directories were overwritten before the
never-overwrite rule existed, except one interrupted `gemini-3.5-flash` run.

## v2: revision policy × repair context (5 calibration tasks)

**Question.** Does asking for a second attempt after a public pass (`always_review`), or giving the
revision request full context, change repair outcomes?

**Live calibration** (`gemini-3.5-flash-lite`, 2026-10-22):

- **Baseline only** (5 episodes, 6 calls): 4/5 passed public and hidden tests on the first
  attempt. `dedupe_events` was repaired after one revision, giving 5/5 final.
- **All four configurations** (20 episodes, 30 calls, 0 API errors):
  - 18/20 first candidates already passed the hidden tests; final success was 17/20.
  - Four of the five tasks were solved on the first attempt every time, so they are too easy.
  - Only `dedupe_events` (tie-handling rules) fell in the 30–70% target band (2/4 first-attempt
    hidden success).
  - `always_review` never repaired a candidate. It once turned a hidden-correct candidate into
    one that failed a *public* test, and it cost about 1.7–2.5× the tokens.

**Lessons carried forward:**

1. Tasks need cross-function interactions to avoid a ceiling.
2. Revising without a public-test check can regress, which motivates public-test fallback.
3. A one-episode difference between configurations with identical first prompts is noise.
