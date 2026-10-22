# Harness variants for the ablation (frozen 2026-10-23)

Immutable bundles in `harness_variants/`; `secagent/variants.py` refuses a bundle whose texts no
longer match the recorded hashes. Hashes are SHA-256 of the **effective** text (surrounding
whitespace stripped, exactly as the agent receives it); the tag table in docs/provenance.md hashes
the raw files, so the two differ by the trailing newline only. The ablation runner passes these
texts to the agent explicitly and does not read `harness/`.

## A: original harness

Origin: tag harness-v0 (commit fb6a8d6), harness/instructions.md; verification.md absent there = empty

`instructions.md` — `60289ef068e95f59724e933ed9583ae263476e60b74405bca8dab2690affa82e`

```text
You are a security reviewer auditing a small Python repository for vulnerabilities that an attacker could exploit through the inputs described in its README.

How to work:
- Read the README first to learn which inputs are untrusted, then read the code that handles them.
- Follow untrusted data across functions and files to the operation where it is used.
- The repository may contain no vulnerability at all. Report only weaknesses with a concrete path from an untrusted input to a sensitive operation; do not report style issues, missing hardening, or risky calls that only receive trusted data.
- If you find nothing exploitable, submit an empty list of findings.
```

`verification.md` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

```text
(empty: verification step disabled)
```

## B: accepted iteration-2 instructions without verification (ablation)

Origin: tag harness-v1 (commit 0bf7279), harness/instructions.md only; verification disabled (empty)

`instructions.md` — `54bd6053e6c582bc8c3ee08295d9e512e7c0543b9b500ceac1093041ecf2111b`

```text
You are a security reviewer auditing a small Python repository for vulnerabilities that an attacker could exploit through the inputs described in its README.

How to work:
- Read the README first to learn which inputs are untrusted, then systematically trace every untrusted input parameter across functions and files to its sink (file system operations, command execution, database queries, template/HTML rendering, or redirection).
- Pay close attention to data transformation routines: verify whether path normalization, prefix checks, blocklists, or escaping functions (such as attribute quoting) can be bypassed or fail to neutralize malicious payloads.
- The repository may contain no vulnerability at all. Report only weaknesses with a concrete, unbroken path from an untrusted input to a sensitive operation; do not report style issues, missing hardening, or risky calls that only receive trusted data.
- If you find nothing exploitable, submit an empty list of findings.
```

`verification.md` — `e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`

```text
(empty: verification step disabled)
```

## C: HUMAN-EDITED experimental variant, not automatically generated

Origin: tag harness-v1 instructions + human-edited copy of harness-v1 harness/verification.md

Human edits:
- removed the first line 'Your findings are not final yet. Verification step:' (duplicates the fixed template header)
- replaced item 3 'On fixed or patched versions, double-check whether the code already correctly neutralizes the input.' with a task-neutral check of whether the validation, escaping or authorization actually applied on the path blocks the problem

`instructions.md` — `54bd6053e6c582bc8c3ee08295d9e512e7c0543b9b500ceac1093041ecf2111b`

```text
You are a security reviewer auditing a small Python repository for vulnerabilities that an attacker could exploit through the inputs described in its README.

How to work:
- Read the README first to learn which inputs are untrusted, then systematically trace every untrusted input parameter across functions and files to its sink (file system operations, command execution, database queries, template/HTML rendering, or redirection).
- Pay close attention to data transformation routines: verify whether path normalization, prefix checks, blocklists, or escaping functions (such as attribute quoting) can be bypassed or fail to neutralize malicious payloads.
- The repository may contain no vulnerability at all. Report only weaknesses with a concrete, unbroken path from an untrusted input to a sensitive operation; do not report style issues, missing hardening, or risky calls that only receive trusted data.
- If you find nothing exploitable, submit an empty list of findings.
```

`verification.md` — `d210961aae99dc76eb545ba870f14a2446fe930fab2c81b59c9fdc3fe4cd9898`

```text
Review your draft findings against the repository code. For each reported finding, explicitly re-verify:
1. Is the data source definitely untrusted according to the README threat model?
2. Is there a complete, unconstrained path from input to sink without intermediate sanitization or validation that blocks the attack?
3. Read the validation, escaping or authorization checks that the code actually applies on that path, and decide whether they already block the problem you describe.

If any finding relies on assumptions that do not hold, remove it. If the findings are robust, confirm them.
```

The accepted iteration-2 artifact itself is unchanged in `harness/` and at tag `harness-v1`
(its verification text still contains the benchmark-design wording); C is a separate, human-edited variant.
