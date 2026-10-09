# issuesmith

issuesmith is a toolkit for GitHub Issue workflows driven by labels, built on [ghdag](https://github.com/sumipan/ghdag). ghdag runs the DAGs, polls labels and moves Issues between phases. issuesmith supplies the Issue side of that loop: design and merge gates, a request queue with triage, lane planning, LLM role switching, label projection, andons, and the single CLI that workflow templates call.

**What it is not:** issuesmith is not a scheduler or a DAG runner (that is ghdag), and it does not call an LLM API directly. LLM steps run through the engine CLIs (`claude`, `codex`, `cursor`) that ghdag dispatches.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-v0.146.1-blue)
![ci](https://github.com/sumipan/issuesmith/actions/workflows/ci.yml/badge.svg?branch=main)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

The current release is **v0.146.1** (pre-1.0). Public interfaces can change in any `0.Y.0` release. Breaking changes are listed in [CHANGELOG.md](./CHANGELOG.md).

## Installation

```bash
pip install "issuesmith[ghdag] @ git+https://github.com/sumipan/issuesmith.git@v0.146.1"
```

The gates, forge access, `observe`, `andon`, `lanes` and `metrics` import ghdag, so install the `ghdag` extra unless another package already provides ghdag. The base package alone is:

```bash
pip install "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.146.1"
```

| Item | Value |
|---|---|
| Python | `>=3.10` (tested on 3.10, 3.11, 3.12) |
| Runtime dependencies | `pyyaml`, `ruamel.yaml`, `packaging`, `python-dotenv` |
| `[ghdag]` extra | `ghdag @ git+https://github.com/sumipan/ghdag.git@v0.72.0` |
| `[dev]` extra | `pytest`, `pytest-cov`, `mypy`, `ruff`, ghdag `v0.72.0` |
| Console script | `issuesmith` (`issuesmith.cli:main`) |

## Quick Start

1. Put an `issuesmith.yaml` at the root of the host repository. `repo` and `phases` are required; every other key has a default (see [Configuration](#configuration)):

```yaml
repo: owner/my-repo
supported_repos:
  - owner/my-repo
phases:
  - name: draft
    role: design
    entry_step: b1
    handler: brushup
    writes_files: false
    advance_when: [deps_terminal]
  - name: develop
    role: implementation
    entry_step: cp2
    handler: impl
    steps: [p0, p1, p3, cp2]
    preconditions: [draft-done]
    advance_when: [deps_terminal]
  - name: merge
    role: implementation
    entry_step: m2
    handler: merge
    steps: [m1, m2]
    advance_when: [deps_terminal, closing_pr_exists]
steps:
  p1:
    requires: [lint, tests, pr_scope]
    input_kind: worktree
```

2. Check the environment and the resolved configuration:

```bash
export ISSUESMITH_CONFIG=/path/to/issuesmith.yaml   # not needed when the file is in cwd or a parent
python3 -m issuesmith doctor
python3 -m issuesmith config show
python3 -m issuesmith engine show
```

3. Run a gate on an Issue body, queue an Issue, and resume a stopped run:

```bash
python3 -m issuesmith gate cp1 --body-file body.md
python3 -m issuesmith queue enqueue --issue 123 --phase draft --source cli \
  --actor-kind human --priority normal --requested-by me
python3 -m issuesmith queue tick
python3 -m issuesmith resume 123 --from p1
```

Day-to-day operation uses `python3 -m issuesmith andon list`, `python3 -m issuesmith observe --json`, `python3 -m issuesmith lanes check --json` and `python3 -m issuesmith metrics rework --since 2026-10-01`.

## CLI Reference

Run a command as `python3 -m issuesmith <command>` or through the `issuesmith` console script. The top-level commands are the keys of `issuesmith.cli._HANDLERS`. `issuesmith.__main__` handles `andon` (and `labels`) before `_HANDLERS`, so `andon` is available only through `python3 -m issuesmith`.

| Command | Description |
|---|---|
| `context` / `context_hook` / `context-hook` | ghdag context hook: Issue YAML metadata and step context |
| `gate <name> --body-file F` | Same as `gate-preflight --gate <name> --body-file F` |
| `gate cp1 <issue>` / `gate m2 <issue>` | Run the CP1 / M2 gate entry points on an Issue |
| `gate-preflight` | ghdag gate runner (`ghdag.workflow.gates`) with the issuesmith rules registered |
| `cp1-gate` / `m2-gate` | CP1 pattern gate / M2 acceptance-criteria gate |
| `verify b1` / `b1-verify` | Deterministic Verify of B1 output (`--prev-report FILE` adds oscillation detection) |
| `queue <subcommand>` | Request queue (see [`queue`](#queue-subcommands)) |
| `deps` | Extract and check Issue dependencies |
| `tier b1` / `tier cp2` | Pick the light or heavy model tier for B1 / CP2 |
| `comments` | Pipeline comment filter |
| `gh` | Issue / PR helpers on the ghdag forge client, with label and target guards (does not shell out to the `gh` CLI) |
| `engine <subcommand>` | LLM role switcher and runner (see [`engine`](#engine-subcommands)) |
| `dispatch` | Live re-render runner for shell step bodies |
| `publish --issue N --branch B --base B --worktree W --repo R --issue-repo R [--allow-paths A] [--target-count N]` | Push the branch, run the version bump and open the PR |
| `labels reconcile [--fix] [--json]` | Report (or fix) managed-label divergences on all open Issues |
| `doctor` | Runtime environment health check |
| `smoke [issues...] [--workflow NAME]` | Smoke-test the pipeline templates on real Issues |
| `gen-live` | Generate shell step trampoline templates |
| `version-bump --worktree W --base B` | Deterministic `pyproject.toml` bump and CHANGELOG fold (see below) |
| `resume <issue> --from <step>` / `resume <issue> --phase <phase>` | Resume a workflow; `--workflow`, `--handler`, `--mark-done STEP` (repeatable) and `--force` apply to `--from` |
| `recover` / `redispatch` | Deprecated (see [Deprecated API](#deprecated-api)) |
| `convert-to-milestone <issue> [--dry-run]` | Move an Issue that entered the develop path back to the milestone path |
| `milestone <subcommand>` | Milestone chain (see [`milestone`](#milestone-subcommands)) |
| `config show` | Print the resolved configuration as JSON and validate `steps.*.requires` (exit 1 when invalid) |
| `observe [--apply] [--json]` | Collect observe events; `--apply` also runs the policy actions |
| `lanes <subcommand>` | Lane ledger planning (see [`lanes`](#lanes-subcommands)) |
| `metrics rework [--since YYYY-MM-DD] [--issue N] [--audit PATH] [--json]` | Rework report from `paths.metrics` and an optional ghdag audit JSONL; `--since` uses `timezone` |
| `main-health` | Run `observe.main_health.command` on the base branch and write the state file (exit 2 on `MainHealthError`) |
| `apply` / `ingest-review` | Moved to the host's `tools/stash/`; print a notice and exit 2 |

`version-bump` prints `VERSION_BUMP_TYPE` and `VERSION_BUMP_REASON`, then commits `chore: bump version to <new>`. It bumps Y (minor) when the diff adds or removes a public `def` / `class`, an `__all__` entry or a `[project.scripts]` / entry-point key, or when a commit carries a Conventional Commits breaking marker (`type!:` or `BREAKING CHANGE:`); otherwise it bumps Z (patch). It never changes X (major). When `CHANGELOG.md` has a non-empty `## Unreleased` section, the bump inserts the new release heading under it, so the Unreleased entries become the release notes of the bumped version (`ops.version_bump.fold_unreleased`).

### `andon` subcommands

| Subcommand | Description |
|---|---|
| `andon list [--all] [--json]` | List open (unanswered) andons; `--json` adds `raised_at` and `notes` |
| `andon show <andon-id>` | Show one andon |
| `andon answer <andon-id> <action>` | Post the answer, remove the label and call the resume hook |
| `andon note <andon-id> --key K --value V` | Record a note on an open andon (no label change) |
| `andon auto-answer [--apply] [--json]` | Plan (or apply) the `andon.auto_answer` rules |

### `engine` subcommands

| Subcommand | Description |
|---|---|
| `engine show` | Print the effective engine and model per role |
| `engine check` | Validate commands, models and workflow drift |
| `engine switch <role> <engine> [--model M] [--light-model M]` | Switch one role (`design` or `implementation`) |
| `engine run-guarded <role> <template> --failure-status S [--cwd D] [--tier T] [--success S] [--emit-status S] [--requires-step ID] [vars...]` | Run an LLM template, then evaluate the step's `requires` gates |
| `engine run-verified <role> <template> --failure-status S --verify V --recover R [--max-loops N] [--recover-tier T] [--skip-verify-on S] ...` | Run with a verify / recover loop (`--max-loops` default 2, `--recover-tier` default `light`) |
| `engine run` / `engine exec` / `engine resolve` | Internal dispatch helpers (hidden from `--help`) |

`--tier` takes `light` or `heavy`. A step without a `module` runs as `engine run-guarded --requires-step <step_id>`.

### `queue` subcommands

Global options: `--queue-path`, `--state-path`, `--lock-path`.

| Subcommand | Description |
|---|---|
| `queue enqueue --issue N --phase P --source S --actor-kind {human,automation} --priority {high,normal,low} --requested-by X [--force] [--after N]` | Queue an Issue for a configured phase (`--after` is repeatable) |
| `queue tick [--seed PATH]` | Triage and dispatch queued requests |
| `queue status` | Show the queue state |
| `queue doctor` | Report untracked in-flight Issues |
| `queue reset [--keep-last-issue]` | Clear the halt state |
| `queue skip --issue N [--reason R]` | Skip an Issue |
| `queue dequeue --request-id ID [--reason R]` | Remove a request |
| `queue audit [--offline]` | Check queue integrity (`--offline` skips the GitHub checks) |
| `queue triage-log [--last N] [--path P]` | Show recent triage log entries (default 20) |
| `queue migrate [--from-night-queue-state P] [--seed P] [--dry-run]` | Migrate from the legacy night-queue state |
| `queue release --issue N` | Release a design-slot in-flight entry (recovery only) |

### `lanes` subcommands

`lanes` reads the ledger at `paths.lanes` and exits 2 when that key is not set or the ledger is invalid.

| Subcommand | Description |
|---|---|
| `lanes check [--apply] [--json]` | Plan slots, candidates, enqueues and escalations per lane; `--apply` enqueues the plan (skipped while the GitHub API budget is low) |
| `lanes report [--json]` | Patrol report: the lane plan plus `andon.auto_answer` escalations and merged Issues since `report_since`; state is kept in `<ledger>.state.json` |

Ledger keys: `lanes` (lane name to Issue numbers), `hold`, `backlog`, `auto_lanes.enabled` (`false`), `queued_stale_minutes` (`30`), `api_min_remaining` (`api_brake.min_remaining`), `report_every_minutes` (`120`), `report_since` (ISO timestamp or `daily`).

### `milestone` subcommands

| Subcommand | Description |
|---|---|
| `milestone status <parent>` | Show the milestone chain status |
| `milestone resume <parent>` | Resume milestone chain progress |
| `milestone prune [--dry-run]` | Prune finished milestone chain state |
| `milestone consolidate <child> --into <sibling>` | Mark a child as consolidated into a sibling so the chain treats it as merged when the sibling merges |

## Public API

`issuesmith.__all__` is empty; import the submodules. Modules with `__all__` are listed by that list; other modules list their public definitions. Names that start with `_` are internal.

### Gates

| Module | Symbols |
|---|---|
| `issuesmith.gates` | `Verdict`, `GateBuildContext`, `GateBuildError`, `GateEntry`, `RequiresGate`, `GATE_REGISTRY`, `resolve_gate`, `validate_step_requires`, `check_scope`, `check_pr_scope`, `check_m2`, `check_deps` |
| `issuesmith.gates.base` | `ContractInput`, `Gate` |
| `issuesmith.gates.dep` | `check_deps`, `DepsGate`, `dependents_of`, `on_dep_merge_done` |
| `issuesmith.gates.m1` | `RULE_ID`, `VersionBehindBaseGate` |
| `issuesmith.gates.m2` | `check_m2` |
| `issuesmith.gates.pr_scope` | `check_pr_scope`, `PrScopeGate` |
| `issuesmith.gates.review` | `ReviewGate`, `build_review_gate` |
| `issuesmith.gates.scope` | `check_scope`, `ScopeGate` |
| `issuesmith.gates.worktree` | `LintGate`, `TestsGate`, `ExternalLeakGate`, `BaseFreshnessGate`, `WORKTREE_GATES`, `changed_files`, `derive_test_allow_paths`, `derive_ledger_allow_paths`, `check_derived_test_guard`, `line_has_cjk`, `cjk_added_lines`, `external_target_state`, `is_external_target` |
| `issuesmith.m2_gate` | `has_acceptance_criteria_section`, `get_unchecked_count`, `check_gate`, `check_gate_multi_root`, `synthesize_contract_failures`, `main` |
| `issuesmith.repair` | `RequiresResult`, `evaluate_requires`, `apply_auto_fixes`, `record_metrics` |
| `issuesmith.scope_gate` | `ScopeMeasure`, `ScopeVerdict`, `evaluate`, `format_comment`, `measure_scope`, `override_from_metadata`, `parse_allow_paths_from_ctx`, `record_p0_trip_metric`, `resolve_scope_root` |
| `issuesmith.pr_scope` | `DEFAULT_FORBIDDEN_PR_PATHS`, `DIFF_LINES_FALLBACK`, `DerivedAllowPathsError`, `allow_paths_from_issue_body`, `check_pr_diff_scope`, `check_pr_scope_with_derived`, `derived_allow_paths_from_result`, `filenames_from_pr_files`, `find_pr_for_branch`, `normalize_allow_path`, `pr_diff_lines`, `unchecked_ac_count` |
| `issuesmith.ac_contract` | `GateMaterializationError`, `materialize_gate_root`, `cleanup_gate_root`, `dual_gate_roots`, `extract_contract_from_body`, `run_checks`, `contract_failures`, `pending_manual_checks`, `is_invalid_contract_path`, `normalize_reference_entry`, `extract_key_path_values`, `manual_check_description`, `KNOWN_POST_MERGE_KINDS`, `POST_MERGE_REQUIRED_FIELDS` |
| `issuesmith.gate_rules.b1_milestone_subdesign` | `B1MilestoneSubdesignRules`, `extract_sub_blocks`, `infer_sub_dependencies`, `find_dependency_cycles`, `apply_inferred_dependency_fixes`, `apply_sub_plan_dep_format_fixes`, `change_rows_with_content` |

Gate ids usable in `steps.*.requires`: `b1_ac_format`, `b1_migration`, `b1_milestone_subdesign`, `base_freshness`, `cp1`, `deps`, `external_leak`, `lint`, `m2`, `milestone_consistency`, `pin_bump`, `pr_scope`, `scope`, `scope_breadth`, `scope_coupling`, `scope_size`, `tests` (`GATE_REGISTRY`), plus `review` (`STEP_BOUND_GATES`; needs `steps.<id>.review`). A `module.path:attr` reference loads an external gate.

### Contract, projection and labels

| Module | Symbols |
|---|---|
| `issuesmith.contract` | `StepContext`, `StepResult`, `Verdict`, `Andon`, `LabelWriteForbidden`, `CONTRACT_EXTRACTORS`, `get_section`, `parse_table_rows`, `extract_change_table_rows`, `change_paths_for_repo`, `sub_header_re`, `SUB_HEADER_RE` (lazy, delegates to `sub_header_re()`), `iter_sub_blocks`, `sub_block`, `plan_dep_refs`, `PLAN_DEP_REF_RE`, `parse_frontmatter_fields`, `validate_frontmatter` |
| `issuesmith.projection` | `IssueState`, `project`, `diff`, `state_from_labels`, `phase_for_step`, `phase_steps`, `is_final_step`, `is_managed` |
| `issuesmith.ops.labels` | `project_issue` (the only writer that applies label projection to an Issue), `reconcile`, `run_hygiene`, `ExecRecord` |
| `issuesmith.forge_guard` | `ReadOnlyLabelForge`, `guard_step_forge` |
| `issuesmith.preconditions` | `PRECONDITION_REGISTRY`, `register`, `resolve_predicate`, `external_reference_valid`, `PreconditionContext`, `evaluate` |
| `issuesmith.pins` | `requires_pins`, `pin_version`, `installed_version`, `unlanded_pins`, `develop_pins_landed` |
| `issuesmith.body_editor` | `count_heading`, `filter_section_by_paths`, `get_section`, `get_section_by_keyword`, `get_subsections`, `split_h2_sections`, `upsert_section`, `replace_allow_paths`, `normalize_sub_headers`, `relocate_sub_plan`, `apply_milestone_normalizers` |
| `issuesmith.language` | `LanguagePack`, `EN`, `FIELD_NAMES`, `load_language_pack`, `language_pack_from_mapping` |

Built-in phase predicates: `deps_terminal`, `closing_pr_exists` (`issuesmith.preconditions`) and `pins_landed` (registered by `issuesmith.pins`, which the config loader imports).

### Operations

| Module | Symbols |
|---|---|
| `issuesmith.andon` | `Andon`, `AndonSink`, `AutoAnswerPlan`, `to_comment`, `from_comment`, `raise_andon`, `list_open`, `list_open_records`, `answer`, `answer_if_open`, `note`, `plan_auto_answers`, `apply_auto_answers`, `project_issue` |
| `issuesmith.lanes` | `Ledger`, `LanePlan`, `IssueView`, `load_ledger`, `classify`, `plan`, `apply`, `report`, `resolve_report_since`, `count_merge_done_since`, `ledger_state_path`, `load_report_state`, `save_report_state` |
| `issuesmith.merge` | `CompanionReadyResult`, `LocalMergeVerifyResult`, `MergeStateInfo`, `PostMergeTestResult`, `PrSearchResult`, `MergeStateTimeoutError`, `find_pr`, `find_companion_pr`, `list_pulls`, `get_merge_state`, `graphql_merge_state`, `wait_merge_state`, `is_already_merged`, `check_companion_ready`, `verify_local_merge`, `run_post_merge_pytest` |
| `issuesmith.worktree` | `WorktreeError`, `assert_jobs_clean`, `clone_if_missing`, `ensure_base_included`, `fetch_base_or_raise`, `fetch_base_with_retry`, `github_client`, `handle_milestone`, `prepare_cross_repo_worktree`, `prepare_local_worktree`, `prepare_worktree`, `require_yaml_metadata`, `resolve_base_ref`, `validate_branch` |
| `issuesmith.milestone` | `milestone_status`, `milestone_resume`, `milestone_prune`, `milestone_consolidate`, `advance_milestone_chains`, `parse_split_plan`, `plan_section`, `build_child_body`, `prevalidate_child_body`, `validate_children`, `check_v6_dependency_refs`, `run_sub1_create`, `main` and further SUB1 helpers |
| `issuesmith.ops.publish` | `PublishResult`, `publish`, `run_version_bump`, `main` |
| `issuesmith.ops.version_bump` | `BumpDecision`, `decide_bump`, `apply_bump`, `fold_unreleased`, `run_bump`, `main` |
| `issuesmith.metrics_events` | `append_event`, `record_step_started` |
| `issuesmith.metrics_rework` | `compute`, `format_text`, `load_jsonl`, `parse_since`, `resolve_cause_target`, `main` |
| `issuesmith.quota_gate` | `GitHubApiState`, `read_github_api_state`, `is_github_api_low`, `read_github_api_notified`, `write_github_api_notified` |
| `issuesmith.observe` | `observe`, `ObserveSnapshot`, `github_api_status` |

### Verbs

`issuesmith.verbs` re-exports the step verbs.

| Symbol | Module | Notes |
|---|---|---|
| `prepare_worktree` | `issuesmith.verbs.worktree` | Create or verify a worktree branched from a base |
| `publish_branch` | `issuesmith.verbs.publish` | Push a branch (force-with-lease when already published) |
| `find_pr` / `merge_state` / `merge_pr` | `issuesmith.verbs.merge` | Find, inspect and merge a PR |
| `cleanup_worktrees` / `cleanup_branches` / `close_issue` | `issuesmith.verbs.finalize` | Remove worktrees and branches, close the Issue |

### Protocols and extension points

- `issuesmith.gates.base.Gate`: `check(body, labels) -> list` returns violations; `fix(inp: ContractInput) -> ContractInput` may only narrow its input (drop files, run `ruff --fix`, fast-forward a branch) and never widens allow_paths.
- `phases[].advance_when` and `steps.*.requires` accept `module.path:attr` references, so a host can add predicates and gates without changing issuesmith.
- `issuesmith.preconditions.register(name, fn)` adds a named predicate.
- Language packs replace the Issue-body vocabulary and posted text (see [Language packs](#language-packs)).

### Public API Stability

issuesmith is pre-1.0 and follows `0.Y.Z`: a `Y` bump may change or remove public API, a `Z` bump does not. The modules and symbols above are the supported surface; anything else may change in any release.

### Deprecated API

| Item | Replacement |
|---|---|
| `recover` command (emits `FutureWarning`) | `resume <issue> --from <step>` |
| `redispatch` command (emits `FutureWarning`) | `resume <issue> --phase <phase>` |
| `apply` / `ingest-review` commands (exit 2) | The host's `tools/stash/` scripts |
| `issuesmith.yaml` keys `sections`, `sub_design_subsections`, `scope_size.delete_words` / `new_words` / `sub_plan_header` / `no_deps_word` (emit `DeprecationWarning`) | `language_pack` |

## Architecture

ghdag's `WorkflowDispatcher` owns orchestration: polling, DAG construction, label transitions and idempotency. issuesmith provides the gates, steps and tools that the workflow templates call. A phase may advance when its `preconditions` labels are present, its `excludes` labels are absent and every `advance_when` predicate passes. After a step finishes, its `requires` gates run; auto-fixable violations are fixed in place, other blocking violations start a repair run (`steps.*.repair`) or raise an andon (`steps.*.andon_when`). Label writes from steps go through `forge_guard`; managed labels are derived by `projection` and written only by `ops/labels.project_issue`.

| Module | Role |
|---|---|
| `issuesmith/__init__.py` | Package marker; empty `__all__` |
| `issuesmith/__main__.py` | `python3 -m issuesmith`: handles `andon` and `labels`, then calls `cli.main` |
| `issuesmith/ac_contract.py` | Extract and run the acceptance-criteria YAML contract |
| `issuesmith/andon.py` | Andons (typed stop-the-line signals in Issue comments) and auto-answer rules |
| `issuesmith/b1_tier.py` | B1 model tier selection |
| `issuesmith/b1_verify.py` | Deterministic Verify of B1 output and deterministic recovery |
| `issuesmith/body_editor.py` | Issue body editing (ghdag `body_editor` plus milestone normalizers) |
| `issuesmith/branch_reuse.py` | Find and record reusable branches from earlier runs |
| `issuesmith/cli.py` | Unified CLI (`_HANDLERS`) |
| `issuesmith/config.py` | `issuesmith.yaml` loading, validation and defaults |
| `issuesmith/context_hook.py` | ghdag context hook and Issue YAML metadata parsing |
| `issuesmith/contract.py` | Step contract types and Issue-body section parsers |
| `issuesmith/convert_to_milestone.py` | Move an Issue back to the milestone path |
| `issuesmith/cp1_gate.py` | CP1 pattern gate entry point |
| `issuesmith/cp2_tier.py` | CP2 tier from diff size, AC completion and P2 status |
| `issuesmith/dep_extractor.py` | Dependency extraction and checks |
| `issuesmith/engine.py` | LLM role switcher, `run-guarded`, `run-verified`, rate-limit pause and engine wait |
| `issuesmith/forge_api.py` | Typed raw REST calls on forge clients |
| `issuesmith/forge_guard.py` | Label-write guard for step dispatch |
| `issuesmith/gate_rules/__init__.py` | Imports every rule module to fill ghdag's gate registry |
| `issuesmith/gate_rules/b1_ac_format.py` | Acceptance-criteria YAML format rules |
| `issuesmith/gate_rules/b1_migration.py` | Migration plan rules |
| `issuesmith/gate_rules/b1_milestone_subdesign.py` | Milestone sub-design rules and dependency inference |
| `issuesmith/gate_rules/cp1.py` | CP1 design rules |
| `issuesmith/gate_rules/m2.py` | M2 merge rules |
| `issuesmith/gate_rules/milestone_consistency.py` | Split plan without the `scope:milestone` label |
| `issuesmith/gate_rules/pin_bump.py` | Reject dependency pin changes in feature Issues |
| `issuesmith/gate_rules/scope_breadth.py` | allow_paths breadth rules |
| `issuesmith/gate_rules/scope_coupling.py` | Caller / test coupling and deletion reference rules |
| `issuesmith/gate_rules/scope_size.py` | Issue size rules and promotion of oversized Issues to sub plans |
| `issuesmith/gates/__init__.py` | Unified gate API: `GATE_REGISTRY`, `resolve_gate`, `validate_step_requires` |
| `issuesmith/gates/base.py` | `Gate` protocol and `ContractInput` |
| `issuesmith/gates/dep.py` | Dependency gate |
| `issuesmith/gates/m1.py` | M1 version-behind-base gate |
| `issuesmith/gates/m2.py` | M2 gate |
| `issuesmith/gates/pr_scope.py` | PR scope gate |
| `issuesmith/gates/review.py` | `review` gate: runs `steps.<id>.review` through `run-guarded` and maps its problems to violations |
| `issuesmith/gates/scope.py` | Scope gate |
| `issuesmith/gates/worktree.py` | `lint`, `tests`, `external_leak` and `base_freshness` gates |
| `issuesmith/github_api.py` | `gh` command: Issue / PR helpers with label and target guards |
| `issuesmith/lanes.py` | Lane ledger planning: capacity, candidates, enqueue and patrol report |
| `issuesmith/language.py` | Language packs |
| `issuesmith/m2_gate.py` | M2 acceptance-criteria gate entry point |
| `issuesmith/merge.py` | PR merge and verification helpers |
| `issuesmith/metrics_events.py` | Metrics JSONL event writers |
| `issuesmith/metrics_rework.py` | Rework metrics (`metrics rework`) |
| `issuesmith/milestone.py` | Milestone chain and SUB1 split-plan helpers |
| `issuesmith/observe/__init__.py` | Observe layer: `observe()` |
| `issuesmith/observe/dag_state.py` | DAG liveness from `exec.jsonl` and done / running markers |
| `issuesmith/observe/events.py` | Observe event types |
| `issuesmith/observe/main_health.py` | Base-branch health check |
| `issuesmith/observe/policy.py` | Turn events into actions and run them |
| `issuesmith/ops/__init__.py` | Operational commands package |
| `issuesmith/ops/dispatch.py` | `dispatch`: live re-render of shell step bodies |
| `issuesmith/ops/doctor.py` | `requires` chain validation for `steps.*.requires` / `input_kind` |
| `issuesmith/ops/gen_live_dispatch.py` | `gen-live`: shell step trampoline templates |
| `issuesmith/ops/labels.py` | Phase / attention label projection and reconciliation |
| `issuesmith/ops/preflight.py` | `doctor`: runtime environment health check |
| `issuesmith/ops/publish.py` | `publish`: push, version bump and PR |
| `issuesmith/ops/repair_step.py` | Repair step for `requires` violations |
| `issuesmith/ops/smoke.py` | `smoke`: pipeline smoke test on real Issues |
| `issuesmith/ops/version_bump.py` | `version-bump`: `pyproject.toml` bump and CHANGELOG Unreleased fold |
| `issuesmith/pipeline_comments.py` | Pipeline comment filter |
| `issuesmith/pins.py` | `requires_pins` parsing and the `pins_landed` predicate |
| `issuesmith/pr_scope.py` | PR diff scope: allow_paths, `forbidden_pr_paths` and `forbidden_pr_paths_except` |
| `issuesmith/preconditions.py` | Phase advance predicates (`PRECONDITION_REGISTRY`, `evaluate`) |
| `issuesmith/projection.py` | Pure label projection: Issue state to managed labels |
| `issuesmith/queue.py` | `queue` command: enqueue, triage, dispatch, migrate |
| `issuesmith/queue_store.py` | Queue store: JSONL requests, state and lock |
| `issuesmith/queue_triage.py` | Deterministic checks and LLM triage for the queue |
| `issuesmith/quota_gate.py` | GitHub API rate-limit brake |
| `issuesmith/recovery.py` | Deprecated `recover` / `redispatch` |
| `issuesmith/repair.py` | `requires` evaluation and repair orchestration |
| `issuesmith/resume.py` | `resume` entry point |
| `issuesmith/scope_gate.py` | allow_paths scope measurement |
| `issuesmith/targets.py` | Multi-target Issue model |
| `issuesmith/template_ids.py` | `string.Template` identifier extraction (Python 3.10 compatible) |
| `issuesmith/verbs/__init__.py` | Public verb API |
| `issuesmith/verbs/finalize.py` | `cleanup_worktrees`, `cleanup_branches`, `close_issue` |
| `issuesmith/verbs/merge.py` | `find_pr`, `merge_state`, `merge_pr` |
| `issuesmith/verbs/publish.py` | `publish_branch` |
| `issuesmith/verbs/worktree.py` | `prepare_worktree` |
| `issuesmith/worktree.py` | Worktree creation and base-branch fetch |

## Configuration

### Config file resolution

`get_config()` loads the configuration once per process (`reset_config_cache()` clears it). `load_config(path)` looks for the file in this order:

1. the `path` argument
2. `ISSUESMITH_CONFIG`
3. `issuesmith.yaml` in the current directory or a parent
4. `issuesmith.yaml` at the root of the issuesmith source checkout

A file that is not found is treated as empty, which fails validation because `repo` and `phases` are required. Relative paths in the file resolve against the directory that holds it.

### Environment variables

| Name | Default | Description |
|---|---|---|
| `ISSUESMITH_CONFIG` | (unset) | Path to `issuesmith.yaml` |
| `ISSUESMITH_QUEUE_DIR` | (unset) | Directory for the queue, queue state, lock and triage log (overrides the `paths` defaults) |
| `ISSUESMITH_ENGINE_WAIT_POLL_SEC` | `60` | Re-check period while every engine is paused; values outside 1-60 give 60 |
| `ISSUESMITH_ENGINE_WAIT_INTERVAL_SEC` | `60` | Legacy name, read only when `ISSUESMITH_ENGINE_WAIT_POLL_SEC` is unset |
| `ISSUESMITH_ENGINE_WAIT_MAX_SEC` | `21600` | Longest wait for an engine to leave pause |
| `ISSUESMITH_TIMEOUT_SEC` | (unset) | LLM run timeout used when the engine state has no `timeout_sec` for the role; falls back to `engines.<role>.timeout_sec` |
| `ISSUESMITH_PYTEST_TIMEOUT_SEC` | `1500` | Timeout of one pytest run in the `tests` gate |
| `ISSUESMITH_REPAIR_ACTIVE` | (unset) | Set internally during a repair run to stop nested repairs |
| `METRICS_JSONL_PATH` | `paths.metrics` | Engine metrics JSONL path |
| `P0_FETCH_LOCK_WAIT` | `30` | Seconds to wait for the P0 base-fetch lock |
| `GHDAG_TASK_UUID` | (set by ghdag) | Current DAG task UUID, recorded as `parent_uuid` in metrics and dispatch |
| `AGENT_SKILLS_DIR` | `~/.agents/skills` | Skills directory checked by `doctor` |

### `issuesmith.yaml` keys

| Key | Default | Description |
|---|---|---|
| `repo` | (required) | Host repository `owner/name` |
| `phases` | (required) | Phase list (see below); there are no built-in phases |
| `label_namespace` | `issuesmith` | Managed label prefix |
| `timezone` | `Asia/Tokyo` | IANA timezone for reports and `--since` |
| `supported_repos` | `[]` | Repositories allowed as cross-repo targets |
| `paths` | see below | Queue, state, metrics, worktree, template and ledger paths |
| `engines.<role>` | see below | Per role (`design`, `implementation`): `allowed`, `default_model`, `light_model`, `timeout_sec`, `pause_ttl_sec` |
| `concurrency` | `default: 1` | `default`, `per_engine`, `strict_order` (`false`), `max_dispatch_per_tick` (`1`: requests started per queue tick; each engine still stops at its `per_engine` limit) |
| `milestone_chain` | `enabled: false` | `enabled`, `child_priority` (`normal`), `auto_develop` (`true`), `auto_close_parent` (`true`) |
| `triage` | `enabled: true` | Queue LLM triage: `engine` (`claude`), `model` (`claude-sonnet-4-6`), `timeout` (`60`), `body_chars` (`500`), `circuit_breaker_threshold` (`3`), `circuit_breaker_reset_seconds` (`1800`) |
| `steps.<id>` | `p1` with `andon_when` | Per-step settings (see below) |
| `label_write_guard` | `warn` | `warn` or `enforce`: warn about or block label writes from steps |
| `forbidden_pr_paths` | `jobs/**`, `logs/**`, `.sessions/**`, `*.jsonl`, `*.pid`, `*.lock` | Paths a PR may not touch |
| `forbidden_pr_paths_except` | `[]` | Glob patterns exempt from `forbidden_pr_paths` (they must still match allow_paths); `tests/**/fixtures/**` is always exempt |
| `terminal_labels` | `[]` | Labels that mark a finished Issue |
| `terminal_without_merge` | `[]` | Labels that mark an Issue finished without a merge (names without `:` get the `label_namespace` prefix) |
| `scope_gate` | `enabled: true` | P0 allow_paths size: `max_files` (`80`), `max_lines` (`20000`), `hard_max_files` (`200`) |
| `scope_coupling` | `enabled: true` | `search_dirs` (`[tests, src]`), `data_file_tests` (`true`) |
| `scope_size` | `enabled: true` | B1 Issue size: `max_files` (`8`), `max_concerns` (`4`), `max_slices` (`3`), `delete_with_new` (`false`), `exclude_prefixes` |
| `tests` | `flaky_reruns: 2` | Reruns of newly failing tests (`0` turns flaky detection off) |
| `metrics` | `done_step: m2` | Rework metrics: `done_step`, `repair_templates`, `cause_targets` |
| `derived_allow` | `enabled: true`, `ledger_globs: [tests/conventions/known_*.txt]` | Let repair edit newly failing tests outside allow_paths; ledgers matching `ledger_globs` may only shrink (`[]` turns ledger derivation off) |
| `external_leak` | `cjk_free_external_targets: false` | Fail added CJK lines on branches whose `target_repo` is not `repo` |
| `observe` | see below | Observe thresholds and `main_health` |
| `api_brake` | `enabled: false` | GitHub API brake: `min_remaining` (`800`) |
| `andon.auto_answer` | `[]` | Auto-answer rules: `name` (unique), `match` (non-empty; keys `step`, `kind`, `rule_id`), `action`, `max_per_issue` (`1`) |
| `language_pack` | (unset: `EN`) | Path to a language pack YAML (see [Language packs](#language-packs)) |
| `installs` | `{}` | Package name to install directory, used by `pins_landed` |
| `sections` / `sub_design_subsections` | (deprecated) | Use `language_pack` |

`tests`, `metrics`, `derived_allow`, `external_leak`, `steps.*.repair` and `steps.*.review` reject unknown keys. `scope_coupling.ignore_symbols` is no longer supported and raises `ConfigError`.

#### `engines.<role>` fields

| Field | Default (`design` / `implementation`) | Description |
|---|---|---|
| `allowed` | `[claude, codex]` / `[claude, cursor]` | Engines the role may switch to |
| `default_model` | `claude: claude-opus-4-6`, `codex: gpt-5.6-sol` / `claude: claude-sonnet-4-6`, `cursor: auto` | Model per engine for the heavy tier |
| `light_model` | `claude: claude-sonnet-4-6`, `codex: gpt-5.5` / `{}` | Model per engine for the light tier |
| `timeout_sec` | `1800` / `3600` | LLM run timeout |
| `pause_ttl_sec` | `3600` | How long an engine stays paused after a rate limit (`resume_at = observed_at + pause_ttl_sec`); must be a positive integer |

#### `phases[]` fields

| Field | Default | Description |
|---|---|---|
| `name` | (required) | Phase name; labels are `<namespace>:<name>-ready` / `-running` / `-done` |
| `role` | (required) | `design` or `implementation` (at most one design phase) |
| `entry_step` | (required) | Step id that starts the phase |
| `handler` | (required) | Workflow handler name |
| `preconditions` | `[]` | Labels required before the phase can start (names without `:` get the namespace prefix) |
| `excludes` | `[]` | Labels that block the phase |
| `writes_files` | `true` | Whether the phase writes files |
| `advance_when` | `[]` | Predicates from `PRECONDITION_REGISTRY` or `module.path:attr` references |
| `steps` | `[]` | Step ids in run order; empty means `[entry_step]`. A step may belong to one phase only |

#### `steps.<id>` fields

| Field | Default | Description |
|---|---|---|
| `module` | `""` | Python module implementing the step; empty means an LLM step run through `engine run-guarded` |
| `template` | (unset) | Template path for LLM steps |
| `requires` | `[]` | Gate ids (or `module.path:attr`) evaluated after the step |
| `input_kind` | `issue` | `issue`, `worktree` or `artifact` |
| `accepts` | `[]` | Accepted artifact kinds |
| `andon_when` | `[]` (`p1`: `external_leak.target_unknown`) | Violation ids that raise an andon instead of a repair |
| `repair.max` | `3` | Repair attempts for blocking violations (`0` disables repair) |
| `repair.push` | `false` | Push the branch after a repair |
| `review` | (unset) | Required when `requires` or `accepts` lists `review`: `role`, `template`, `success_status`, `failure_status` (required), `tier`, `problems_heading` (`Problems:`) |

#### `paths` keys

Relative to the config file directory: `queue` `jobs/issuesmith-queue.jsonl`, `queue_state` `logs/issuesmith-queue-state.json`, `queue_lock` `logs/issuesmith-queue.lock`, `triage_log` `jobs/issuesmith-triage.jsonl`, `seed` `configs/night-queue.yaml`, `night_state` `logs/night-queue-state.json`, `exec_jsonl` `jobs/exec.jsonl`, `done_dir` `jobs/done`, `quota_state` `jobs/quota-gate.json`, `metrics` `jobs/metrics.jsonl`, `worktrees_dir` `.claude/worktrees`, `external_dir` `.claude/external`, `workflow` `workflows/issuesmith.yml`, `template_dir` `workflows/issuesmith`, `engine_state` `.pipeline-state/issuesmith-engine.yml`, `brake_state` (defaults to `quota_state`), `lanes` (unset; the ledger read by the `lanes` command).

#### `observe` keys

`stall_minutes` `120`, `task_timeout_minutes` `90`, `systemic_min_issues` `2`, `systemic_window_minutes` `60`, `forge_max_consecutive_errors` `3`, `max_api_calls` `8`. `observe.main_health` is off when unset; it takes `worktree` and `command` (both required), `base_branch` (`main`) and `timeout_seconds` (`1800`).

### Language packs

A language pack holds the words issuesmith uses to read Issue bodies and the text it posts to GitHub (Issue comments, andon summaries, PR titles). The package ships one pack, `issuesmith.language.EN`, which is used when `language_pack` is not set. A host that writes Issues in another language sets:

```yaml
language_pack: configs/issuesmith-lang.xx.yaml
```

The pack file is a YAML mapping keyed by `LanguagePack` field names. Fields the pack omits, and keys it omits from `sections` and `messages`, fall back to `EN`. Unknown keys, wrong types and wrong list lengths raise `ConfigError`. The resolved pack is `get_config().language`.

| Field | Type | `EN` value |
|---|---|---|
| `sections` | mapping | H2 headings keyed `acceptance_criteria`, `migration`, `migration_state_survey`, `sub_plan`, `design`, `background`, `dependencies`, `impact_survey`, `milestone`, `changed_files` |
| `sub_design_subsections` | list of 4 | `Scope`, `Design Policy`, `Changed Files`, `Acceptance Criteria` |
| `sub_header_prefix` | string | `Sub` (header `#### Sub<N>:`) |
| `sub_plan_columns` | list of 5 | `#`, `Title`, `Target repo`, `Content`, `Depends on` |
| `change_table_columns` | list of 4 | `Repository`, `File path`, `Change type`, `Description` |
| `no_deps_word` | string | `none` |
| `delete_words` | list | `delete` |
| `new_words` | list | `new`, `add` |
| `removal_words` | list | `delete`, `remove`, `replace`, `substitute`, `deprecate` |
| `placeholder_words` | list | `TBD`, `TODO`, `FIXME`, `XXX`, `placeholder` |
| `vague_ac_words` | list | `works correctly`, `properly`, `without problems`, `as needed` |
| `derived_from_phrase` | string | `derived` |
| `parent_issue_label` | string | `Parent issue` |
| `dependencies_table_header` | string | `\| # \| Dependency \| State \|` |
| `out_of_scope_heading` | string | `Out of Scope` |
| `order_after_words` | list | `after`, `once`, `depends on` |
| `messages` | mapping | GitHub-posted text keyed `<module>.<id>` (`str.format` templates); copy the keys from `EN.messages` |

Logs, exception messages, CLI help and violation text stay English and are not part of a pack. Without `language_pack`, the deprecated `sections`, `sub_design_subsections` and `scope_size` vocabulary keys still override the matching `EN` fields; with `language_pack` set they are ignored. Both cases emit a `DeprecationWarning`.

## Error Reference

| Type | Module | Base | Raised when |
|---|---|---|---|
| `ConfigError` | `issuesmith.config` | `ValueError` | Invalid `issuesmith.yaml` section, language pack, gate or predicate reference; missing `phases:` or `phases[].handler`; a step in two phases; `review` gate without `steps.<id>.review` |
| `QueueValidationError` | `issuesmith.queue_store` | `ValueError` | Invalid queue request input (CLI exits 2) |
| `GateBuildError` | `issuesmith.gates` | `ValueError` | A gate cannot be built from its `GateBuildContext` |
| `DerivedAllowPathsError` | `issuesmith.pr_scope` | `ValueError` | `derived_allow_paths_from_result` finds malformed entries |
| `MainHealthError` | `issuesmith.observe.main_health` | `RuntimeError` | The base-branch health check could not run (git, worktree, timeout); state is not updated |
| `GateMaterializationError` | `issuesmith.ac_contract` | `RuntimeError` | The temporary `origin/<base>` worktree for gate checks could not be created |
| `RetrySignal` | `issuesmith.engine` | `RuntimeError` | A step is deferred for retry (engine paused or rate-limited); not a failure |
| `LabelWriteForbidden` | `issuesmith.contract` | `RuntimeError` | A step wrote labels while `label_write_guard` is `enforce` |
| `WorktreeError` | `issuesmith.worktree` | `Exception` | Worktree preparation failed |
| `MergeStateTimeoutError` | `issuesmith.merge` | `Exception` | The PR merge state did not settle before the timeout |

Some shape errors raise plain `ValueError` instead of `ConfigError`: a missing `repo`, a non-mapping config file, an empty or malformed `phases` list, a non-positive or non-integer `engines.<role>.pause_ttl_sec`, a non-list `forbidden_pr_paths`, `forbidden_pr_paths_except`, `terminal_labels` or `terminal_without_merge`, and an incomplete `observe.main_health`. `TemplateVariableError` (`ghdag.pipeline.order`) can propagate from `issuesmith.engine` when a template variable is missing.

## License

MIT (SPDX `MIT`). See [LICENSE](./LICENSE).
