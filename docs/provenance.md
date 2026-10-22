# Provenance (checked against HEAD `2ef97a2`, 2026-10-23)

The git history was rewritten once on 2026-10-23 to remove commit-message trailers. Commit IDs
cited in documents written before that date are **original IDs**; `docs/commit-hash-map.md` maps
them to current IDs. History is not rewritten again. How the work was produced is unchanged by
that rewrite: the code, documents and experiment design in this repository were written with an
AI coding assistant under the author's direction, and the experiments call `gemini-3.5-flash-lite`.

## Harness tags (verified by hashing the tagged files)

| Tag | Commit | harness/instructions.md | harness/memory.md | harness/verification.md |
|---|---|---|---|---|
| `harness-v0` (original) | `fb6a8d6` | `63aae5104e83…` | empty (`e3b0c442…`) | absent (treated as empty) |
| `harness-v1` (accepted iteration 2) | `0bf7279` | `3685c9715d9f…` | empty | `ebc388e2b200…` |

Both match the hashes recorded when the runs were made (docs/adaptation-pilot-plan.md,
docs/iteration2-results.md). The working tree at `2ef97a2` equals `harness-v1`.
