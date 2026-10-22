# Pilot: exploratory transfer (proposed harness)

Model: `gemini-3.5-flash-lite` · temperature 0.0 · reasoning_effort None · episodes 4/4

| Condition | Pair score (95% CI over pairs) | Detection | Twin clean | Precision | Misclass. | No submit | Path cov. | In tok/ep | Out tok/ep | Calls/ep | Latency/ep | Cost € |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| proposed | 0.75 [0.50, 1.00] | 2/2 (100%) | 1/2 (50%) | 2/2 (100%) | 0 | 1 | 1.0 | 10447 | 194 | 5 | 3.92s | 0.0145 |

Repetition agreement: 4/4 (task, variant, condition) groups gave the same outcome in every repetition.

## Per task

Witness kind: `exploit` = harmful effect observed on planted test data; `reachability` = untrusted input shown to reach the dangerous call (weaker evidence). Never merged.

| Task | Class | Witness | Condition | Vulnerable: detected | Twin: clean | Twin alarms | End reasons |
|---|---|---|---|---|---|---|---|
| cal_invoicer | missing authorization | exploit | proposed | 1/1 | 0/1 | 0 | model_call_limit, submitted |
| cal_pricer | code injection | reachability | proposed | 1/1 | 1/1 | 0 | submitted |

Returned model ids: gemini-3.5-flash-lite (requested: gemini-3.5-flash-lite). A returned id is what the API reports; it does not prove the weights are unchanged.
