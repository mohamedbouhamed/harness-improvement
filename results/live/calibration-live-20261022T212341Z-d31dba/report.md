# calibration run (calibration-live-20261022T212341Z-d31dba)

Model: `gemini-3.5-flash-lite` · temperature 0.0 · reasoning_effort None · episodes 16/16

| Condition | Pair score (95% CI over pairs) | Detection | Twin clean | Precision | Misclass. | No submit | Path cov. | In tok/ep | Out tok/ep | Calls/ep | Latency/ep | Cost € |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| checklist | 0.75 [0.50, 1.00] | 2/4 (50%) | 4/4 (100%) | 2/2 (100%) | 0 | 0 | 0.8 | 7666 | 158 | 3.75 | 10.26s | 0.0216 |
| lessons | 0.75 [0.50, 1.00] | 2/4 (50%) | 4/4 (100%) | 2/2 (100%) | 0 | 0 | 0.8 | 9155 | 165 | 3.88 | 8.61s | 0.0253 |

Paired differences vs `none` (mean over pairs, 95% bootstrap CI):


Repetition agreement: 16/16 (task, variant, condition) groups gave the same outcome in every repetition.

## Per task

Witness kind: `exploit` = harmful effect observed on planted test data; `reachability` = untrusted input shown to reach the dangerous call (weaker evidence). Never merged.

| Task | Class | Witness | Condition | Vulnerable: detected | Twin: clean | Twin alarms | End reasons |
|---|---|---|---|---|---|---|---|
| cal_docshare | path traversal | exploit | checklist | 0/1 | 1/1 | 0 | submitted |
| cal_docshare | path traversal | exploit | lessons | 0/1 | 1/1 | 0 | submitted |
| cal_mediakit | OS command injection | reachability | checklist | 1/1 | 1/1 | 0 | submitted |
| cal_mediakit | OS command injection | reachability | lessons | 1/1 | 1/1 | 0 | submitted |
| cal_miniwiki | cross-site scripting | exploit | checklist | 0/1 | 1/1 | 0 | submitted |
| cal_miniwiki | cross-site scripting | exploit | lessons | 0/1 | 1/1 | 0 | submitted |
| cal_teamnotes | SQL injection | reachability | checklist | 1/1 | 1/1 | 0 | submitted |
| cal_teamnotes | SQL injection | reachability | lessons | 1/1 | 1/1 | 0 | submitted |

Returned model ids: gemini-3.5-flash-lite (requested: gemini-3.5-flash-lite). A returned id is what the API reports; it does not prove the weights are unchanged.
