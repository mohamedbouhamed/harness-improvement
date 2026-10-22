# Live results (committed artifacts)

Copies of every live run folder from `runs/sec/` (which is git-ignored), so the history can be
checked from a fresh clone. Mock runs are excluded. Transcripts are gzipped (`transcripts.jsonl.gz`).

Sanitisation: the folders were checked for credentials and local filesystem paths before
copying; none were present (the harness never logs API keys). Content is otherwise unmodified:
episode records (findings, scores under scorer v1, actions, token usage, returned model id),
full transcripts (prompts and replies), run manifests and reports, the pilot / iteration-2 logs
(proposer prompts, raw replies, patches, decisions), and the request-level cost ledger.

Verify without model calls: `python -m secagent.reproduce`.
