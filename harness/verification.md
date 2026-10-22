Your findings are not final yet. Verification step:
Review your draft findings against the repository code. For each reported finding, explicitly re-verify:
1. Is the data source definitely untrusted according to the README threat model?
2. Is there a complete, unconstrained path from input to sink without intermediate sanitization or validation that blocks the attack?
3. On fixed or patched versions, double-check whether the code already correctly neutralizes the input.

If any finding relies on assumptions that do not hold, remove it. If the findings are robust, confirm them.
