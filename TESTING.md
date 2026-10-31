# Testing policy

Tests protect contracts, not implementations. A test may pin "this input gives
this output and these side effects". It must not pin private helpers, call
order, source text, bytecode, or documentation wording.

## Principles

1. Pin behaviour, not implementation shape. Call the public interface and
   check its result or externally visible side effects.
2. Keep one source of truth. Derive expected values from source or config;
   do not copy them into tests.
3. Name tests after behaviour. Put issue and acceptance-criterion references
   in docstrings, not file or function names.
4. Fake only process boundaries: subprocesses, HTTP, clocks, and filesystems.
   Do not patch a private helper and assert its call arguments.
5. Put each repository-wide invariant in one convention test under
   `tests/conventions/`.
6. Remove a test in the same PR as the behaviour it protected. Verify a
   removal once in review instead of keeping a permanent absence test.
7. Keep test volume in check: at most one test per contract, new files under
   800 lines, and test lines no greater than source lines.

## Rules

| ID | Rule |
|----|------|
| T1 | Do not inspect source text or bytecode outside a justified convention test. |
| T2 | Do not assert documentation wording; checking forbidden-pattern absence is allowed in a convention test. |
| T3 | Do not keep permanent removal or absence checks. |
| T4 | Do not name test files or functions after issue numbers, acceptance criteria, or phases. |
| T5 | Do not assert private-helper calls; verify boundaries and results. |
| T6 | Do not snapshot structural counts or directory enumerations; public `__all__` is a contract and may be checked. |
| T7 | Do not import host-only vocabulary such as nexus labels, templates, Slack, or `jobs/` paths into this repository's tests. |
| T8 | Keep test files under 1,000 lines and split them by target module. |
| T9 | Do not add characterization tests that freeze current behaviour before a change; write one test per acceptance criterion. |
| T10 | Do not copy complete string constants from external packages; import a public constant or check only a stable distinguishing fragment. |
| T11 | When removing behaviour, list the tests that pin it and remove or rewrite them in the same PR. |
| T12 | Keep `--cov-fail-under=72` unchanged and do not add tests only to satisfy coverage. |

## Removing tests

For each candidate, ask in order: what contract does it protect; does another
behaviour test already protect it; is the contract worth keeping. Delete when
in doubt because history remains in git. Rewrite only when the contract matters,
no behaviour test exists, and the rewrite is no longer than the original.

From a `sumipan/nexus` checkout, inventory candidates with:
`python3 scripts/test-policy-audit.py --repo <path-to-this-repo>`.
