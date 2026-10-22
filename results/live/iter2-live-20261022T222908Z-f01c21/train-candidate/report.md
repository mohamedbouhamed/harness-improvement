# Iteration 2: train-candidate

Model: `gemini-3.5-flash-lite` · temperature 0.0 · reasoning_effort None · episodes 8/8

| Condition | Pair score (95% CI over pairs) | Detection | Twin clean | Precision | Misclass. | No submit | Path cov. | In tok/ep | Out tok/ep | Calls/ep | Latency/ep | Cost € |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| candidate | 0.88 [0.62, 1.00] | 3/4 (75%) | 4/4 (100%) | 3/3 (100%) | 0 | 0 | 0.783 | 12693 | 261 | 5.38 | 3.84s | 0.0357 |

Repetition agreement: 8/8 (task, variant, condition) groups gave the same outcome in every repetition.

## Per task

Witness kind: `exploit` = harmful effect observed on planted test data; `reachability` = untrusted input shown to reach the dangerous call (weaker evidence). Never merged.

| Task | Class | Witness | Condition | Vulnerable: detected | Twin: clean | Twin alarms | End reasons |
|---|---|---|---|---|---|---|---|
| cal_docshare | path traversal | exploit | candidate | 0/1 | 1/1 | 0 | submitted |
| cal_mediakit | OS command injection | reachability | candidate | 1/1 | 1/1 | 0 | submitted |
| cal_miniwiki | cross-site scripting | exploit | candidate | 1/1 | 1/1 | 0 | submitted |
| cal_teamnotes | SQL injection | reachability | candidate | 1/1 | 1/1 | 0 | submitted |

Returned model ids: gemini-3.5-flash-lite (requested: gemini-3.5-flash-lite). A returned id is what the API reports; it does not prove the weights are unchanged.
