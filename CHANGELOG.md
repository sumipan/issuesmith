# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).


## Unreleased

### Fixed

- B1 Verify now runs `b1_milestone_subdesign` (nexus #4002). `b1_verify._GATES` ends with `b1_milestone_subdesign`, so an Issue that the verify→recover loop promoted to `scope:milestone` (split plan added, label added) but that still has no `#### サブN` sub design blocks fails with `b1_milestone_subdesign.sub_count_mismatch` instead of reaching `draft-done` and stopping SUB1's `_parent_design_gate`. Non-milestone Issues are unaffected (the gate returns `[]` without `scope:milestone`). The fix_hints that drive recovery now ask for the sub design blocks too: `scope_size` (design section and `sub_design_subsections` from config, mentions `b1_milestone_subdesign`), `milestone_consistency.label_missing` (label alone is not enough) and `b1_milestone_subdesign.sub_count_mismatch` (was `None`; lists the required subsections). `cp1.py` is unchanged.

- allow_paths conflicts are phase-aware (nexus, 2026-09-26). `draft` (B1) and `sub` (SUB1) only edit Issue bodies / create child Issues, so a draft / sub candidate no longer waits on overlapping allow_paths, and draft / sub runs in flight no longer block a `develop` candidate. In-flight entries now record `phase` (`QueueStore.add_in_flight(phase=)`); legacy entries with `role: design` and no phase are treated as draft. Before, two brushups touching the same doc waited on each other for nothing.

### Added

- `scope_coupling.data_file_tests` (nexus #3949, default `true`). When a change-table row modifies a data / config file (`.yml` / `.yaml` / `.json` / `.toml` / `.txt`, change type without a delete / move / rename keyword), `_extract_search_keys` adds its file name with extension (e.g. `vcs.yml`) to the required keys, so tests under `search_dirs` that reference it (for example a test comparing every row of `configs/vcs.yml` with an `EXPECTED` dict) fail `scope_coupling.tests_outside_allow_paths` and are added to `allow_paths` by the CP1 autofix. Previously only the stem was an optional (reference-only) key, so B1 could leave such a test out of `allow_paths` and P1 stopped with `IMPL_FAILED`. Set `scope_coupling: {data_file_tests: false}` to turn off only this check; with `enabled: false` it is not evaluated. New field `ScopeCouplingConfig.data_file_tests`.

- `scope_coupling.deletion_reference_uncovered` (nexus #3953). For each change-table row whose change type matches `scope_size.delete_words`, `scope_coupling` runs `git grep` for the file name, stem and module name (`scripts/git-sync.py` -> `git-sync.py` / `git-sync` / `git_sync`) under `tests/` `scripts/` `tools/` of the base checkout and fails (not auto-fixable, one violation per deleted path) when a referrer matches neither `allow_paths` nor `paths_must_not_exist`. B1 Verify, CP1 and P0 requires run it through `ScopeCouplingRules`, also for `scope_mode: internal` and with `scope_coupling.enabled: false` (the flag now turns off only the caller/test coupling check); referrers already added by the coupling autofix are not reported twice. The queue re-runs it before dispatching a `develop` request (comment on the Issue, request stays queued), and when an in-flight Issue is released with `merge-done` it re-checks the open Issues that list it as a dependency and comments on those with uncovered referrers (`gates.dep.dependents_of` / `on_dep_merge_done`, once per dependency). New helpers `deletion_search_keys`, `uncovered_deletion_references`, `check_deletion_references`, `deletion_references_for_body`, `format_deletion_references`. Previously a dependency that merged after the Issue was written could add a test importing the deleted file, and P1's repair stopped on it because the test was outside `allow_paths`.

- `post_merge` kind `manual_check` (`description: str`, required and non-empty) for human verification steps after merge (nexus #3945). `ac_contract.run_checks` no longer fails such an item: it emits a `post_merge_manual_check` record with result `MANUAL` and the description as `detail`, and the new `ac_contract.pending_manual_checks(records)` returns those descriptions so the M2 close comment can list them as pending manual checks. A `manual_check` without a non-empty `description` fails `post_merge_schema`, and `run_checks` now also fails `post_merge_schema` for a `kind` outside `KNOWN_POST_MERGE_KINDS` (`unknown kind: <kind>; allowed: ...`). `b1_migration.post_merge_schema` accepts `manual_check` (missing `description` → `missing required field: description`; empty → `description must be a non-empty string`), and the `post_merge` fix_hint shows a `manual_check` example. A `scope:migration` Issue that needs no install, tag or restart can now state its manual verification in the contract instead of free text (nexus #2972).

- `b1_migration` now validates the contents of `post_merge` / `removed_trees`, not only their presence (nexus #3955). `b1_migration.post_merge_schema` (fail, not auto-fixable) reports items that are not dicts, have a `kind` outside `ac_contract.KNOWN_POST_MERGE_KINDS` (`stable_install` / `tag` / `restart`; the fix_hint lists them with a full example), or miss a field from `ac_contract.POST_MERGE_REQUIRED_FIELDS` (`stable_install`: `repo` / `path`, `tag`: `repo` / `tag`, `restart`: `processes`). `b1_migration.removed_trees_schema` reports non-string `removed_trees` items. Previously a free-text `post_merge` item passed B1 through merge and stopped M2 with a decision andon after the PR was merged (nexus #2972). M2's `ac_contract.run_checks` is unchanged.

- M1 gate `m1.version_behind_base` (nexus #3936). Before merging a CLEAN PR, the M1 step compares the branch's `pyproject.toml` version with `origin/<base>`'s; when it is not ahead (two Issues on the same repo published the same next version in parallel, so the second merge reused the first one's release tag and never got a release), `VersionBehindBaseGate.fix()` merges `origin/<base>` into the branch, re-runs the deterministic version bump against it (branch 0.81.0 / base 0.81.0 → 0.81.1; branch 0.80.0 / base 0.81.0 → 0.81.1), pushes, and M1 re-reads the merge state before merging. A failed fix resets the branch and reports `MERGE_FAILED_STAGES:version_behind_base` without merging. Targets without `pyproject.toml` (nexus) always pass. `ops.publish` now declares `__all__`, exporting `_run_version_bump` and `_bump_commits_on_top`, plus the public alias `run_version_bump` that the gate imports.

- `external_leak` gate: CJK added-line check for external targets (nexus #3909). With `external_leak.cjk_free_external_targets: true` in `issuesmith.yaml`, a branch whose Issue `target_repo` differs from the host `repo` fails P1's requires with `external_leak.cjk_added_line` (one violation per file, listing the added line numbers) when `origin/<base>...HEAD` adds lines containing CJK characters, literal or `\uXXXX` escaped; pre-existing CJK is ignored. The repair loop then rewrites the lines inside P1 instead of a later publish-time check stopping the pipeline without repair. New helpers `line_has_cjk`, `cjk_added_lines`, `is_external_target`; `ExternalLeakConfig` on the config.

### Added

- Dependency declarations as list items (nexus #3901 / #3905). `dep_extractor.extract_dependencies` now reads `- ` / `* ` / `+ ` / `1. ` / `1) ` items in the dependencies section in addition to table data rows and `依存:` lines (fenced code blocks are skipped). New `unparsed_dependency_refs(body)` returns `#N` refs that the dependencies section mentions without declaring them; `check_dependencies(..., unparsed_refs=)` / `check_issue` return `decision: BLOCK` with `reason: unparsed_dependency_section` (and `unparsed_refs`) before any forge call, `DepsGate` raises `deps.unparsed_dependency_section`, and the queue's phase preconditions refuse the issue. Previously a hand-written section such as `- #3855 (...)` extracted nothing and the dependency gate passed as "no deps" (fail-open), so P1 started on issues whose prerequisites were still open.

- Issue size gate `scope_size` (nexus #3665), run by B1 Verify (`b1_verify._GATES`). It reads only the Issue's change table (no clone) and fails with `scope_size.too_many_files` (counted files > `max_files`, default 8), `scope_size.too_many_concerns` (distinct parent directories > `max_concerns`, default 2) and `scope_size.delete_with_new` (deletion and creation mixed). `tests/` `docs/` `README.md` `CHANGELOG.md` `pyproject.toml` are not counted; `scope:milestone` Issues are skipped. The fix_hint proposes a sub-issue split plan per concern. Configure with top-level `scope_size:` in `issuesmith.yaml` (`ScopeSizeConfig`); set `scope_size: {enabled: false}` to turn it off. The vocabulary of the host's Issue bodies is configurable and ASCII by default: `delete_words` (`["delete"]`) / `new_words` (`["new", "add"]`) classify the change-type cell, `sub_plan_header` / `no_deps_word` shape the split-plan example in the fix_hint.

### Fixed

- `b1_ac_format` rejected the `post_merge` / `removed_trees` keys that `b1_migration` requires in the same AC YAML block, so a `scope:migration` issue could not pass B1 verify at all (`b1_ac_format.yaml_invalid` vs `b1_migration.post_merge_missing`; nexus #3899). Both keys are allowed; `tests/test_b1_verify.py` now runs a complete migration body through every B1 gate.

- Machine-readable andons (nexus #3678). `andon.list_open_records(client)` returns `asdict(Andon)` plus `raised_at` (the andon comment's `created_at`) and `notes`; `andon.note(client, id, key, value)` posts an `<!-- andon-note -->` comment (no label / metrics / resume change; `KeyError` when the andon is not open, `ValueError` for an empty key) that is folded into `notes` (same key: last write wins). CLI: `andon list --json` (`[]` when empty, same sort as the text output) and `andon note <id> --key <k> --value <v>`. `list_open()` now drops andons answered by an `<!-- andon-answer -->` comment and fetches each Issue's comments once; the andon comment format is unchanged.

- Base-branch health check (nexus #3664). `observe.main_health` (`MainHealthConfig(worktree, command, base_branch="main", timeout_seconds=1800)`) configures a command that `python3 -m issuesmith main-health` runs in a detached worktree of `origin/<base_branch>`; the result is written atomically to `<queue_state dir>/issuesmith-main-health.json` and the command is skipped while the SHA is unchanged. git / worktree / timeout failures raise `MainHealthError` (CLI exit 2) without touching the state. `observe()` reads the state (normal and `github_api_low` modes) and emits `MainRedEvent` / `MainGreenEvent`; policy halts `phase:develop` and raises one `broken` andon on red, and resumes on green. `HaltAction.keep_existing` keeps a halt raised by another event; `ResumeAction.event_kind` clears only a halt raised by that event (defaults keep the previous behaviour).

- `resume --from <step>` without `--handler` infers the handler from exec.jsonl (`_infer_handler_from_exec`: the `issuesmith:<handler>:<issue>` row whose `annotations.step_name` is the step) before falling back to the workflow YAML table, and prints `handler inferred from exec: <inferred> (table said <static>)` when they differ. When the handler has no run for an issue that exec.jsonl knows, it exits 1 with `no steps to reset: step <s> not found in handler <h> (try --handler …)` instead of letting `dag recover` reset 0 steps. Fixes `resume 3762 --from m1` resolving to `merge` while M1 / M2 run in the impl DAG (nexus #3844).

- `BaseFreshnessGate.fix()` no longer swallows a failed `git merge --ff-only`: on a diverged branch it rebases onto `origin/<base>`, and a conflicting rebase is aborted and raised (the violation stays blocking → andon instead of a silent loop). `run_requires_loop` bounds auto-fix rounds (`_MAX_AUTO_FIX_ROUNDS = 2`); remaining auto-fixable violations are waived after that (publish rebases at P3). `_run_pytest` gets a timeout (`ISSUESMITH_PYTEST_TIMEOUT_SEC`, default 1500 s) and returns 124 instead of hanging until the task timeout. Fixes P1 post-phase loops that hit the 3600 s task timeout while the base branch moved every few seconds (nexus #3865 / #3864).

- `steps/repair.py` asked the engine for role `implement`; the engine state only has `design` / `implementation`, so the requires repair loop (#3663) crashed with `KeyError` on its first real use and the step exited 1 without a message. Fixed to `implementation`; `tests/conventions/test_run_guarded_roles.py` rejects unknown literal roles.

- `publish` is idempotent after its own version bump: `_bump_commits_on_top` recognizes publish-made `chore: bump version to ...` commits at the top of `origin/<base>..HEAD`; the commit diff gate inspects the diff below them and `_maybe_bump_version` does not bump twice. `pr_list` failures (rate limit, network) return `PublishResult(status="PR_LIST_FAILED")` instead of a traceback. Re-running P3 after a failed PR lookup no longer fails with `P3_GATE_FAILED` on the bump commit.
- Derived allow_paths for newly failing tests (#3756). `TestsGate(allow_paths=...)` sets
  `derived_allow_paths` to test files under `tests/` that pass on base, fail on the branch and
  reference a changed file (path, module name, stem or public def/class in the diff);
  `run_requires_loop` keeps the union in `context["derived_allow_paths"]`, `PrScopeGate`
  (`derived_allow_paths=` / `GateBuildContext.derived_allow_paths`) allows them behind
  `check_derived_test_guard` (`derived_allow.test_weakened` / `derived_allow.test_skipped`),
  the repair instruction lists them, `run-guarded` prints a `derived_allow_paths:` block before
  `PIPELINE_STATUS:`, and CP2 accepts the files recorded in the P1 result. Disable with
  `derived_allow: {enabled: false}`
- GitHub API rate-limit brake (`api_brake:` in `issuesmith.yaml`, `ApiBreakConfig(enabled=False,
  min_remaining=800)`). `issuesmith.quota_gate` reads the latest `github_rate_limit` record that
  ghdag writes to `jobs/audit.jsonl`; while `remaining < min_remaining` and the reset time is
  still ahead, `dispatch_one` returns `github_api_low (remaining=…, reset=HH:MM)` before any
  forge call, and `observe()` runs a reduced, API-free mode (`dag_terminated` for in_flight
  Issues and `orphan_exec` only). `GitHubApiLowEvent` / `GitHubApiRecoveredEvent` are emitted
  once per state change (flag kept in quota-gate.json `resources.github_api_notified`) and map
  to notify-only andons (no halt). Disabled by default (#3769)
- `ScopeCouplingConfig.search_dirs`: configurable list of directories to grep for callers
  and tests (default `["tests", "src"]`). Set in `issuesmith.yaml` under `scope_coupling:
  search_dirs: [tests, src, workflows, tools, scripts]` to catch references in workflow
  templates, tool scripts, etc. (#3647)
- `scope_coupling._removal_names`: backtick identifiers and `` `${template_var}` `` names
  in removal-context table rows, removal section headings (heading text and body) are now unconditionally required search
  keys, allowing the gate to catch references to constants, YAML keys, and dataclass fields
  being removed (not just `def`/`class`-defined symbols) (#3647)

### Changed

- `observe()` cuts GitHub API calls: a per-tick `ObserveSnapshot` prefetches each in_flight /
  queued Issue once (`issue_get` with `labels`/`number`/`state`), and `_detect_dag_terminated` /
  `_detect_label_drift` read from it instead of fetching the same Issue again. All forge reads
  share one `max_api_calls` budget (one call reserved for the `develop-running` listing); when it
  is exhausted the remaining reads are skipped with a single WARN. No `/labels` or comments
  endpoints are called during observation. `ObserveConfig.max_api_calls` default 20 → 8.
  in_flight 3 with failed DAGs: 7 → 4 calls (#3768)

- `queue.dispatch_one` (tick) cuts GitHub API calls: Issue reads go through a tick-scoped
  cache (`_TickCachedForge`) so each Issue is fetched once per tick with the union of needed
  fields, and every `issues?state=open` list request (including the milestone
  `labels=scope:milestone` scan) shares one fetch filtered locally. `_find_untracked_running`
  consults DAG state first and skips the `develop-running` listing when no untracked DAG is
  running. Measured scenario (queue 1, in_flight 3, 17 stale running labels): 18 → 8 calls (#3759)

- `scope_coupling` gate now returns `scope_coupling.root_unavailable` (severity `fail`,
  `auto_fixable=False`) when the target repo clone is absent, instead of silently returning
  an empty violation list. This aligns with `scope_breadth.root_unavailable` (#3647)

- `andon.answer_if_open(client, andon_id, action)`: like `answer()` but silently skips
  when the andon is not found (already closed) and never calls `_call_resume_hook` (#3740)
- `andon list --all`: show all open andons in original unsorted order (new flag); default
  now sorts decision → broken → non-observe blocked → observe blocked (#3740)

### Changed

- `sync_observe_andons` now returns `tuple[set[str], set[str]]` = `(new_ids, resolved_ids)`
  instead of just `new_ids`; callers receive resolved IDs for auto-resolve (#3740)
- `dag_terminated` andon key is now `dag_terminated:{stripped_event_key}` (generation
  suffix stripped) so the same issue/phase/step deduplicates across generations (#3740)
- `observe/policy.execute()` auto-resolves `resolved_ids` andons by calling
  `answer_if_open`; `dag_terminated` andons are only auto-resolved when the issue is back
  in_flight (prevents premature resolution before user re-dispatches); deferred ids are kept
  via `QueueStore.retain_observe_andons` so a later re-dispatch still resolves them (#3740)


## 0.62.0 - 2026-09-25

### Added

- `run-guarded --requires-step <step>`: pre/post gate evaluation wraps the LLM call. Gates
  with `pre_llm=True` (`base_freshness`, `scope_breadth`) run before the LLM; the full
  `requires` list runs after via `run_requires_loop`. Non-repairable gate violations
  (`scope_breadth`) surface as `andon(decision)` with `widen:<files>`, `split`, `reject`
  options instead of triggering repair (#3663)
- `StepConfig.module` is now optional (empty string = LLM-only step). Dispatching a
  module-less step via `dispatch` raises `andon(broken)` immediately (#3663)
- `StepConfig.requires_declared`: `requires: []` in YAML is valid and means "explicitly no
  gates"; a missing `requires:` key in a non-repair step is a `validate_requires_chain`
  violation (#3663)
- `run_requires_loop` (renamed from `_check_requires_loop`): fetches a fresh issue body on
  every evaluation and rebuilds gates from scratch to avoid staleness (#3663)
- `changed_files(worktree_path, base_branch)`: returns sorted union of committed and
  uncommitted changes vs `origin/<base>`, excluding `jobs/`, `logs/`, `.pipeline-state/`
  (#3663)
- `PrScopeGate.check()` returns one `Violation(rule_id="pr_scope.out_of_allow")` per
  out-of-scope file; `fix_hint` follows `widen:<file>` pattern (#3663)
- `LintGate` and `ExternalLeakGate` now target `changed_files()` instead of `allow_paths`
  globs, eliminating false positives when globs over-match (#3663)
- `andon.answer("widen:<files>")`: updates `allow_paths` in the issue body YAML block and
  calls `resume(issue_num, from_step=step)` automatically; posts a comment only when no
  YAML block is present (#3663)

## 0.61.2 - 2026-09-25

### Added

- `resume <issue> --from <step> --force`: re-run the step and everything downstream even if
  they already succeeded (done markers and results are cleared). Until now re-running a
  succeeded step meant moving `jobs/done/<uuid>` aside by hand (twice on 2026-09-25, #3628)

### Fixed

- `resume --from` no longer deletes the result files of succeeded downstream steps that
  recover will not re-run (0.59.1 cleared every downstream result; a `--from` on a succeeded
  step would have left later steps without their inputs)

## 0.61.1 - 2026-09-25

### Fixed

- `publish`: the remote branch is fetched with an explicit refspec
  (`+refs/heads/<branch>:refs/remotes/origin/<branch>`). In the single-branch clones used for
  external repositories (`fetch = +refs/heads/main:refs/remotes/origin/main`) a bare
  `git fetch origin <branch>` never created `origin/<branch>`, so every publish after the
  first took the "new branch" path and was rejected non-fast-forward instead of using
  `--force-with-lease` (sumipan/nexus#3628 generation 3)

## 0.59.1 - 2026-09-24

### Fixed

- `resume --from <step>` restores what the restarted DAG needs instead of leaving it to the
  operator: the result files of the re-run steps are cleared first (ghdag keeps a non-empty
  result and discards the rerun's stdout, sumipan/nexus#3638), and after `dag recover` the
  `<phase>-running` label comes back (`<phase>-ready` removed) and the issue is registered in
  in_flight. Without this the P3 finalizer failed with "REPORT_DONE requires
  issuesmith:develop-running" on every recovery after #3662 released the slot
  (sumipan/nexus#3627 / #3696, 2026-09-24)

## [Unreleased]

### Changed

- `TestsGate` now runs all tests without `-x`, compares failures against `origin/<base_branch>`,
  and marks preexisting failures non-blocking (AC-6 / #3494 sub-2 AC-6). Added baseline comparison
  via temporary git worktree, fail-safe for collection errors (rc=2), and correct handling of
  parametrized test IDs with spaces (sumipan/nexus#3646)

### Removed

- `gate_rules.PREFLIGHT_PARITY`, `gate_rules.STOP_STATUS_SUFFIXES`,
  `gate_rules.assert_preflight_parity`, and the `StepResult.__post_init__` stop-status guard
  were all provisional scaffolding from #3487 / issuesmith#61. Stop-status quality is now
  guaranteed structurally by the requires-chain validation introduced in sumipan/nexus#3626 /
  #3627; the one-off parity table is no longer needed (sumipan/nexus#3628).
- `test_workflow_conventions.py` R3 tests (`test_r3_*`) removed alongside the code they
  guarded; R1 (contract-parser uniqueness) test is unchanged.
- `ScopeCouplingConfig.ignore_symbols` (31-word exclusion list) and its YAML parser removed;
  configs that still carry the `scope_coupling.ignore_symbols` key now raise `ConfigError` so
  operators remove the dead setting. The requires-chain validation introduced in
  sumipan/nexus#3626 / #3627 makes per-symbol exclusions structurally unnecessary
  (sumipan/nexus#3628).

### Added

- `branch_reuse.is_base_recorded(repo_dir, branch)`: public helper that returns True if
  `git config branch.<branch>.issuesmithbase` is set; used by both context_hook and P0 to
  avoid duplicating the lookup (sumipan/nexus#3695)
- `build_context` now returns a `reuse_source` key (`"comment"` / `"recorded"` /
  `"unrecorded"` / `"none"`) indicating how the pipeline_id was determined (sumipan/nexus#3695)
- P0 posts an Issue comment and prints `WORKTREE_REUSED: <branch>` to stdout when the
  target branch already existed before preparation (sumipan/nexus#3695)
- `issuesmith resume --from <step> --mark-done <step>`: explicitly mark a failed/cancelled/skipped
  ancestor step as succeeded before recovering, so `--from` can restart from a later step without
  re-running the already-completed work; `--mark-done` may be repeated for multiple ancestors
  (sumipan/nexus#3636)

### Fixed

- `_fetch_issue_comments_from_api` now calls `get_issue_comments` (full pagination, ghdag
  v0.72.0) instead of `issue_get(fields=["comments"])` which returned only the first 30;
  falls back to the old path if `get_issue_comments` raises (sumipan/nexus#3695)
- `find_reusable_branch` now accepts branches without `issuesmithbase` recorded (pre-0.56.0
  era) as long as they share common history with base, are not merged, and have at least one
  commit ahead; orphan/unrelated branches are still excluded (sumipan/nexus#3695)
- ghdag dependency bumped to `v0.72.0` (fixes full-pagination for comments and issues)
- `issuesmith resume --from <step>` now checks for upstream failures before calling
  `ghdag dag recover`; if an ancestor step is failed/cancelled/skipped/dep_failed, the command
  exits 1 with a message showing the blocking steps and two recovery options, instead of silently
  reporting success while no step actually restarts (sumipan/nexus#3636)

## 0.58.0 - 2026-09-24

### Added

- `observe.dag_state.load_dag_states`: reads DAG liveness from `exec.jsonl`, done markers, and
  `running/<uuid>.json` markers without any GitHub API calls; returns `DagState` per issue
- `observe.events.DagTerminatedEvent`: fired when an issue's DAG is `failed` and the issue is
  still tracked in `in_flight` or has a `develop-running` label
- `observe.policy.ReleaseInFlightAction`: removes the issue from `in_flight` and removes the
  `<phase>-running` label without adding a `<phase>-ready` label (preventing auto-redispatch)
- `observe._detect_dag_terminated`: detects failed DAGs and emits `DagTerminatedEvent`

### Fixed

- `_find_untracked_running` now only re-registers issues whose DAG is actually running (has a
  `running/<uuid>.json` marker); issues whose DAG has failed are excluded so they are not
  re-added to `in_flight` on every tick (sumipan/nexus#3662 bugfix 1)
- `_dispatch_pipeline_ready` no longer blocks for issues whose DAG is still running even when
  those issues are absent from `in_flight` (e.g. merged and auto-closed by GitHub before the
  DAG finished); only truly orphaned / pending exec rows continue to block (sumipan/nexus#3662
  bugfix 2)
- `_detect_orphan_exec` skips exec rows whose issue's DAG is still running, eliminating false
  orphan andons for CLOSED issues mid-execution (sumipan/nexus#3662)

## 0.57.2 - 2026-09-24

### Fixed

- `config.load_config` no longer imports the gate registry to validate `requires`. The registry
  imports every gate rule, some of which call `get_config()` at import time, so any
  `issuesmith.yaml` with `requires:` crashed every command with
  `ImportError: partially initialized module 'issuesmith.gates'` (sumipan/nexus#3687, found
  while resuming #3627). Gate ids and input_kind compatibility are now validated by
  `gates.validate_step_requires`, called by `doctor` (`requires_chain`), `dispatch` (fail fast,
  exit 2) and `config show`; the messages are unchanged
- `tests/conventions/test_config_load_is_import_light.py` loads a config that declares every
  registered gate in a fresh interpreter, and asserts the loader never imports `issuesmith.gates`

## 0.57.1 - 2026-09-24

### Fixed

- `publish`: a failing plain `git push` no longer escapes as a bare `CalledProcessError` (the P3
  step crashed with only a traceback while the commit was already complete, sumipan/nexus#3680).
  Pushes are retried twice on transient errors (network, auth) and then reported as
  `PUBLISH_STATUS: PUSH_FAILED` with git's stderr; rejections keep `PUSH_DIVERGED` without retry

## Unreleased

### Added

- `gates.GATE_REGISTRY`: unified registry now includes all `gate_rules/` ids (auto-imported from
  ghdag's `GATE_REGISTRY`), worktree gates (`lint`, `tests`, `external_leak`, `base_freshness`),
  and issue adapters (`deps`, `scope`, `pr_scope`). Every entry has a `build(GateBuildContext)`
  factory; the 4 hand-written tables (`_KNOWN_GATE_INPUT_KINDS`, `WORKTREE_GATE_IDS`,
  `dispatch._build_worktree_gate`, ghdag direct-lookup) are replaced by this single source.
- `gates.GateBuildContext`, `gates.GateBuildError`, `gates.GateEntry.build`: new public types.
- `gates.dep.DepsGate`, `gates.scope.ScopeGate`, `gates.pr_scope.PrScopeGate`:
  `RequiresGate` adapters wrapping existing check functions.
- `steps.repair`: new module. `run(ctx, step)` executes one LLM repair cycle via `run_guarded`,
  reads `ctx.repair_violations` / `ctx.repair_step_origin`, and guards against re-entry via
  `ISSUESMITH_REPAIR_ACTIVE`.
- `steps.base.StepContext`: `repair_violations` and `repair_step_origin` fields (default `""`).
- `ops.dispatch`: `_context_to_step` now passes `repair_violations` / `repair_step_origin` to
  `StepContext`. `_build_requires_gates` uses `GATE_REGISTRY.build()` instead of hand-written
  tables; `GateBuildError` → andon(broken). Repair step (`repair`) skips requires re-evaluation.
  `ISSUESMITH_REPAIR_ACTIVE` guard prevents re-entry from bash templates.
- `ops.doctor.validate_requires_chain`: also checks that requires ids are in `GATE_REGISTRY`;
  `repair` step is exempt from the "must have requires" rule.
- `ops.preflight.main`: runs `_check_requires_chain` and prints `requires_chain: ok / FAIL`.
- `README.md`: new `## Gates` table listing all 15 registered gate ids and their source.
- `tests/conventions/test_gate_registry_complete.py`: structural completeness tests for the gate
  registry (ghdag ids, worktree classes, README table parity).
- `tests/steps/test_repair.py`: unit tests for `steps.repair.run`.

### Changed

- `config._build_steps`: `_KNOWN_GATE_INPUT_KINDS` removed; gate validation now consults
  `GATE_REGISTRY`. Input_kind rule relaxed: issue gates can be used in worktree steps
  (body + labels are always available). Worktree gates remain worktree-only. Error message now
  says `missing gate ids` instead of `unknown gate ids`.
- `gates.worktree`: `WORKTREE_GATE_IDS` (frozenset) replaced by `WORKTREE_GATES` (dict of
  build factories).

- `branch_reuse`: new module with `find_reusable_branch`, `record_base`, and `previous_commits`.
  `find_reusable_branch` scans local branches for a previous-generation branch of the same Issue
  that has uncommitted work and a matching `issuesmithbase` config entry, enabling `context_hook`
  to reuse the same `pipeline_id` across `redispatch`/`resume` generations so P0 reconnects to
  the existing worktree instead of creating a new one.
- `context_hook.build_context`: new `previous_commits` key in the returned context dict.
  Populated with `<sha7> <subject>` lines when the `pipeline_id` was restored via branch reuse;
  empty string for comment-restored and new-uuid cases.

### Changed

- `context_hook.build_context`: `pipeline_id` determination now has a second fallback (between
  comment-restore and new-uuid): `branch_reuse.find_reusable_branch` searches the target clone
  for a local branch matching `feat/issue-N-[a-f0-9]+` with `issuesmithbase == base_branch`,
  not merged into base, and with at least one commit ahead. `target_repo` / `target_clone_path`
  resolution is moved before `pipeline_id` determination so the search uses the correct repo dir.
- `steps.p0_worktree._prepare_local` / `_prepare_cross_repo`: call
  `branch_reuse.record_base(repo_dir, branch, base)` after `prepare_worktree` succeeds (new
  creation, existing branch attach, and existing worktree reuse). Idempotent; diary branches are
  not recorded (they are not candidates for reuse).

## 0.55.1 - 2026-09-23

### Fixed

- `dispatch`: the `QuotaGate` used to register a deferred task now gets `brake_state_path`
  (`paths.brake_state`). Without it `release_ready` saw every engine as available and re-queued
  a brake-paused task on the next tick, so the step ran, deferred, and ran again (sumipan/nexus#3627
  cp2 launched twice within four minutes while all engines were brake-paused)

## 0.53.1 - 2026-09-23

### Fixed

- `observe.policy.execute` raises each andon **once per occurrence** instead of re-emitting it to
  the sinks on every tick (a halted milestone produced a Slack post every 30 minutes,
  sumipan/nexus#3621). `QueueStore.sync_observe_andons` remembers the ids whose condition is
  still present; an id is forgotten when the condition clears, so a recurrence is reported once
  more. `AndonAction.key` gives the condition a stable identity (`stall:<phase>`,
  `orphan:<uuid>`, `chain_halted`, `timeout:<uuid>`, `systemic:<step>:<class>`,
  `forge_unavailable`, `skew:<package>`) independent of counters in the summary
- `execute(..., client=)` makes the andon canonical for Issue-bound actions (Issue comment +
  attention label through `raise_andon`, sinks included); `observe --apply` passes the forge
  client. Calls without `client` keep the sink-only behaviour

## 0.53.0 - 2026-09-23

### Added

- CI runs `mypy src/issuesmith/` and `pytest --cov=issuesmith --cov-fail-under=72`
  (OSS_QUALITY chapter 8 section 8.5, sumipan/nexus#3574). `[tool.mypy]` (python 3.10,
  `warn_unused_ignores`, missing imports ignored only for ghdag / yaml) and `[tool.coverage.run]`
  in `pyproject.toml`; `pytest-cov` and `mypy` in the `dev` extra
- `src/issuesmith/py.typed` (PEP 561), shipped through `[tool.setuptools.package-data]`

### Changed

- Coverage floor is 72: measured 72.2% on 2026-09-23 (9922 statements, 2757 missed, full suite),
  above the 70 floor ghdag uses, so the measured value is the one that must not drop
- mypy baseline: 35 errors on 33 lines silenced with `# type: ignore[<code>]  # TODO(#3611)`
  (12 `ForgePort.api_request` attr-defined, 3 `Template.get_identifiers`, 14 Optional handling,
  1 `FailureClass` call-arg, 1 no-redef); 10 stale `type: ignore` comments removed. The dead
  `ops.label_hygiene` import in `cli.py` is silenced by a `[tool.mypy.overrides]` block tagged
  `TODO(#3612)` because its error code depends on the environment.
  Reducing the backlog is tracked in sumipan/nexus#3611
- CI installs ghdag from the pyproject pin instead of a hardcoded `v0.52.0`
## 0.52.1 - 2026-09-23

### Added

- `tests/conventions/`: structural tests (OSS_QUALITY chapter 8, sumipan/nexus#3571). CLI table vs
  usage / docs / deprecation advice, lazily imported handler modules, `__all__` resolution with a
  shrinking list of private cross-module imports, no nexus workflow vocabulary in verbs / gates /
  observe / andon / labels / dispatch / engine, every ghdag name used exists in the pinned ghdag, and
  write / read-back round trips on `LocalForge` (andon raise / list / answer, label apply,
  `MERGE_DONE` projection vs `labels.project`, queue enqueue / in-flight / complete). Each
  detector has a reproduction test for the incident it guards against (#3566, #3515, #3507)
- `doctor` prints `upstream_apis: ok` / `upstream_apis: missing: ...` from
  `ops.preflight.REQUIRED_UPSTREAM_APIS` (ghdag names reached at runtime through getattr)

### Known violations (strict xfail, remove the entry when fixed)

- `cli.py` `labels` handler imports the removed `issuesmith.ops.label_hygiene` (sumipan/nexus#3612)

## 0.52.0 - 2026-09-23

### Changed

- `dispatch`: a `RetrySignal` now registers the running task with `QuotaGate.defer` (uuid from
  `GHDAG_TASK_UUID`, role engines from `ROLE_ENGINES`) and prints `PIPELINE_STATUS: DEFERRED`,
  so ghdag marks the task `DONE_DEFERRED` and `release_ready` re-queues it once an engine is
  available again (sumipan/nexus#3515). Previously the task exited 0 with no status and stayed
  waiting until a manual `resume`
- ghdag pin raised to `v0.68.0` (exports `GHDAG_TASK_UUID` to launched tasks)

## 0.51.0 - 2026-09-22

### Changed

- `gate_rules.scope_coupling` requires follow-up only for callers / tests of public symbols
  defined in the files the Issue changes and for basenames of deleted or moved files
  (declaration-derived keys). Basenames of `allow_paths` entries and identifiers defined elsewhere
  are reported for reference in `fix_hint` and never widen `allow_paths`, so the requirement no
  longer grows with `allow_paths`. When widening would exceed `scope_breadth.max_files`, the
  message says so (sumipan/nexus#3527).
- Issue YAML `scope_mode: internal` declares an unchanged public interface; `scope_coupling`
  returns no violations for it (sumipan/nexus#3527).

## 0.50.4 - 2026-09-22

### Fixed

- `observe`: `orphan_exec` reads the issue number from the third segment of the idempotency key,
  so generation-suffixed keys (`issuesmith:impl:3548:1`) no longer report issue `#1`
  (sumipan/nexus#3523).

## 0.50.3 - 2026-09-22

### Fixed

- `ops.labels.project`: the `queued` marker is additive. A queued issue keeps its phase label
  (for example `draft-done`, which `queue._phase_preconditions` requires before `develop`), so
  `labels reconcile --fix` no longer strips a label that dispatch depends on (sumipan/nexus#3601).

## 0.50.2 - 2026-09-22

### Fixed

- `observe --json` no longer raises `TypeError: Object of type frozenset is not JSON serializable`:
  `LabelDriftEvent.add` / `.remove` are sorted tuples and the CLI passes a JSON default for sets
  (sumipan/nexus#3590).
- `resume --from <step>` accepts `--workflow` (default: stem of `paths.workflow`, e.g. `issuesmith`)
  and `--handler`, and forwards `--workflow` to `ghdag dag recover`. Hosts with several workflow
  files previously got `multiple workflows found ... specify --workflow` on every call
  (sumipan/nexus#3590).

## 0.50.1 - 2026-09-22

### Added

- `tests/conftest.py`: session-scoped autouse `_no_side_effects` fixture that guards against
  filesystem writes outside pytest's tmp directory. The fixture redirects module-level path
  constants (`QUOTA_STATE_PATH`, `BRAKE_STATE_PATH`, `EXEC_PATH`, `DONE_DIR`, `JOBS_DIR`,
  `DEFAULT_TRIAGE_LOG_PATH`) to `basetemp` and sets `ISSUESMITH_QUEUE_DIR` so `dispatch_one`
  and triage writes stay within the test sandbox. Any write outside `basetemp` raises
  `AssertionError("side effect outside tmp: <path>")`. A session finalizer asserts that
  `jobs/`, `logs/`, and `.pipeline-state/` do not exist at the repo root after the suite.
- `tests/test_no_side_effects.py`: smoke tests verifying the write-guard harness (AC-1) and
  that no runtime directories appear at repo root (AC-2).

## 0.48.1 - 2026-09-22

### Added

- `scope_coupling.enabled` (default `true`): set `false` in `issuesmith.yaml` to turn the
  CP1/B1 scope coupling gate off. The gate demands every caller of a core module and
  contradicts `scope_breadth` (nexus #3431 / #3504 / #3522); it stays off until redesigned
  in nexus #3527.

## 0.43.1 - 2026-09-21

### Fixed

- `steps.cp2_checkpoint`: `success_statuses` no longer lists `CP2_SKIPPED`. Since 0.43.0 (#3505)
  `engine.run_guarded` raises `ValueError` for `*_SKIPPED` success markers, so every CP2 failed in
  its deterministic prefix before the review started and raised an empty `decision` andon
  (sumipan/nexus#3506, 2026-09-21). `CP2_SKIPPED` now falls through to `CP2_FAILED` (R2).
- `steps.cp2_checkpoint.run` re-raises `engine.RetrySignal` so a paused engine is deferred by
  `ops.dispatch` instead of being reported as a review failure; other exceptions print a
  `REASON:` line before `CP2_FAILED`.

## 0.42.0 - 2026-09-21

### Changed

- `engine._execute` no longer sleeps up to `ISSUESMITH_ENGINE_WAIT_MAX_SEC` while every allowed engine is
  paused; it raises `RetrySignal(reason=QUOTA_PAUSED, after=resume_at)` immediately and `ops.dispatch`
  registers a ghdag `QuotaGate` defer and applies the `<namespace>:waiting` label (exit 0, no
  `DEP_FAILED`). Merged as PR #63 for sumipan/nexus#3485 without a version bump, so v0.41.0 did not
  include it; this release exists to publish it.

### Added

- `issuesmith.contract`: single canonical parser for the change table (bold label and heading forms)
  used by the B1 gate, milestone helpers and SUB1 alike; `CONTRACT_EXTRACTORS` names the functions
  that may only be defined there (#3487)
- `gate_rules.PREFLIGHT_PARITY` + `tests/test_workflow_conventions.py`: deterministic detector for
  the workflow design conventions (R1 one parser per contract section, R3 every runtime stop status
  has a preflight rule or an explicit runtime-only reason) (#3487)
- `b1_milestone_subdesign.change_paths_unreadable`: B1 fails when a `#### サブN` change table yields
  no path for its repo — the exact condition under which SUB1 cannot build a child (#3487)
- `scope_breadth.root_unavailable`: CP1 fails closed when the cross-repo clone is missing (#3487)
- `tests/contract/`: fixture parity tests feeding the same body to gate and step (#3487)
- `steps.scope_gate.resolve_scope_root`: single root resolver shared by CP1 (`gate_rules.scope_breadth`),
  `gate-preflight`, and P0 (`steps.p0_worktree`) — replaces the private `_resolve_root` copy that
  diverged from what P0 measured (#3487)
- CP1 / `b1_verify` narrow oversized `allow_paths` deterministically to the union of the change table
  and the AC `paths_must_exist` list and continue, instead of failing, when the narrower set is itself
  within threshold; the rewritten allow_paths are persisted to the Issue body and a comment records the
  before/after (#3487)
- `scope_gate.record_p0_trip_metric` + P0 comment wording: a P0 trip after CP1 passed is flagged as a
  gate contradiction (workflow defect) and recorded as `scope_gate.p0_trip` in `jobs/metrics.jsonl` (#3487)

### Fixed

- SUB1 never inherits the parent's `allow_paths`; a row whose change table is unreadable fails
  instead of creating a child with the parent's `tests/**` (nexus #3483 tripped P0 with 91 files) (#3487)
- `scope_breadth` measured `.claude/external/<owner>/<repo>` (never exists) and passed silently;
  now uses `paths.external_dir/<repo>`, the layout `context_hook` actually clones into (#3487)

- `tests/test_no_cjk.py`: CI gate enforcing zero CJK characters in the fully-migrated test files
  (`steps/test_m1_merge.py`, `test_body_editor_shim.py`, `gate_rules/test_cp1_gate.py`) (#3385)
- `tests/gate_rules/test_cp1_gate.py`: ASCII-only duplicate of core CP1 gate tests, no Japanese
  fixture data (#3385)

### Changed

- `tests/steps/test_m1_merge.py`: Replace Japanese PR/issue titles in live-capture fixtures
  with English equivalents (#3385)
- `tests/test_body_editor_shim.py`: Replace Japanese section names with English in all assertions
  (#3385)
- `tests/test_body_editor_normalize.py`: Inject English config in `relocate_sub_plan` tests;
  replace hardcoded Japanese section names with config-driven English names (#3385)

### Fixed

- `steps.sub1_create._run_guarded_body`: `resolve("implementation", "default")` が TIERS（light / heavy）外で
  ValueError になり、子 Issue の LLM 本文生成が常にスキップされて雛形（設計空・AC「(from parent)」）のまま
  起票されていた。tier を渡さない（state のモデルをそのまま使う）ように修正（nexus #3379 / #3322）
- `steps.sub1_create._resolve_dependencies`: 依存の連番参照を substring 置換していたため `#2, #3` が
  `#3382` 置換後に `#3` が `#3382` 内へマッチして `#3383382` になり、子 Issue 検証（V4）で chain が
  halted になっていた。トークン単位の正規表現置換に変更し、milestone 参照の除外も同じ経路に統合（nexus #3379 / #3301）

### Fixed

- `ac_contract.run_checks`: `references_must_resolve` の plain string 形式（`- docs/FOO.md`）を
  「ファイルが存在すること」の検査として受理し、`key_path` 省略の dict も同様に扱う。
  従来は `TypeError: string indices must be integers` で M2 が落ちていた（nexus #3290）
- `steps.m2_finalize._evaluate_dual_root`: 契約実行中の例外を fail-open（stderr ログ + 基本判定を返す）にし、
  契約フォーマット変異で impl ステップ全体がクラッシュしないようにする（nexus #3290）

### Added

- P0 **scope_gate** (`steps.scope_gate` / `config.ScopeGateConfig`): worktree 作成後に
  `allow_paths` 一致の追跡ファイル数・行数を計測し、既定閾値（80 files / 20000 lines）超過時は
  Issue コメント + `issuesmith:scope-too-large` + `SCOPE_TOO_LARGE` で P1 を止める。
  Issue YAML の `scope_gate.max_files` で個別上書き可（`hard_max_files` は CP1 が検証）（#3349）
- `paths.brake_state`: issuesmith budget gate（既定フォールバックは `quota_state`）を追加し、
  queue / engine の pause 判定を global quota gate と budget gate の和集合にする。
  `call_managed` と `rate_limit_detected` の書き込み先は `quota_state` に固定する（#3263）
- `pr_diff_scope` gate (`issuesmith.pr_scope.check_pr_diff_scope`): CP2 冒頭で
  PR 変更ファイルを `allow_paths` / `forbidden_pr_paths`（`jobs/**`・`*.jsonl` 等）と照合し、
  違反時はコメント + `CP2_FAILED` で M1/M2 を止める。P0 は worktree 作成後に `jobs/` dirty
  を検査する（#3178）
- `gate_rules.milestone_consistency`: 本文の分割計画パターン（`### サブイシュー分割計画` /
  `#### サブN:` / `#### Sub N:`）と `scope:milestone` ラベルの矛盾を検知する。
  CP1 `check_gate()` と B1 Verify に組み込み、誤って develop 高速経路へ進むのを防ぐ（#3077）
- `body_editor.normalize_sub_headers` / `relocate_sub_plan`: 英語サブ見出しの正規化と、
  `## 設計` 配下の分割計画を `## マイルストーン` へ移す決定論正規化（#3077）
- `issuesmith convert-to-milestone <N>`: develop 誤投入 Issue を milestone 経路へ
  1 コマンドで復旧（DAG cancel・ラベル・milestone オブジェクト・in_flight 除去・
  draft redispatch）。`--dry-run` 対応（#3077）
- `redispatch --phase sub`: milestone 経路の sub フェーズ再投入。`cmd_redispatch` は
  冒頭で stale `in_flight` を除去する（#3077）
- `gate_rules.cp1.check_test_version_exact_assert`: tests/ の `version == "X.Y"` /
  `git+https://…@vX.Y` 完全一致 assert を unified diff から検出する（#3065）
- Milestone chain multi-repo validation: V1 expects child `target_repo` from the parent
  split-plan `対象リポジトリ` column (falls back to parent `target_repo` when the column
  is absent); unsupported repos fail V1; V2 `allow_paths` checks only change-table rows
  for the child's `target_repo` (#2961)
- `issuesmith milestone status` shows each child's `target_repo`

### Fixed

- engine: 全エンジン pause 時に `resume_at=None`（budget-brake 等）でも最大 30〜60 分
  一括 sleep せず、`ISSUESMITH_ENGINE_WAIT_POLL_SEC`（既定 60 秒）ごとに
  `QuotaGate.snapshot()` を再取得する。待機と LLM 実行の合計は `ISSUESMITH_TIMEOUT_SEC`
  に収め、待機予測分を `call_managed` timeout に加算しない。2026-09-13 の nexus #3252
  CP2 で codex pause 解除後も長時間再確認されず `DAG_TASK_TIMEOUT` と二重実行した事象の修正（#3256）
- publish: rebase 後の再 push で non-fast-forward にならないよう、リモート tip が
  `HEAD` の祖先でないときは `git push --force-with-lease=<branch>:<remote_sha>` で
  更新する。lease 失敗（他者の未知コミット等）は `PUBLISH_STATUS: PUSH_DIVERGED`
  を返す。push 前に `fetch` し `rev-list` の ahead/behind を stdout に残す（#3237）
- publish: `_ensure_rebased` が rebase 前に `RUNTIME_DIR_EXCLUDES`（`jobs/**`・
  `chat/**`・`sessions/**`・`logs/**`）配下の未コミット変更を破棄する。allow_paths
  内に汚れが残る場合は `PUBLISH_STATUS: DIRTY_WORKTREE`、本物の競合は
  `REBASE_CONFLICT`（競合ファイル一覧付き・`--abort`）を返す。nexus worktree で
  実行時ファイル汚れにより P3 が全件 `REBASE_CONFLICT` になる障害を防ぐ（#3227）
- publish: `_check_commit_diff_gates` の差分を二点ドット（`origin/<base>..HEAD`）から
  三点ドット（`origin/<base>...HEAD`、merge-base 起点）に変更。base が進んだだけで
  逆方向の `version =` 差分が写り `P3_GATE_FAILED` になる偽陽性を防ぐ。publish 前に
  `git fetch` + 必要時 `rebase` し、競合時は `PUBLISH_STATUS: REBASE_CONFLICT` を返す（#3221）
- M1: PR マージ成功後に `post_merge_test` だけ失敗したとき `issuesmith:merge-running` を付与し、
  M1r → M2 のラベル遷移欠落を防ぐ（#3221）
- M2 finalizer: `develop-done` / `merge-ready` / `merge-running` から `merge-done` へ到達可能にし、
  `transition()` がフェーズラベル欠落で失敗したときは `issue_update` で直付替する fallback を追加（#3221）
- queue: design engine（claude）が paused でも implementation ロールの
  `develop` / `merge` / `sub` は投入できるよう、paused 判定をフェーズ別ロールに変更。
  CP2（design）は `resume_at=null` でも固定インターバルで sleep & retry し、
  既定最大 6h まで REJECT を遅らせる。`call_managed` の timeout にも待機分を加算（#3091）
- queue: `draft-done` 直後の design スロット解放がラベルのみに依存していたため、
  brushup/impl の未完了 exec がある Issue が `in_flight` から消え、watcher が
  起動した impl DAG が追跡外になり `_dispatch_pipeline_ready` が queue 全体を
  止めていた。未完了 exec がある間は解放せず、`develop-running` なのに
  `in_flight` 不在の Issue は tick 時に再登録する。`queue status` /
  `queue doctor` で追跡外実行中 Issue を表示する（#3092）
- publish が commit 後・version bump 前に `check_version_line_in_diff` と
  `check_test_version_exact_assert` を実行し、LLM による `pyproject.toml` の
  `version =` 変更および tests/ の版・pin 完全一致 assert を `P3_GATE_FAILED` で止める（#3065）
- `pyproject.toml` の ghdag 依存バージョンを v0.35.0 から v0.44.0 に更新（`[ghdag]` / `[dev]` extras）
- `README.md` のバージョン表記・インストール手順を v0.1.0 → v0.10.0 に更新、冒頭に参照版と確認日を追加
- `src/issuesmith/github_api.py` のエラー文を旧ドット形式（`issuesmith.queue enqueue`）から統一 CLI 形式（`issuesmith queue enqueue`）に修正
- `engine check` が `DEFAULT_LIGHT_MODELS`（`issuesmith.yaml` の `engines.<role>.light_model.<engine>`）全項目を許可リストと突合し、外れていれば修正キーを案内する。`engine resolve --tier light` に `allowlist_valid` / `reason` を追加。`--engine` 上書き経路でも allowlist 外 light は heavy にフォールバックする（nexus #2981）
- `_iter_issuesmith_exec_records` が世代付き冪等キー（`issuesmith:impl:2959:1`、redispatch 時に ghdag が付与）の末尾要素を issue 番号と誤解釈し、再投入中の DAG を in_flight 外の「孤児」とみなして `pipeline not idle` で全 dispatch を止めていた。`issuesmith:<handler>:<issue>[:<generation>]` を正しく解釈する（2026-09-09、nexus #2959 の再投入で実測）

### Changed

- Milestone chain: when all children are `CLOSED` with `issuesmith:merge-done`, auto-close the parent (config `milestone_chain.auto_close_parent`, default `true`). Children closed without merge-done halt the chain for human review (#2959)

## 0.8.2 - 2026-09-09

### Fixed

- queue が `*-ready` を付けるとき、ハンドラーの冪等キーが消費済みなら `ghdag trigger --redispatch` で世代を上げて起動する
  （従来は `redispatch` / `enqueue --force` 後に watcher が「already dispatched」で skip し続けた）
- `PIPELINE_STATUS` の独立行判定がバッククォート / 太字の装飾を許容する（codex が装飾付きで出力し CP2 PASS が FAIL 扱いになった）

## 0.8.1 - 2026-09-09

### Fixed

- `engine resolve --tier light` は light モデルが `configs/llm-models.yml` の allowlist に無い場合、
  `EngineModelError` でパイプラインを止めず heavy モデルへフォールバックする（#2968 / #2986 の再発防止）
- `engine check` が state 未設定時の config 既定 light モデルも allowlist と照合する
- codex の light 既定モデルを `gpt-5.4-mini` → `gpt-5.5`（ChatGPT アカウント認証で 400 になる）

### Added

- Queue allow_paths conflict gate, `concurrency.strict_order`, draft-done in_flight release, and role-aware in_flight accounting (#2980)
- `sub` queue phase with `issuesmith:sub-ready` / `sub-running` / `sub-done` labels
- Milestone chain automation (`milestone_chain` config, `advance_milestone_chains`, `milestone status` / `milestone resume` CLI)
- Public `get_dep_status` / `is_satisfied` APIs in `dep_extractor`

## 0.1.0 — 2026-09-05

### Added

- Initial release of `issuesmith` as a standalone package extracted from nexus (`tools/issuesmith`)
- Unified CLI: `python3 -m issuesmith <command>` / `issuesmith` console script
- Core modules: context hook, gates (cp1/m2/b1), queue/triage, engine, ops (dispatch/publish/doctor/smoke/version-bump)
- Configuration via `issuesmith.yaml` (env `ISSUESMITH_CONFIG`, cwd walk-up, package-root fallback, builtin defaults)
- Depends on ghdag `v0.35.0` (optional `[ghdag]` / `[dev]` extras)
