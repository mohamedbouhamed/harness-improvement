# Iteration 2: train-baseline

Model: `gemini-3.5-flash-lite` · temperature 0.0 · reasoning_effort None · episodes 8/8

| Condition | Pair score (95% CI over pairs) | Detection | Twin clean | Precision | Misclass. | No submit | Path cov. | In tok/ep | Out tok/ep | Calls/ep | Latency/ep | Cost € |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| none | 0.75 [0.50, 1.00] | 2/4 (50%) | 4/4 (100%) | 2/2 (100%) | 0 | 0 | 0.633 | 7938 | 176 | 3.75 | 2.5s | 0.0226 |

Repetition agreement: 8/8 (task, variant, condition) groups gave the same outcome in every repetition.

## Per task

Witness kind: `exploit` = harmful effect observed on planted test data; `reachability` = untrusted input shown to reach the dangerous call (weaker evidence). Never merged.

| Task | Class | Witness | Condition | Vulnerable: detected | Twin: clean | Twin alarms | End reasons |
|---|---|---|---|---|---|---|---|
| cal_docshare | path traversal | exploit | none | 0/1 | 1/1 | 0 | submitted |
| cal_mediakit | OS command injection | reachability | none | 1/1 | 1/1 | 0 | submitted |
| cal_miniwiki | cross-site scripting | exploit | none | 0/1 | 1/1 | 0 | submitted |
| cal_teamnotes | SQL injection | reachability | none | 1/1 | 1/1 | 0 | submitted |

Returned model ids: gemini-3.5-flash-lite (requested: gemini-3.5-flash-lite). A returned id is what the API reports; it does not prove the weights are unchanged.
