# calibration run (calibration-live-20261022T204455Z-898a0a)

Model: `gemini-3.5-flash-lite` · temperature 0.0 · reasoning_effort None · episodes 32/32

| Condition | Pair score (95% CI over pairs) | Detection | Twin clean | Precision | Misclass. | No submit | Path cov. | In tok/ep | Out tok/ep | Calls/ep | Latency/ep | Cost € |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| none | 0.69 [0.56, 0.88] | 8/16 (50%) | 14/16 (88%) | 8/12 (67%) | 0 | 1 | 0.9 | 9460 | 179 | 4.69 | 17.97s | 0.1052 |

Repetition agreement: 16/16 (task, variant, condition) groups gave the same outcome in every repetition.

## Per task

Witness kind: `exploit` = harmful effect observed on planted test data; `reachability` = untrusted input shown to reach the dangerous call (weaker evidence). Never merged.

| Task | Class | Witness | Condition | Vulnerable: detected | Twin: clean | Twin alarms | End reasons |
|---|---|---|---|---|---|---|---|
| cal_docshare | path traversal | exploit | none | 0/2 | 2/2 | 0 | submitted |
| cal_invoicer | missing authorization | exploit | none | 0/2 | 2/2 | 0 | model_call_limit, submitted |
| cal_mediakit | OS command injection | reachability | none | 2/2 | 2/2 | 0 | submitted |
| cal_miniwiki | cross-site scripting | exploit | none | 0/2 | 2/2 | 0 | submitted |
| cal_prefsync | unsafe deserialization | reachability | none | 2/2 | 0/2 | 3 | submitted |
| cal_pricer | code injection | reachability | none | 2/2 | 2/2 | 0 | submitted |
| cal_ssohub | open redirect | exploit | none | 0/2 | 2/2 | 0 | submitted |
| cal_teamnotes | SQL injection | reachability | none | 2/2 | 2/2 | 0 | submitted |

Returned model ids: gemini-3.5-flash-lite (requested: gemini-3.5-flash-lite). A returned id is what the API reports; it does not prove the weights are unchanged.
