# issuesmith

issuesmith is a GitHub Issue label-driven workflow toolkit built on [ghdag](https://github.com/sumipan/ghdag). ghdag runs the DAGs, polls labels and moves Issues between them. issuesmith adds the Issue-side parts: gates, the request queue and its triage, context hooks, LLM role switching, label projection, and one CLI that workflow templates call.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-v0.135.0-blue)
![ci](https://github.com/sumipan/issuesmith/actions/workflows/ci.yml/badge.svg?branch=main)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

The current release is **v0.135.0** (pre-1.0). Public interfaces may change before `1.0.0`; see [CHANGELOG.md](./CHANGELOG.md) for breaking changes.

## Installation

```bash
pip install "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.135.0"
```

Most runtime paths (gates, forge access, `observe`, `andon`, `metrics`) import ghdag. Install it with the `ghdag` extra:

```bash
pip install "issuesmith[ghdag] @ git+https://github.com/sumipan/issuesmith.git@v0.135.0"
```

| Item | Value |
|---|---|
| Python | `>=3.10` (classifiers: 3.10, 3.11, 3.12) |
| Runtime dependencies | `pyyaml`, `ruamel.yaml`, `packaging`, `python-dotenv` |
| `[ghdag]` extra | `ghdag @ git+https://github.com/sumipan/ghdag.git@v0.72.0` |
| `[dev]` extra | `pytest`, `pytest-cov`, `mypy`, `ruff`, and ghdag `v0.72.0` |
| Console script | `issuesmith` (`issuesmith.cli:main`) |

## Quick Start

1. Put an `issuesmith.yaml` at the root of the host repository. Only `repo` is required; every other key has a default (see [Configuration](#configuration)). The engine models below are the built-in defaults:

```yaml
repo: owner/my-repo
label_namespace: issuesmith
timezone: Asia/Tokyo
supported_repos:
  - owner/my-repo
paths:
  queue: jobs/issuesmith-queue.jsonl
  workflow: workflows/issuesmith.yml
  template_dir: workflows/issuesmith
engines:
  design:
    allowed: [claude, codex]
    default_model:
      claude: claude-opus-4-6
      codex: gpt-5.6-sol
    light_model:
      claude: claude-sonnet-4-6
      codex: gpt-5.5
    timeout_sec: 1800
  implementation:
    allowed: [claude, cursor]
    default_model:
      claude: claude-sonnet-4-6
      cursor: auto
    timeout_sec: 3600
```

2. Check the environment and the resolved configuration:

```bash
export ISSUESMITH_CONFIG=/path/to/issuesmith.yaml   # optional when the yaml is in cwd or a parent
python3 -m issuesmith doctor
python3 -m issuesmith config show
python3 -m issuesmith engine show
```

3. Run a gate against an Issue body, queue an Issue, and resume a stopped run:

```bash
python3 -m issuesmith gate cp1 --body-file body.md
python3 -m issuesmith queue enqueue --issue 123 --phase draft --source cli \
  --actor-kind human --priority normal --requested-by me
python3 -m issuesmith queue tick
python3 -m issuesmith resume 123 --from p1
```

Other common entry points: `python3 -m issuesmith andon list`, `python3 -m issuesmith observe --json` and `python3 -m issuesmith metrics rework --since 2026-10-01`.

## CLI Reference

Run commands as `python3 -m issuesmith <command>` or with the `issuesmith` console script. Top-level commands are the keys of `issuesmith.cli._HANDLERS`. `issuesmith.__main__` handles `andon` before `_HANDLERS` (so `andon` works only through `python3 -m issuesmith`); `labels` is registered in both places.

| Command | Description |
|---|---|
| `context` / `context_hook` / `context-hook` | ghdag context hook: Issue YAML metadata and step context |
| `gate <name> ...` | Run a gate; `gate <name> --body-file F` is `gate-preflight --gate <name>`; `gate cp1 <issue>` / `gate m2 <issue>` run the CP1 / M2 entry points |
| `gate-preflight` | ghdag gate runner (`ghdag.workflow.gates`) with issuesmith gate rules loaded |
| `cp1-gate` / `m2-gate` | CP1 pattern gate / M2 acceptance-criteria gate |
| `verify b1 ...` / `b1-verify` | Deterministic Verify of B1 output (`--prev-report FILE` adds oscillation detection) |
| `queue <subcommand>` | Request queue (see below) |
| `deps` | Extract and verify Issue dependencies |
| `tier b1` / `tier cp2` | Choose the light or heavy model tier for B1 / CP2 |
| `comments` | Pipeline comment filter |
| `gh` | Issue / PR helpers through the ghdag forge client (not the `gh` CLI) |
| `engine <subcommand>` | LLM role switcher and runner (see below) |
| `dispatch` | Live re-render runner for shell step bodies |
| `publish` | Publish a branch (push, PR, version bump) |
| `labels reconcile [--fix] [--json]` | Report or fix managed-label divergences on open Issues |
| `doctor` | Runtime environment health check |
| `smoke` | Smoke test of the pipeline templates against real Issues |
| `gen-live` | Generate shell step trampoline templates |
| `version-bump` | Deterministic `pyproject.toml` version bump |
| `resume <issue> --from <step>` / `--phase <phase>` | Resume a workflow (`--workflow`, `--handler`, `--mark-done STEP`, `--force` with `--from`) |
| `recover` / `redispatch` | Deprecated; see [Deprecated API](#deprecated-api) |
| `convert-to-milestone` | Move an Issue that entered the develop path back to the milestone path |
| `milestone <subcommand>` | Milestone chain (see below) |
| `config show` | Print the resolved configuration as JSON and validate `steps.*.requires` |
| `observe [--apply] [--json]` | Collect observe events; `--apply` runs the policy actions |
| `metrics rework` | Rework metrics report (see below) |
| `main-health` | Run `observe.main_health.command` on the base branch and write the state file |
| `apply` / `ingest-review` | Moved to the host's `tools/stash/`; exits 2 |

### `andon` subcommands

| Subcommand | Description |
|---|---|
| `andon list [--all] [--json]` | List open (unanswered) andons |
| `andon show <andon-id>` | Show one andon |
| `andon answer <andon-id> <action>` | Post the answer, remove the label, call the resume hook |
| `andon note <andon-id> --key <key> --value <value>` | Record a note on an open andon (no label change) |

### `engine` subcommands

| Subcommand | Description |
|---|---|
| `engine show` | Show the effective role assignments |
| `engine check` | Validate commands, models and workflow drift |
| `engine switch <role> <engine> [--model M] [--light-model M]` | Switch one role (`design` or `implementation`) |
| `engine run-guarded <role> <template> --failure-status S [--cwd] [--tier] [--success S] [--emit-status S] [--requires-step ID] [vars...]` | Run an LLM template and evaluate the step's `requires` gates |
| `engine run-verified <role> <template> --failure-status S --verify V --recover R [--max-loops N] [--recover-tier T] [--skip-verify-on S] ...` | Run with a verify / recover loop |
| `engine run` / `engine exec` / `engine resolve` | Internal dispatch helpers (hidden from `--help`) |

`--tier` takes `light` or `heavy`. `run-guarded` is not a top-level command: a step without a `module` runs as `engine run-guarded --requires-step <step_id>`.

### `queue` subcommands

Global options: `--queue-path`, `--state-path`, `--lock-path`.

| Subcommand | Description |
|---|---|
| `queue enqueue --issue N --phase P --source S --actor-kind {human,automation} --priority {high,normal,low} --requested-by X [--force] [--after N]` | Queue an Issue for a configured phase |
| `queue tick [--seed PATH]` | Triage and dispatch queued requests |
| `queue status` | Show the queue state |
| `queue doctor` | Check for untracked in-flight Issues |
| `queue reset [--keep-last-issue]` | Clear the halt state |
| `queue skip --issue N [--reason R]` | Skip an Issue |
| `queue dequeue --request-id ID [--reason R]` | Remove a request |
| `queue audit [--offline]` | Check queue integrity (`--offline` skips GitHub checks) |
| `queue triage-log [--last N] [--path P]` | Show recent triage log entries |
| `queue migrate [--from-night-queue-state P] [--seed P] [--dry-run]` | Migrate from the legacy night-queue state |
| `queue release --issue N` | Release a design-slot in-flight entry (recovery only) |

### `milestone` subcommands

| Subcommand | Description |
|---|---|
| `milestone status <parent>` | Show the milestone chain status |
| `milestone resume <parent>` | Resume milestone chain progress |
| `milestone prune [--dry-run]` | Prune finished milestone chain state |
| `milestone consolidate <child> --into <sibling>` | Mark a child as consolidated into a sibling (marker comment, `rejected`, close) so C4 treats it as merged when the sibling merged |

### `metrics` subcommands

| Subcommand | Description |
|---|---|
| `metrics rework [--since YYYY-MM-DD] [--issue N] [--audit PATH] [--json]` | Aggregate rework from `paths.metrics` and an optional ghdag audit JSONL (`--since` uses `timezone`) |

## Public API

`issuesmith.__all__` is empty: import submodules directly. The tables list each module's `__all__`; modules without `__all__` list their public definitions below.

### `issuesmith.language`

| Symbol | Notes |
|---|---|
| `LanguagePack` | Frozen dataclass of Issue-body vocabulary and GitHub-posted text |
| `EN` | Built-in English pack (the only pack shipped) |
| `FIELD_NAMES` | Field names of `LanguagePack` |
| `load_language_pack` | Load a pack YAML file |
| `language_pack_from_mapping` | Build a `LanguagePack` from a parsed mapping |

### `issuesmith.contract`

| Symbol | Notes |
|---|---|
| `StepContext` / `StepResult` / `Verdict` / `Andon` | Step contract types |
| `LabelWriteForbidden` | Raised when a step writes labels while `label_write_guard` is `enforce` |
| `CONTRACT_EXTRACTORS` | Contract extractor table |
| `get_section` | Body of an H2 section |
| `parse_table_rows` | Markdown table rows |
| `extract_change_table_rows` / `change_paths_for_repo` | Change-table rows and paths |
| `sub_header_re` | Sub-design header regex built from the configured pack |
| `SUB_HEADER_RE` | Lazy object that delegates to `sub_header_re()` |
| `iter_sub_blocks` / `sub_block` | Sub-design blocks of a body |
| `parse_frontmatter_fields` / `validate_frontmatter` | Leading YAML block fields and their validation |

### `issuesmith.preconditions`

| Symbol | Notes |
|---|---|
| `PRECONDITION_REGISTRY` | Registry of phase advance predicates |
| `register` | Register a predicate by name |
| `PreconditionContext` | Context passed to predicates (`issue`, `labels`, `client`, `issue_number`) |
| `evaluate` | Evaluate whether a phase may advance |

Built-in predicates: `deps_terminal`, `closing_pr_exists`.

### `issuesmith.projection`

| Symbol | Notes |
|---|---|
| `IssueState` | Pure state: phase statuses, andon kinds, queued / waiting flags |
| `project` | Desired managed labels for an `IssueState` |
| `diff` | Sorted `(add, remove)` over managed labels |
| `state_from_labels` | Parse current labels into `IssueState` |
| `phase_for_step` | Phase name owning a step id |
| `is_final_step` | Whether a step is the last in its phase |
| `is_managed` | Whether a label belongs to the managed vocabulary |
| `phase_steps` | Steps of a phase in run order |

### `issuesmith.forge_guard`

| Symbol | Notes |
|---|---|
| `ReadOnlyLabelForge` | `ForgePort` wrapper that blocks or warns on label writes |
| `guard_step_forge` | Context manager patching `get_forge` during step dispatch |

### `issuesmith.body_editor`

| Symbol | Notes |
|---|---|
| `count_heading` | Count markdown headings |
| `filter_section_by_paths` | Filter a section by allow_paths |
| `get_section` / `get_section_by_keyword` / `get_subsections` | Read sections |
| `split_h2_sections` | Split a body into H2 sections |
| `upsert_section` | Insert or replace an H2 section |
| `replace_allow_paths` | Replace the allow_paths block |
| `normalize_sub_headers` / `relocate_sub_plan` / `apply_milestone_normalizers` | Milestone body normalizers |

### `issuesmith.ac_contract`

| Symbol | Notes |
|---|---|
| `GateMaterializationError` | Temporary worktree for gate checks could not be created |
| `materialize_gate_root` / `cleanup_gate_root` / `dual_gate_roots` | Gate worktree helpers |
| `is_invalid_contract_path` | Whether an acceptance-criteria path entry is invalid |
| `extract_contract_from_body` / `run_checks` | Acceptance-criteria YAML contract |
| `contract_failures` / `pending_manual_checks` | Contract check helpers |

### `issuesmith.gates`

| Symbol | Notes |
|---|---|
| `Verdict` | Gate result |
| `GateBuildContext` / `GateEntry` / `GateBuildError` | Gate instantiation |
| `GATE_REGISTRY` | Unified gate registry |
| `RequiresGate` | Gate used in `steps.*.requires` |
| `validate_step_requires` | Validate `steps.*.requires` |
| `check_scope` / `check_pr_scope` / `check_m2` / `check_deps` | Run one gate |

### `issuesmith.gates.*`

| Module | `__all__` |
|---|---|
| `issuesmith.gates.base` | `ContractInput`, `Gate` |
| `issuesmith.gates.dep` | `check_deps`, `DepsGate`, `dependents_of`, `on_dep_merge_done` |
| `issuesmith.gates.m1` | `RULE_ID`, `VersionBehindBaseGate` |
| `issuesmith.gates.m2` | `check_m2` |
| `issuesmith.gates.pr_scope` | `check_pr_scope`, `PrScopeGate` |
| `issuesmith.gates.scope` | `check_scope`, `ScopeGate` |
| `issuesmith.gates.worktree` | `LintGate`, `TestsGate`, `ExternalLeakGate`, `BaseFreshnessGate`, `WORKTREE_GATES`, `changed_files`, `derive_test_allow_paths`, `derive_ledger_allow_paths`, `check_derived_test_guard`, `line_has_cjk`, `cjk_added_lines`, `external_target_state`, `is_external_target` (plus internal `_run_pytest`, `_parse_failed_ids`, `_func_id`, `_baseline_failed_func_ids`) |

### `issuesmith.gate_rules.b1_milestone_subdesign`

| Symbol | Notes |
|---|---|
| `apply_inferred_dependency_fixes` | Infer and apply dependency ordering fixes to a B1 body |

### `issuesmith.m2_gate`

`has_acceptance_criteria_section`, `get_unchecked_count`, `check_gate`, `check_gate_multi_root`, `synthesize_contract_failures`, `main`.

### `issuesmith.merge`

| Symbol | Notes |
|---|---|
| `CompanionReadyResult` / `LocalMergeVerifyResult` / `MergeStateInfo` / `PostMergeTestResult` / `PrSearchResult` | Result types |
| `MergeStateTimeoutError` | Merge state polling timed out |
| `find_pr` / `find_companion_pr` / `list_pulls` | Find PRs |
| `get_merge_state` / `graphql_merge_state` / `wait_merge_state` / `is_already_merged` | Read merge state |
| `check_companion_ready` | Companion PR readiness |
| `verify_local_merge` / `run_post_merge_pytest` | Post-merge checks |

### `issuesmith.metrics_events`

| Symbol | Notes |
|---|---|
| `append_event` | Append one metrics JSONL line with `ts` and optional `parent_uuid` |
| `record_step_started` | Write `step_started` when `exec.jsonl` has a matching `GHDAG_TASK_UUID` row |

### `issuesmith.metrics_rework`

| Symbol | Notes |
|---|---|
| `compute` | Aggregate rework from the metrics file and an optional audit file |
| `format_text` | Text report |
| `load_jsonl` | Read a JSONL file |
| `parse_since` | Parse `--since` in the configured timezone |
| `resolve_cause_target` | Map a cause to a target (`metrics.cause_targets`) |
| `main` | `metrics rework` entry point |

### `issuesmith.pr_scope`

| Symbol | Notes |
|---|---|
| `DEFAULT_FORBIDDEN_PR_PATHS` / `DIFF_LINES_FALLBACK` | Constants |
| `DerivedAllowPathsError` | Malformed derived allow_paths |
| `allow_paths_from_issue_body` | allow_paths of an Issue body |
| `check_pr_diff_scope` / `check_pr_scope_with_derived` | PR diff scope checks |
| `derived_allow_paths_from_result` | Derived allow_paths from a step result |
| `normalize_allow_path` | Normalize one allow_paths entry |
| `filenames_from_pr_files` / `find_pr_for_branch` / `pr_diff_lines` / `unchecked_ac_count` | Helpers |

### `issuesmith.repair`

`RequiresResult`, `evaluate_requires`, `apply_auto_fixes`, `record_metrics`. Violations whose severity is not `fail` go to `RequiresResult.warnings` and do not start a repair.

### `issuesmith.scope_gate`

`ScopeMeasure`, `ScopeVerdict`, `evaluate`, `format_comment`, `measure_scope`, `override_from_metadata`, `parse_allow_paths_from_ctx`, `record_p0_trip_metric`, `resolve_scope_root`.

### `issuesmith.worktree`

`WorktreeError`, `assert_jobs_clean`, `clone_if_missing`, `ensure_base_included`, `fetch_base_or_raise`, `fetch_base_with_retry`, `github_client`, `handle_milestone`, `prepare_cross_repo_worktree`, `prepare_local_worktree`, `prepare_worktree`, `require_yaml_metadata`, `resolve_base_ref`, `validate_branch`.

### `issuesmith.andon`

`Andon`, `to_comment`, `from_comment`, `list_open`, `list_open_records`, `raise_andon`, `answer`, `answer_if_open`, `note`, `project_issue`.

### `issuesmith.ops.labels`

`project_issue` — the only writer that applies label projection to an Issue.

### `issuesmith.milestone`

`milestone_status`, `milestone_resume`, `milestone_prune`, `milestone_consolidate`, `parse_split_plan`, `build_child_body`, `validate_children`, `plan_section`, and other SUB1 / chain helpers.

### `issuesmith.ops.publish`

`PublishResult`, `publish`, `run_version_bump`, `main` (plus internal `_bump_versions_in_range`, `_run_version_bump`).

### `issuesmith.verbs`

| Symbol | Module | Notes |
|---|---|---|
| `prepare_worktree` | `issuesmith.verbs.worktree` | Create or verify a worktree branched from a base |
| `publish_branch` | `issuesmith.verbs.publish` | Push a branch (force-with-lease when already published) |
| `find_pr` / `merge_state` / `merge_pr` | `issuesmith.verbs.merge` | Find, inspect and merge a PR |
| `cleanup_worktrees` / `cleanup_branches` / `close_issue` | `issuesmith.verbs.finalize` | Remove worktrees and branches, close the Issue |

### Protocols

`issuesmith.gates.base.Gate` is the gate protocol: `check(body, labels) -> list` returns violations, and `fix(inp: ContractInput) -> ContractInput` may only narrow the input (remove files, apply `ruff --fix`, fast-forward a branch), never widen allow_paths.

### Public API Stability

issuesmith is pre-1.0. The modules and `__all__` lists above are the supported surface; anything else, and names that start with `_`, may change in any release.

### Deprecated API

| Item | Replacement |
|---|---|
| `recover` command (emits `FutureWarning`) | `resume <issue> --from <step>` |
| `redispatch` command (emits `FutureWarning`) | `resume <issue> --phase <phase>` |
| `apply` / `ingest-review` commands (exit 2) | The host's `tools/stash/` scripts |
| `issuesmith.yaml` keys `sections`, `sub_design_subsections`, `scope_size.delete_words` / `new_words` / `sub_plan_header` / `no_deps_word` | `language_pack` (see [Language packs](#language-packs)) |

## Architecture

ghdag's `WorkflowDispatcher` owns orchestration: polling, DAG construction, label transitions and idempotency. issuesmith provides the Issue-domain gates, steps and tools that the workflow templates call. Label writes during step dispatch are guarded by `forge_guard`; the runner projects labels through `projection` and `ops/labels.project_issue`.

| Module | Role |
|---|---|
| `issuesmith/__init__.py` | Package marker; empty `__all__` |
| `issuesmith/__main__.py` | `python3 -m issuesmith`: handles `andon` / `labels`, then `cli.main` |
| `issuesmith/ac_contract.py` | Extract and run the acceptance-criteria YAML contract |
| `issuesmith/andon.py` | Andons: typed stop-the-line signals posted as Issue comments |
| `issuesmith/b1_tier.py` | B1 model tier selection |
| `issuesmith/b1_verify.py` | Deterministic Verify of B1 output and deterministic recovery |
| `issuesmith/body_editor.py` | Issue body edit helpers (ghdag `body_editor` plus extensions) |
| `issuesmith/branch_reuse.py` | Find and record reusable branches from earlier runs |
| `issuesmith/cli.py` | Unified CLI (`_HANDLERS`) |
| `issuesmith/config.py` | `issuesmith.yaml` resolution and defaults |
| `issuesmith/context_hook.py` | ghdag context hook and Issue YAML metadata parsing |
| `issuesmith/contract.py` | Contract types and Issue-body section parsers |
| `issuesmith/convert_to_milestone.py` | Move an Issue back to the milestone path |
| `issuesmith/cp1_gate.py` | CP1 pattern gate entry point |
| `issuesmith/cp2_tier.py` | CP2 tier from diff size, AC completion and P2 status |
| `issuesmith/dep_extractor.py` | Dependency extraction and verification |
| `issuesmith/engine.py` | LLM role switcher, `run-guarded`, `run-verified`, engine wait |
| `issuesmith/forge_api.py` | Typed raw REST calls on forge clients |
| `issuesmith/forge_guard.py` | `ReadOnlyLabelForge` / `guard_step_forge` label-write guard |
| `issuesmith/gate_rules/__init__.py` | Imports every rule module to fill ghdag's `GATE_REGISTRY` |
| `issuesmith/gate_rules/b1_ac_format.py` | Acceptance-criteria YAML format rules |
| `issuesmith/gate_rules/b1_migration.py` | Migration plan rules |
| `issuesmith/gate_rules/b1_milestone_subdesign.py` | Milestone sub-design rules |
| `issuesmith/gate_rules/cp1.py` | CP1 design rules |
| `issuesmith/gate_rules/m2.py` | M2 merge rules |
| `issuesmith/gate_rules/milestone_consistency.py` | Split plan without the `scope:milestone` label |
| `issuesmith/gate_rules/scope_breadth.py` | allow_paths breadth rules |
| `issuesmith/gate_rules/scope_coupling.py` | Caller / test coupling and deletion reference rules |
| `issuesmith/gate_rules/scope_size.py` | Issue size rules |
| `issuesmith/gates/__init__.py` | Unified gate API: `GATE_REGISTRY`, `Verdict` |
| `issuesmith/gates/base.py` | `Gate` protocol and `ContractInput` |
| `issuesmith/gates/dep.py` | Dependency gate |
| `issuesmith/gates/m1.py` | M1 version-behind-base gate |
| `issuesmith/gates/m2.py` | M2 gate |
| `issuesmith/gates/pr_scope.py` | PR scope gate |
| `issuesmith/gates/scope.py` | Scope gate |
| `issuesmith/gates/worktree.py` | `lint`, `tests`, `external_leak`, `base_freshness` gates |
| `issuesmith/github_api.py` | `gh` command: Issue / PR helpers with label and target guards |
| `issuesmith/language.py` | Language packs |
| `issuesmith/m2_gate.py` | M2 acceptance-criteria gate entry point |
| `issuesmith/merge.py` | PR merge and verification helpers |
| `issuesmith/metrics_events.py` | Metrics JSONL event writers |
| `issuesmith/metrics_rework.py` | Rework metrics aggregation (`metrics rework`) |
| `issuesmith/milestone.py` | Milestone chain and SUB1 split-plan helpers |
| `issuesmith/observe/__init__.py` | Observe layer: `observe()` |
| `issuesmith/observe/dag_state.py` | DAG liveness from `exec.jsonl` and done / running markers |
| `issuesmith/observe/events.py` | Observe event types |
| `issuesmith/observe/main_health.py` | Base-branch health check |
| `issuesmith/observe/policy.py` | Turn events into actions and run them |
| `issuesmith/ops/__init__.py` | Operational commands package |
| `issuesmith/ops/dispatch.py` | `dispatch`: live re-render of shell step bodies |
| `issuesmith/ops/doctor.py` | `requires` chain validation |
| `issuesmith/ops/gen_live_dispatch.py` | `gen-live`: shell step trampoline templates |
| `issuesmith/ops/labels.py` | Phase / attention label projection and reconciliation |
| `issuesmith/ops/preflight.py` | `doctor`: runtime environment health check |
| `issuesmith/ops/publish.py` | `publish`: push, PR and version bump |
| `issuesmith/ops/repair_step.py` | Repair step for `requires` violations |
| `issuesmith/ops/smoke.py` | `smoke`: pipeline smoke test on real Issues |
| `issuesmith/ops/version_bump.py` | `version-bump`: `pyproject.toml` version bump |
| `issuesmith/pipeline_comments.py` | Pipeline comment filter |
| `issuesmith/pr_scope.py` | PR diff scope: allow_paths and `forbidden_pr_paths` |
| `issuesmith/preconditions.py` | `PRECONDITION_REGISTRY` / `evaluate` phase advance predicates |
| `issuesmith/projection.py` | `IssueState` / `project` / `diff` / `state_from_labels` |
| `issuesmith/queue.py` | `queue` command: enqueue, triage, dispatch, migrate |
| `issuesmith/queue_store.py` | Queue store: JSONL requests, state and lock |
| `issuesmith/queue_triage.py` | Deterministic gates and LLM triage for the queue |
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

`get_config()` loads the config once per process. The file is resolved in this order:

1. the path passed to `load_config(path)`
2. `ISSUESMITH_CONFIG`
3. `issuesmith.yaml` in the current directory or a parent
4. `issuesmith.yaml` at the package repository root
5. built-in defaults

Relative paths in the file resolve against the directory that holds it.

### Environment variables

| Name | Default | Description |
|---|---|---|
| `ISSUESMITH_CONFIG` | (unset) | Path to `issuesmith.yaml` |
| `ISSUESMITH_QUEUE_DIR` | (unset) | Directory for the queue, queue state, lock and triage log (overrides the `paths` defaults) |
| `ISSUESMITH_ENGINE_WAIT_POLL_SEC` | `60` | Re-check period while every engine is paused; values outside 1-60 give 60 |
| `ISSUESMITH_ENGINE_WAIT_INTERVAL_SEC` | `60` | Legacy name, read only when `ISSUESMITH_ENGINE_WAIT_POLL_SEC` is unset |
| `ISSUESMITH_ENGINE_WAIT_MAX_SEC` | `21600` | Longest wait for an engine to leave pause |
| `ISSUESMITH_TIMEOUT_SEC` | (unset) | LLM run timeout when the engine state has no `timeout_sec` for the role |
| `ISSUESMITH_PYTEST_TIMEOUT_SEC` | `1500` | Timeout of one pytest run in the `tests` gate |
| `ISSUESMITH_REPAIR_ACTIVE` | (unset) | Set internally during a repair dispatch to stop nested repairs |
| `METRICS_JSONL_PATH` | `paths.metrics` | Path of the engine metrics JSONL |
| `P0_FETCH_LOCK_WAIT` | `30` | Seconds to wait for the P0 base-fetch lock |
| `GHDAG_TASK_UUID` | (set by ghdag) | Current DAG task UUID, used as `parent_uuid` in metrics and dispatch |
| `AGENT_SKILLS_DIR` | `~/.agents/skills` | Skills directory checked by `doctor` |

### `issuesmith.yaml` keys

| Key | Default | Description |
|---|---|---|
| `repo` | (required) | Host repository `owner/name` |
| `label_namespace` | `issuesmith` | Managed label prefix |
| `timezone` | `Asia/Tokyo` | IANA timezone |
| `supported_repos` | `[]` | Repositories allowed as cross-repo targets |
| `paths` | see below | Queue, state, metrics, worktree and template paths |
| `engines` | see [Quick Start](#quick-start) | Per role (`design`, `implementation`): `allowed`, `default_model`, `light_model`, `timeout_sec` |
| `concurrency` | `default: 1` | `default`, `per_engine`, `strict_order` (`false`) |
| `milestone_chain` | `enabled: false` | `enabled`, `child_priority` (`normal`), `auto_develop` (`true`), `auto_close_parent` (`true`) |
| `triage` | `enabled: true` | Queue LLM triage: `engine` (`claude`), `model` (`claude-sonnet-4-6`), `timeout` (`60`), `body_chars` (`500`), `circuit_breaker_threshold` (`3`), `circuit_breaker_reset_seconds` (`1800`) |
| `phases` | `draft`, `sub`, `develop`, `merge` | List of phase mappings (see below) |
| `steps` | `p1` with `andon_when` | Per step: `module`, `template`, `requires`, `input_kind`, `accepts`, `andon_when` |
| `label_write_guard` | `warn` | `warn` or `enforce`: block step label writes through the forge |
| `forbidden_pr_paths` | `jobs/**`, `logs/**`, `.sessions/**`, `*.jsonl`, `*.pid`, `*.lock` | Paths a PR may not touch |
| `scope_gate` | `enabled: true` | P0 allow_paths size: `max_files` (`80`), `max_lines` (`20000`), `hard_max_files` (`200`) |
| `scope_coupling` | `enabled: true` | `search_dirs` (`[tests, src]`), `data_file_tests` (`true`) |
| `scope_size` | `enabled: true` | B1 Issue size: `max_files` (`8`), `max_concerns` (`2`), `delete_with_new` (`false`), `exclude_prefixes` |
| `tests` | `flaky_reruns: 2` | Reruns of newly failing tests on the branch (`0` turns flaky detection off) |
| `metrics` | `done_step: m2` | Rework metrics: `done_step`, `repair_templates`, `cause_targets` |
| `derived_allow` | `enabled: true`, `ledger_globs: [tests/conventions/known_*.txt]` | Allow repair to edit newly failing tests outside allow_paths; ledgers matching `ledger_globs` next to a failing test may only shrink (`[]` turns ledger derivation off) |
| `external_leak` | `cjk_free_external_targets: false` | Fail added CJK lines on branches for external targets |
| `terminal_labels` | `issuesmith:merge-done`, `bump:done` | Labels that mark a finished Issue |
| `observe` | see below | Observe layer thresholds and `main_health` |
| `api_brake` | `enabled: false` | GitHub API brake: `min_remaining` (`800`) |
| `language_pack` | (unset: `EN`) | Path to a language pack YAML; see [Language packs](#language-packs) |
| `sections` / `sub_design_subsections` | (deprecated) | Use `language_pack` |

`tests`, `metrics`, `derived_allow` and `external_leak` reject unknown keys with `ConfigError`. `scope_coupling.ignore_symbols` is no longer supported and raises `ConfigError`.

#### `phases[]` fields

| Field | Default | Description |
|---|---|---|
| `name` | (required) | Phase name (`draft`, `sub`, `develop`, `merge` in the built-in set) |
| `role` | (required) | `design` or `implementation` |
| `entry_step` | (required) | First step id of the phase |
| `handler` | `brushup` / `subissue` / `impl` / `merge` by phase name | Workflow handler name |
| `preconditions` | `[]` | Labels that must be present before the phase can start |
| `excludes` | `[]` | Labels that block the phase |
| `writes_files` | `true` (`false` for `draft` and `sub`) | Whether the phase writes files |
| `advance_when` | `deps_terminal` (`merge` also `closing_pr_exists`) | Predicates from `PRECONDITION_REGISTRY` |
| `steps` | `[]` | Step ids in run order; empty means `(entry_step,)` |

#### `steps.*` fields

| Field | Default | Description |
|---|---|---|
| `module` | `""` | Python module implementing the step |
| `template` | (unset) | Template path for LLM steps |
| `requires` | `[]` | Gate ids evaluated after the step |
| `input_kind` | `issue` | `issue`, `worktree`, or `artifact` |
| `accepts` | `[]` | Accepted artifact kinds |
| `andon_when` | `[]` (`p1`: `external_leak.target_unknown`) | Violation ids that raise an andon instead of repair |

`paths` keys and defaults (relative to the config directory): `queue` `jobs/issuesmith-queue.jsonl`, `queue_state` `logs/issuesmith-queue-state.json`, `queue_lock` `logs/issuesmith-queue.lock`, `triage_log` `jobs/issuesmith-triage.jsonl`, `seed` `configs/night-queue.yaml`, `night_state` `logs/night-queue-state.json`, `exec_jsonl` `jobs/exec.jsonl`, `done_dir` `jobs/done`, `quota_state` `jobs/quota-gate.json`, `metrics` `jobs/metrics.jsonl`, `worktrees_dir` `.claude/worktrees`, `external_dir` `.claude/external`, `workflow` `workflows/issuesmith.yml`, `template_dir` `workflows/issuesmith`, `engine_state` `.pipeline-state/issuesmith-engine.yml`, `brake_state` (defaults to `quota_state`).

`observe` keys and defaults: `stall_minutes` `120`, `task_timeout_minutes` `90`, `systemic_min_issues` `2`, `systemic_window_minutes` `60`, `forge_max_consecutive_errors` `3`, `max_api_calls` `8`. `observe.main_health` (off when unset) takes `worktree` and `command` (both required), `base_branch` (`main`) and `timeout_seconds` (`1800`).

### Language packs

A language pack holds the words issuesmith uses to read the host's Issue bodies and the text it posts to GitHub (Issue comments, andon summaries, PR titles). The package ships one pack, `issuesmith.language.EN`.

**Without `language_pack`, the built-in vocabulary is English.** Sections, sub-design subsections, the sub-design header (`#### Sub<N>:`) and change-table columns are read with the `EN` words; the older built-in Japanese defaults are gone. A host that writes Issues in another language must set `language_pack:` before upgrading:

```yaml
language_pack: configs/issuesmith-lang.xx.yaml
```

The pack file is a YAML mapping whose keys are `LanguagePack` field names. **Fields the pack leaves out, and keys it leaves out of `sections` and `messages`, fall back to `EN`**, so a pack written for an older release keeps loading after a release adds a field or message. Unknown keys, wrong types and wrong list lengths in the values the pack does supply raise `ConfigError`. The resolved pack is `get_config().language`; `get_config().sections`, `.sub_design_subsections` and the `scope_size` vocabulary come from it.

| Field | Type | `EN` value / meaning |
|---|---|---|
| `sections` | mapping | H2 headings keyed `acceptance_criteria`, `migration`, `migration_state_survey`, `sub_plan`, `design`, `background`, `dependencies`, `impact_survey`, `milestone`, `changed_files` |
| `sub_design_subsections` | list of 4 | Subsections of a sub design: scope, design policy, changed files, acceptance criteria |
| `sub_header_prefix` | string | `Sub`: header `#### <prefix>N:` |
| `sub_plan_columns` | list of 5 | `#`, `Title`, `Target repo`, `Content`, `Depends on` |
| `change_table_columns` | list of 4 | `Repository`, `File path`, `Change type`, `Description` |
| `no_deps_word` | string | `none` |
| `delete_words` | list | `delete` (case-insensitive substring) |
| `new_words` | list | `new`, `add` |
| `removal_words` | list | Scope verbs that announce a removal (`scope_coupling`) |
| `placeholder_words` | list | `TBD`, `TODO`, `FIXME`, `XXX`, `placeholder` |
| `vague_ac_words` | list | Words that make an acceptance criterion too vague |
| `derived_from_phrase` | string | `derived`: how a child body names its parent sub design |
| `parent_issue_label` | string | `Parent issue` |
| `dependencies_table_header` | string | `\| # \| Dependency \| State \|` |
| `out_of_scope_heading` | string | `Out of Scope` |
| `order_after_words` | list | `after`, `once`, `depends on`: words in sub design text that order a sub after another sub's completion |
| `messages` | mapping | GitHub-posted text keyed `<module>.<id>`; `str.format` templates with keyword placeholders. Copy the keys from `EN.messages` |

Logs, exception messages, CLI help and stderr, and violation `message` / `fix_hint` text stay English and are not part of a pack. Machine markers (HTML comments, `PIPELINE_STATUS:` lines, `Refs #N`) are added outside the templates.

**Deprecated keys.** Without `language_pack`, `sections`, `sub_design_subsections` and `scope_size.delete_words` / `new_words` / `sub_plan_header` / `no_deps_word` still override the matching `EN` fields and emit a `DeprecationWarning`. With `language_pack` set they are ignored (with a warning). They will be removed in the next release.

## Error Reference

| Type | Module | Base | When |
|---|---|---|---|
| `ConfigError` | `issuesmith.config` | `ValueError` | Invalid `issuesmith.yaml`, language pack or `steps.*.requires` |
| `QueueValidationError` | `issuesmith.queue_store` | `ValueError` | Invalid queue request input (CLI exits 2) |
| `GateBuildError` | `issuesmith.gates` | `ValueError` | `GateEntry.build` cannot instantiate a gate |
| `DerivedAllowPathsError` | `issuesmith.pr_scope` | `ValueError` | `derived_allow_paths_from_result` finds malformed entries |
| `MainHealthError` | `issuesmith.observe.main_health` | `RuntimeError` | The health check could not run (git, worktree, timeout); state is not updated |
| `GateMaterializationError` | `issuesmith.ac_contract` | `RuntimeError` | The `origin/<base>` temporary worktree could not be created |
| `RetrySignal` | `issuesmith.engine` | `RuntimeError` | A step is deferred for retry (engine paused or rate-limited); not a failure |
| `LabelWriteForbidden` | `issuesmith.contract` | `RuntimeError` | A step wrote labels through the forge while `label_write_guard` is `enforce` |
| `WorktreeError` | `issuesmith.worktree` | `Exception` | Worktree preparation failed |
| `MergeStateTimeoutError` | `issuesmith.merge` | `Exception` | PR merge state stayed `BLOCKED` until the timeout |

A config file without `repo`, or with a malformed `phases`, `forbidden_pr_paths`, `terminal_labels` or `observe.main_health`, raises plain `ValueError`. `TemplateVariableError` (`ghdag.pipeline.order`) can propagate from `issuesmith.engine` when a template variable is missing at render time.

## License

MIT (SPDX `MIT`). See [LICENSE](./LICENSE).
