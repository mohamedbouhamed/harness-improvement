# calibration run (calibration-live-20261022T205712Z-0943c9)

Model: `gemini-3.5-flash-lite` · temperature 0.7 · reasoning_effort None · episodes 16/16

| Condition | Pair score (95% CI over pairs) | Detection | Twin clean | Precision | Misclass. | No submit | Path cov. | In tok/ep | Out tok/ep | Calls/ep | Latency/ep | Cost € |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| none | 0.69 [0.56, 0.88] | 4/8 (50%) | 7/8 (88%) | 4/6 (67%) | 0 | 1 | 0.9 | 9353 | 177 | 4.38 | 13.98s | 0.052 |

Repetition agreement: 16/16 (task, variant, condition) groups gave the same outcome in every repetition.

## Per task

Witness kind: `exploit` = harmful effect observed on planted test data; `reachability` = untrusted input shown to reach the dangerous call (weaker evidence). Never merged.

| Task | Class | Witness | Condition | Vulnerable: detected | Twin: clean | Twin alarms | End reasons |
|---|---|---|---|---|---|---|---|
| cal_docshare | path traversal | exploit | none | 0/1 | 1/1 | 0 | model_call_limit, submitted |
| cal_invoicer | missing authorization | exploit | none | 0/1 | 1/1 | 0 | submitted |
| cal_mediakit | OS command injection | reachability | none | 1/1 | 1/1 | 0 | submitted |
| cal_miniwiki | cross-site scripting | exploit | none | 0/1 | 1/1 | 0 | submitted |
| cal_prefsync | unsafe deserialization | reachability | none | 1/1 | 0/1 | 1 | submitted |
| cal_pricer | code injection | reachability | none | 1/1 | 1/1 | 0 | submitted |
| cal_ssohub | open redirect | exploit | none | 0/1 | 1/1 | 0 | submitted |
| cal_teamnotes | SQL injection | reachability | none | 1/1 | 1/1 | 0 | submitted |

Returned model ids: gemini-3.5-flash-lite (requested: gemini-3.5-flash-lite). A returned id is what the API reports; it does not prove the weights are unchanged.
