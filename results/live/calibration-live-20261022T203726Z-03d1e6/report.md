# calibration run (calibration-live-20261022T203726Z-03d1e6)

Model: `gemini-3.5-flash-lite` · temperature 0.0 · reasoning_effort None · episodes 2/2

| Condition | Pair score (95% CI over pairs) | Detection | Twin clean | Precision | Misclass. | No submit | Path cov. | In tok/ep | Out tok/ep | Calls/ep | Latency/ep | Cost € |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| none | 0.50 [0.50, 0.50] | 0/1 (0%) | 1/1 (100%) | – | 0 | 0 | None | 9478 | 162 | 3.5 | 21.67s | 0.0065 |

Repetition agreement: 2/2 (task, variant, condition) groups gave the same outcome in every repetition.

## Per task

Witness kind: `exploit` = harmful effect observed on planted test data; `reachability` = untrusted input shown to reach the dangerous call (weaker evidence). Never merged.

| Task | Class | Witness | Condition | Vulnerable: detected | Twin: clean | Twin alarms | End reasons |
|---|---|---|---|---|---|---|---|
| cal_docshare | path traversal | exploit | none | 0/1 | 1/1 | 0 | submitted |

Returned model ids: gemini-3.5-flash-lite (requested: gemini-3.5-flash-lite). A returned id is what the API reports; it does not prove the weights are unchanged.
