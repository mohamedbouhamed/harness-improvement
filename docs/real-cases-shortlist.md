# Real-case shortlist (investigation only, nothing vendored)

Status: **candidates for user selection.** No code from these projects is in this repository.
Retrieved 2026-10-22 from the OSV PyPI dump (`osv-vulnerabilities.storage.googleapis.com/PyPI/all.zip`)
and the GitHub API. These cases are reserved for final evaluation: their advisories and fixes are
never used to write lessons, and the agent never receives them.

## Model dates (the cut-off)

| Fact | Value | Source |
|---|---|---|
| Model code | `gemini-3.5-flash-lite` (the only code; no dated variant exists) | Gemini API models page; `GET /models` |
| API `version` field | `3.5-flash-lite-07-2026` | `GET /v1beta/models/gemini-3.5-flash-lite` |
| Release | 2026-07-21 (GA) | DeepMind model card, Cloud docs |
| Knowledge cutoff | March 2026 | DeepMind model card |
| Moving alias to avoid | `gemini-flash-lite-latest` | `GET /models` |

Google says stable models "usually don't change". That is not a guarantee of fixed weights; every
request records the requested and returned model id, and the README must say this.

## Selection funnel

1. 1,957 PyPI advisories published on/after 2026-07-21 (withdrawn excluded).
2. 252 GHSA advisories in our 8 weakness classes with a GitHub fix reference; 245 with a commit link.
3. 83 in Python repositories under 30 MB with dated fix commits.
4. **29 where every referenced fix commit (author and committer date) is on/after 2026-07-21** (strict rule).
   20 of the 29 are GitPython, mostly variants of one pattern (unsafe git option forwarding).

Most of the remaining 54 fail the strict rule because the fix was committed before the release
even though the advisory was published after it (e.g. webob 2026-06-09, oauthlib 2026-05-22,
wsgidav 2026-06-27, banks GHSA-x8wg 2026-07-07). They are after the knowledge cutoff (March 2026),
so a looser "after cutoff" rule would admit them; that weaker rule is not used unless you decide so.

**Date caveat:** commit dates are metadata set by the author and do not prove when the commit
became public. Pull-request creation dates are better evidence and are listed where they exist.
Fixes merged from a private security fork (no PR) were most likely public only at release or
advisory time.

## Shortlist (strict rule satisfied)

| # | Advisory | Project (licence) | Class | Published | Fix commit / PR | Fix size | Witness feasibility | Notes |
|---|---|---|---|---|---|---|---|---|
| 1 | GHSA-556j-vv39-8rqv | masci/banks (MIT) | path traversal (symlink) | 2026-10-08 | `23ed13e` 2026-09-10, PR #79 opened 2026-09-09 | ~85 lines in `registries/directory.py` (+12 in `__init__`) | good: symlink in registry dir, read planted marker | deps: jinja2, pydantic, griffe (need a deps image) |
| 2 | GHSA-pjvx-prjx-h659 | jupyter/nbconvert (BSD-3) | path traversal (file write) | 2026-10-22 | `ce4bd99` 2026-10-08, no PR | 17 lines, 1 file | good: preprocessor output filename check, no write needed | heavy but pure-Python deps |
| 3 | GHSA-6p8h-3wgx-97gf | GitPython (BSD-3) | OS command / argument injection | 2026-07-24 | `ffcb535` 2026-07-22, PR #2180 opened 2026-07-22 | 5 lines | good: unsafe-option check must raise before `Git.execute` (instrumented) | **borderline**: 1 day after release; earlier GitPython advisories of the same pattern exist (pre-cutoff), so the pattern is not new |
| 4 | GHSA-pfvf-fwfp-25mp | strawberry-graphql (MIT) | missing authorization | 2026-10-09 | `2ebb797` 2026-08-31, PR #4605 same day | 79 lines incl. RELEASE.md, 3 files | good: permission returning an awaitable must deny | big package: needs narrowing to the permission module (a hint; must be documented) |
| 5 | GHSA-phv8-wgjp-g4p9 | 9001/copyparty (MIT) | path traversal (FTP upload) | 2026-10-15 | `b331bb1` 2026-07-27, no PR | 7 lines, 1 file | medium: instrument the FTP path mapping; needs pyftpdlib | very large single project |
| 6 | GHSA-5639-2j2p-m4mx | sqlalchemy/mako (MIT) | path traversal (Windows drive URI) | 2026-10-05 | `000ed85` 2026-09-22, no PR | ~8 lines | weak: Windows-only behaviour, not reproducible in a Linux container | detection-only at best |
| 7 | GHSA-2658-85cv-9wwq | mljar/mercury (Apache-2.0) | XSS | 2026-10-19 | `c798a85` 2026-09-10, no PR | 141 lines incl. docs, 5 files | medium: render markdown, parse HTML; heavy Jupyter deps | |
| 8 | GHSA-2vh9-42vm-xmv2 | InternLM/lmdeploy (Apache-2.0) | unsafe deserialization | 2026-09-18 | `f05b4ad` 2026-08-12, PR #4812 opened 2026-08-02 | 31 lines, 1 file | poor: torch/zmq stack; witness only via stubs | |

Not shortlisted: openapi-python-client (552-line fix across 23 files, no clear single location),
Chainlit (2,642 lines), mercury GHSA-rv4m (936 lines), pacioli (700 lines, 5 stars).

## Recommendation

Pick **#1 banks, #2 nbconvert, #4 strawberry**, plus **#3 GitPython** labelled "borderline/pattern
known". This covers path traversal (seen class) and missing authorization (unseen class), plus
one command/argument-injection case. Each needs:

- a fetch script pinned to the parent of the fix commit (vulnerable) and the fix commit (patched), no code vendored;
- a dependency image built with network, run with `--network none`;
- a written scope (which package/subdirectory the agent gets) and the answer key from manual
  reading of the fix, not just the diff location;
- witnesses in the same style as the synthetic tasks (instrumented, no attack payloads);
- findings on the patched version reported as **unverified alarms**, results reported per case.

Claim to make: this **reduces exact-fix contamination risk**; it does not show that the model has
never seen these vulnerabilities or their patterns.
