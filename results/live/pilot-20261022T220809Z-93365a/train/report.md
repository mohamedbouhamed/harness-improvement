# Pilot: training-set check (proposed harness)

Model: `gemini-3.5-flash-lite` · temperature 0.0 · reasoning_effort None · episodes 8/8

| Condition | Pair score (95% CI over pairs) | Detection | Twin clean | Precision | Misclass. | No submit | Path cov. | In tok/ep | Out tok/ep | Calls/ep | Latency/ep | Cost € |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| proposed | 0.50 [0.12, 0.88] | 2/4 (50%) | 2/4 (50%) | 2/4 (50%) | 0 | 0 | 0.8 | 9576 | 313 | 4.75 | 4.03s | 0.0292 |

Repetition agreement: 8/8 (task, variant, condition) groups gave the same outcome in every repetition.

## Per task

Witness kind: `exploit` = harmful effect observed on planted test data; `reachability` = untrusted input shown to reach the dangerous call (weaker evidence). Never merged.

| Task | Class | Witness | Condition | Vulnerable: detected | Twin: clean | Twin alarms | End reasons |
|---|---|---|---|---|---|---|---|
| cal_docshare | path traversal | exploit | proposed | 0/1 | 1/1 | 0 | submitted |
| cal_mediakit | OS command injection | reachability | proposed | 1/1 | 0/1 | 1 | submitted |
| cal_miniwiki | cross-site scripting | exploit | proposed | 0/1 | 0/1 | 1 | submitted |
| cal_teamnotes | SQL injection | reachability | proposed | 1/1 | 1/1 | 0 | submitted |

Returned model ids: gemini-3.5-flash-lite (requested: gemini-3.5-flash-lite). A returned id is what the API reports; it does not prove the weights are unchanged.
