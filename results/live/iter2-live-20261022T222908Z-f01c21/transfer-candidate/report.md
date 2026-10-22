# Iteration 2: transfer-candidate

Model: `gemini-3.5-flash-lite` · temperature 0.0 · reasoning_effort None · episodes 6/6

| Condition | Pair score (95% CI over pairs) | Detection | Twin clean | Precision | Misclass. | No submit | Path cov. | In tok/ep | Out tok/ep | Calls/ep | Latency/ep | Cost € |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| candidate | 0.67 [0.50, 1.00] | 1/3 (33%) | 3/3 (100%) | 1/1 (100%) | 0 | 0 | 1.0 | 9399 | 176 | 4.5 | 3.24s | 0.0195 |

Repetition agreement: 6/6 (task, variant, condition) groups gave the same outcome in every repetition.

## Per task

Witness kind: `exploit` = harmful effect observed on planted test data; `reachability` = untrusted input shown to reach the dangerous call (weaker evidence). Never merged.

| Task | Class | Witness | Condition | Vulnerable: detected | Twin: clean | Twin alarms | End reasons |
|---|---|---|---|---|---|---|---|
| cal_invoicer | missing authorization | exploit | candidate | 0/1 | 1/1 | 0 | submitted |
| cal_pricer | code injection | reachability | candidate | 1/1 | 1/1 | 0 | submitted |
| cal_ssohub | open redirect | exploit | candidate | 0/1 | 1/1 | 0 | submitted |

Returned model ids: gemini-3.5-flash-lite (requested: gemini-3.5-flash-lite). A returned id is what the API reports; it does not prove the weights are unchanged.
