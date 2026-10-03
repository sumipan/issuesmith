# issuesmith

issuesmith is a GitHub Issue label-driven workflow toolkit that runs on [ghdag](https://github.com/sumipan/ghdag). ghdag owns DAG execution, polling, and label transitions; issuesmith supplies Issue-domain gates, queue triage, context hooks, and a unified CLI that workflow templates invoke.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-v0.113.0-blue)
![ci](https://github.com/sumipan/issuesmith/actions/workflows/ci.yml/badge.svg?branch=main)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Current release is **v0.113.0** (pre-1.0). Public interfaces may change before `1.0.0`.

## Installation

```bash
pip install "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.113.0"
```

With ghdag (required for `gate-preflight`, dispatch, and most runtime paths):

```bash
pip install "issuesmith[ghdag] @ git+https://github.com/sumipan/issuesmith.git@v0.113.0"
```

| Item | Value |
|---|---|
| Python requirement | `>=3.10` |
| Runtime dependencies | `pyyaml`, `ruamel.yaml`, `packaging`, `python-dotenv` |
| Optional / recommended | `ghdag @ git+https://github.com/sumipan/ghdag.git@v0.72.0` (`[ghdag]` or `[dev]`) |
| Dev dependencies | `pip install "issuesmith[dev]"` |

## Quick Start

Minimal `issuesmith.yaml` at the repository root:

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
    timeout_sec: 1800
  implementation:
    allowed: [claude, cursor]
    default_model:
      claude: claude-sonnet-4-6
      cursor: auto
    timeout_sec: 3600
```

Run preflight and engine checks:

```bash
export ISSUESMITH_CONFIG=/path/to/issuesmith.yaml   # optional when yaml is at cwd or above
python3 -m issuesmith doctor
python3 -m issuesmith gate-preflight --gate cp1 --body-file body.md
python3 -m issuesmith engine show
python3 -m issuesmith config show
```

Common entry points: `python3 -m issuesmith doctor`, `python3 -m issuesmith gate-preflight --gate cp1 --body-file body.md`, `python3 -m issuesmith resume <issue> --from <step>`, and `python3 -m issuesmith config show`.

## CLI Reference

Entry points: `issuesmith` / `python3 -m issuesmith`.

Top-level commands are registered in `issuesmith.cli._HANDLERS`. `andon` is dispatched from `issuesmith.__main__` before the handler table (not listed in `_HANDLERS`).

| Command | Description |
|---|---|
| `context` / `context_hook` / `context-hook` | Generate ghdag context for impl/merge handlers |
| `gate` / `gate-preflight` | Run a named gate (`gate cp1 --body-file ...` aliases `gate-preflight`) |
| `cp1-gate` / `m2-gate` | Direct CP1 / M2 gate entry points |
| `verify` / `b1-verify` | B1 verification (`verify b1 ...`) |
| `queue` | Night / draft queue tick and status |
| `deps` | Extract Issue dependencies |
| `tier` | Choose B1 / CP2 model tier (`tier b1` / `tier cp2`) |
| `comments` | Pipeline comment helpers |
| `gh` | GitHub Issue/PR helpers via ghdag client (not the `gh` CLI) |
| `engine` | LLM role switcher / runner (see subcommands below) |
| `dispatch` | Render and enqueue a workflow template |
| `publish` | Publish / version-bump orchestration |
| `labels` | Managed-label reconciliation (`labels reconcile [--fix] [--json]`) |
| `doctor` | Preflight / environment checks |
| `smoke` | Template smoke against live Issue bodies |
| `gen-live` | Generate live dispatch payloads |
| `version-bump` | Deterministic package version bump |
| `resume <issue> --from <step>` / `--phase <phase>` | Resume a workflow from a step or phase |
| `recover` | Deprecated; use `resume --from <step>` instead |
| `redispatch` | Deprecated; use `resume --phase <phase>` instead |
| `convert-to-milestone` | Convert an Issue into a milestone chain |
| `milestone` | Milestone chain status / resume / prune |
| `config` | Resolved configuration (`config show`) |
| `observe [--apply] [--json]` | Collect observe events; `--apply` executes policy actions |
| `main-health` | Run `observe.main_health.command` on the latest base branch and write state |
| `apply` / `ingest-review` | Moved to host `tools/stash/`; exits 2 |

### `andon` subcommands

Dispatched from `issuesmith.__main__` (not in `_HANDLERS`).

| Subcommand | Description |
|---|---|
| `andon list [--all] [--json]` | List open (unanswered) andons |
| `andon show <andon-id>` | Show a specific andon by id |
| `andon answer <andon-id> <action>` | Post answer, remove label, call resume hook |
| `andon note <andon-id> --key <key> --value <value>` | Record a note on an open andon (no label change) |

### `engine` subcommands

| Subcommand | Description |
|---|---|
| `engine show` | Show effective role assignments |
| `engine check` | Validate commands, models, and workflow drift |
| `engine switch <role> <engine> [--model] [--light-model]` | Switch one role |
| `engine run-guarded <role> <template> [--cwd] [--tier] [--success ...] [--requires-step <id>] [vars...]` | Run an LLM template with pre/post gate evaluation |
| `engine run-verified` | Run with verify/recover loop (internal) |
| `engine run` / `engine exec` / `engine resolve` | Internal dispatch helpers |

`run-guarded` is **not** a top-level command. Module-less LLM steps must be invoked as `engine run-guarded --requires-step <step_id>`.

### `queue` subcommands

| Subcommand | Description |
|---|---|
| `queue enqueue` | Enqueue an Issue for a phase |
| `queue tick` | Process the queue (night / draft dispatch loop) |
| `queue status` | Show queue state |
| `queue doctor` | Check for untracked in-flight Issues |
| `queue reset` | Reset queue halt state |
| `queue skip` | Skip an Issue in the queue |
| `queue dequeue` | Remove a request by ID |
| `queue audit` | Audit queue integrity (`--offline` skips GitHub checks) |
| `queue triage-log` | Show recent triage log entries |
| `queue migrate` | Migrate from legacy night-queue state |
| `queue release` | Release a design-slot in-flight entry (recovery only) |

### `milestone` subcommands

| Subcommand | Description |
|---|---|
| `milestone status <parent>` | Show milestone chain status |
| `milestone resume <parent>` | Resume milestone chain progress |
| `milestone prune [--dry-run]` | Prune completed milestone chain state |

### Deprecated API

| Command | Replacement |
|---|---|
| `recover` | `resume <issue> --from <step>` |
| `redispatch` | `resume <issue> --phase <phase>` |
| `apply` / `ingest-review` | Host `tools/stash/` scripts; CLI exits 2 |

## Public API

Top-level `issuesmith.__all__` is empty (`[]`); import submodules directly.

### `issuesmith.body_editor`

| Symbol | Notes |
|---|---|
| `count_heading` | Count markdown headings |
| `filter_section_by_paths` | Filter a section by allow_paths |
| `get_section` | Extract a named H2 section |
| `get_section_by_keyword` | Find section by keyword alias |
| `get_subsections` | List H3 subsections under a section |
| `normalize_sub_headers` | Normalize subsection headers |
| `relocate_sub_plan` | Move sub-plan content between sections |
| `apply_milestone_normalizers` | Apply milestone body normalizers |
| `replace_allow_paths` | Replace allow_paths block in body |
| `split_h2_sections` | Split body into H2 sections |
| `upsert_section` | Insert or replace an H2 section |

### `issuesmith.language`

| Symbol | Notes |
|---|---|
| `LanguagePack` | Frozen dataclass of Issue-body vocabulary and GitHub-posted text |
| `EN` | Built-in English pack (the only pack shipped) |
| `load_language_pack` | Load and validate a pack YAML file |
| `language_pack_from_mapping` | Validate a parsed mapping into a `LanguagePack` |

### `issuesmith.contract`

| Symbol | Notes |
|---|---|
| `sub_header_re` | Sub-design header regex built from the configured pack |
| `SUB_HEADER_RE` | Lazy object delegating `search` / `match` / `finditer` / `findall` / `sub` to `sub_header_re()` |

### `issuesmith.gates`

| Symbol | Notes |
|---|---|
| `Verdict` | Gate evaluation result |
| `GateBuildContext` | Context for gate instantiation |
| `GateBuildError` | Gate cannot be instantiated |
| `GateEntry` | Registry entry for a gate |
| `RequiresGate` | Gate used in `steps.*.requires` |
| `GATE_REGISTRY` | Unified gate registry |
| `validate_step_requires` | Validate `steps.*.requires` chain |
| `check_scope` | Run scope gate |
| `check_pr_scope` | Run PR scope gate |
| `check_m2` | Run M2 gate |
| `check_deps` | Run dependency gate |

### `issuesmith.gates.base`

| Symbol | Notes |
|---|---|
| `ContractInput` | Input for gate `check()` / `fix()` |
| `Gate` | Gate protocol |

### `issuesmith.gates.dep`

| Symbol | Notes |
|---|---|
| `check_deps` | Evaluate dependency gate |
| `DepsGate` | Dependency gate class |
| `dependents_of` | List Issues that depend on a given Issue |
| `on_dep_merge_done` | Notify dependents when a dependency merges |

### `issuesmith.gates.m1`

| Symbol | Notes |
|---|---|
| `RULE_ID` | M1 version-behind-base rule identifier |
| `VersionBehindBaseGate` | M1 version-behind-base gate |

### `issuesmith.gates.m2`

| Symbol | Notes |
|---|---|
| `check_m2` | Evaluate M2 merge gate |

### `issuesmith.gates.pr_scope`

| Symbol | Notes |
|---|---|
| `check_pr_scope` | Evaluate PR diff scope |
| `PrScopeGate` | PR scope gate class |

### `issuesmith.gates.scope`

| Symbol | Notes |
|---|---|
| `check_scope` | Evaluate allow_paths scope gate |
| `ScopeGate` | Scope gate class |

### `issuesmith.merge`

| Symbol | Notes |
|---|---|
| `CompanionReadyResult` | Companion PR readiness result |
| `LocalMergeVerifyResult` | Local merge verification result |
| `MergeStateInfo` | PR merge state snapshot |
| `MergeStateTimeoutError` | Merge state polling timed out |
| `PostMergeTestResult` | Post-merge pytest result |
| `PrSearchResult` | PR search result |
| `check_companion_ready` | Check companion PR readiness |
| `find_companion_pr` | Find companion PR for a branch |
| `find_pr` | Find PR by branch |
| `get_merge_state` | Read PR merge state |
| `graphql_merge_state` | GraphQL merge state query |
| `is_already_merged` | Check whether PR is merged |
| `list_pulls` | List pull requests |
| `run_post_merge_pytest` | Run pytest after merge |
| `verify_local_merge` | Verify local merge result |
| `wait_merge_state` | Poll until merge state settles |

### `issuesmith.m2_gate`

| Symbol | Notes |
|---|---|
| `has_acceptance_criteria_section` | Detect acceptance-criteria section |
| `get_unchecked_count` | Count unchecked AC items |
| `check_gate` | Run M2 gate checks |
| `check_gate_multi_root` | Run M2 gate across multiple roots |
| `synthesize_contract_failures` | Format multi-root contract failures |
| `main` | CLI entry point |

### `issuesmith.pr_scope`

| Symbol | Notes |
|---|---|
| `DEFAULT_FORBIDDEN_PR_PATHS` | Default paths excluded from PR scope |
| `DIFF_LINES_FALLBACK` | Fallback diff line count |
| `DerivedAllowPathsError` | Derived allow_paths could not be computed |
| `allow_paths_from_issue_body` | Parse allow_paths from Issue body |
| `check_pr_diff_scope` | Check PR diff against allow_paths |
| `check_pr_scope_with_derived` | PR scope check with derived paths |
| `derived_allow_paths_from_result` | Derive allow_paths from test failures |
| `filenames_from_pr_files` | Extract filenames from PR files |
| `find_pr_for_branch` | Find PR for a branch name |
| `pr_diff_lines` | Count PR diff lines |
| `unchecked_ac_count` | Count unchecked acceptance criteria |

### `issuesmith.repair`

| Symbol | Notes |
|---|---|
| `RequiresResult` | Result of requires evaluation |
| `evaluate_requires` | Evaluate step requires chain |
| `apply_auto_fixes` | Apply automatic fixes from gate verdicts |
| `record_metrics` | Record repair metrics |

### `issuesmith.scope_gate`

| Symbol | Notes |
|---|---|
| `ScopeMeasure` | Scope measurement (files, lines, by_dir) |
| `ScopeVerdict` | Scope gate verdict |
| `evaluate` | Evaluate scope against limits |
| `format_comment` | Format scope gate comment |
| `measure_scope` | Measure scope of changed files |
| `override_from_metadata` | Override scope limits from metadata |
| `parse_allow_paths_from_ctx` | Parse allow_paths from step context |
| `record_p0_trip_metric` | Record P0 scope trip metric |
| `resolve_scope_root` | Resolve scope measurement root |

### `issuesmith.worktree`

| Symbol | Notes |
|---|---|
| `WorktreeError` | Worktree creation or validation failed |
| `assert_jobs_clean` | Assert jobs directory is clean |
| `clone_if_missing` | Clone external repo if missing |
| `ensure_base_included` | Ensure base branch is included in worktree |
| `fetch_base_or_raise` | Fetch base branch or raise |
| `fetch_base_with_retry` | Fetch base with lock and retries |
| `github_client` | Return forge client |
| `handle_milestone` | Handle milestone-blocked Issues |
| `prepare_cross_repo_worktree` | Prepare cross-repo worktree |
| `prepare_local_worktree` | Prepare local worktree |
| `prepare_worktree` | Prepare worktree (local or cross-repo) |
| `require_yaml_metadata` | Require YAML metadata in Issue body |
| `resolve_base_ref` | Resolve base branch ref |
| `validate_branch` | Validate branch name |

### `issuesmith.ops.publish`

| Symbol | Notes |
|---|---|
| `PublishResult` | Publish operation result |
| `_bump_versions_in_range` | Bump versions in commit range (internal) |
| `_run_version_bump` | Run version bump subprocess (internal) |
| `main` | CLI entry point |
| `publish` | Publish branch and create release |
| `run_version_bump` | Run deterministic version bump |

### `issuesmith.verbs`

| Symbol | Notes |
|---|---|
| `prepare_worktree` | Worktree verb |
| `publish_branch` | Publish verb |
| `find_pr` | Merge verb: find PR |
| `merge_state` | Merge verb: read merge state |
| `merge_pr` | Merge verb: merge PR |
| `cleanup_worktrees` | Finalize verb: remove worktrees |
| `cleanup_branches` | Finalize verb: remove branches |
| `close_issue` | Finalize verb: close Issue |

### Deprecated step shims (`issuesmith.steps.*`)

Each `issuesmith.steps.<name>` module re-exports its canonical location. Prefer the canonical module.

| Module | Canonical location | `__all__` exports |
|---|---|---|
| `issuesmith.steps.base` | `issuesmith.contract` | `Andon`, `StepContext`, `StepResult`, `Verdict` |
| `issuesmith.steps.m1_merge` | `issuesmith.merge` | `run` |
| `issuesmith.steps.m2_finalize` | `issuesmith.ac_contract` | `GateMaterializationError`, `check_gate`, `extract_contract_from_body`, `run`, `run_checks` |
| `issuesmith.steps.p0_worktree` | `issuesmith.worktree` | `WorktreeError`, `fetch_base_with_retry`, `validate_branch`, … |
| `issuesmith.steps.scope_gate` | `issuesmith.scope_gate` | `ScopeMeasure`, `ScopeVerdict`, `evaluate`, `measure_scope`, … |
| `issuesmith.steps.sub1_create` | `issuesmith.milestone` | `PlanRow`, `Sub1State`, `parse_split_plan`, `run`, … |

## Architecture

Orchestration (polling, DAG construction, label transitions, idempotency) lives in **ghdag** `WorkflowDispatcher`. issuesmith provides Issue-domain tools, gates, and steps that workflow templates call.

| Module | Role |
|---|---|
| `issuesmith/__init__.py` | Package docstring; empty `__all__` |
| `issuesmith/__main__.py` | Dispatches `andon` / `labels` before `cli.main` |
| `issuesmith/ac_contract.py` | Acceptance-criteria contract DSL and path/reference checks |
| `issuesmith/andon.py` | Andon list/answer/note |
| `issuesmith/b1_tier.py` | B1 model tier selection |
| `issuesmith/b1_verify.py` | B1 verification runner |
| `issuesmith/body_editor.py` | Issue body edit helpers |
| `issuesmith/branch_reuse.py` | Branch reuse detection for worktrees |
| `issuesmith/cli.py` | Unified CLI (`_HANDLERS`) |
| `issuesmith/config.py` | `issuesmith.yaml` resolution and defaults |
| `issuesmith/context_hook.py` | Issue YAML metadata and ghdag context |
| `issuesmith/contract.py` | Canonical parsers for Issue-body contract sections |
| `issuesmith/convert_to_milestone.py` | Convert Issue to milestone chain |
| `issuesmith/cp1_gate.py` | CP1 gate CLI entry |
| `issuesmith/cp2_tier.py` | CP2 model tier selection |
| `issuesmith/dep_extractor.py` | Dependency extraction from Issue bodies |
| `issuesmith/engine.py` | LLM role switcher, `run-guarded`, metrics |
| `issuesmith/forge_api.py` | ghdag forge client wrappers |
| `issuesmith/gate_rules/__init__.py` | Re-exports ghdag gate types |
| `issuesmith/gate_rules/b1_ac_format.py` | B1 acceptance-criteria YAML format rules (all Issues with YAML blocks) |
| `issuesmith/gate_rules/b1_migration.py` | B1 migration plan rules |
| `issuesmith/gate_rules/b1_milestone_subdesign.py` | B1 milestone sub-design rules |
| `issuesmith/gate_rules/cp1.py` | CP1 design gate rules |
| `issuesmith/gate_rules/m2.py` | M2 merge gate rules |
| `issuesmith/gate_rules/milestone_consistency.py` | Milestone consistency rules |
| `issuesmith/gate_rules/scope_breadth.py` | Scope breadth (pre-LLM) rules |
| `issuesmith/gate_rules/scope_coupling.py` | Scope coupling rules (deleted src layout references) |
| `issuesmith/gate_rules/scope_size.py` | Issue size rules |
| `issuesmith/gates/__init__.py` | Unified `GATE_REGISTRY` and `Verdict` |
| `issuesmith/gates/base.py` | Shared gate base types |
| `issuesmith/gates/dep.py` | Dependency gate (`DepsGate`) |
| `issuesmith/gates/m1.py` | M1 version-behind-base gate |
| `issuesmith/gates/m2.py` | M2 merge checks |
| `issuesmith/gates/pr_scope.py` | PR scope gate |
| `issuesmith/gates/scope.py` | Scope gate |
| `issuesmith/gates/worktree.py` | Lint, tests, external_leak, base_freshness gates |
| `issuesmith/github_api.py` | Issue API wrappers |
| `issuesmith/m2_gate.py` | M2 gate CLI entry |
| `issuesmith/merge.py` | Reusable PR merge and verification helpers |
| `issuesmith/milestone.py` | Milestone chain status, resume, and prune |
| `issuesmith/observe/__init__.py` | Observe package |
| `issuesmith/observe/dag_state.py` | DAG state readers |
| `issuesmith/observe/events.py` | Observe event types |
| `issuesmith/observe/main_health.py` | Base-branch health check |
| `issuesmith/observe/policy.py` | Observe policy actions |
| `issuesmith/ops/__init__.py` | Ops package |
| `issuesmith/ops/dispatch.py` | Template render and enqueue |
| `issuesmith/ops/doctor.py` | Preflight / environment checks |
| `issuesmith/ops/gen_live_dispatch.py` | Live dispatch payload generation |
| `issuesmith/ops/labels.py` | Managed-label reconciliation |
| `issuesmith/ops/preflight.py` | Gate preflight helpers |
| `issuesmith/ops/publish.py` | Publish / version-bump orchestration (detects bump commit anywhere in `origin/base..HEAD`) |
| `issuesmith/ops/repair_step.py` | Explicit repair step for requires violations |
| `issuesmith/ops/smoke.py` | Template smoke tests |
| `issuesmith/ops/version_bump.py` | Deterministic version bump |
| `issuesmith/pipeline_comments.py` | Pipeline comment helpers |
| `issuesmith/pr_scope.py` | PR diff scope helpers |
| `issuesmith/queue.py` | Queue dispatch loop |
| `issuesmith/queue_store.py` | Persistent queue state |
| `issuesmith/queue_triage.py` | LLM triage / title normalization |
| `issuesmith/quota_gate.py` | Quota gate state |
| `issuesmith/recovery.py` | Deprecated recover/redispatch implementation |
| `issuesmith/repair.py` | Repair-step helpers |
| `issuesmith/resume.py` | Resume workflow from step or phase |
| `issuesmith/scope_gate.py` | Reusable allow_paths scope measurement |
| `issuesmith/steps/__init__.py` | Deprecated step shim package |
| `issuesmith/steps/base.py` | Step base types (deprecated shim) |
| `issuesmith/steps/m1_merge.py` | M1 merge step (deprecated shim) |
| `issuesmith/steps/m2_finalize.py` | M2 finalize step (deprecated shim) |
| `issuesmith/steps/p0_worktree.py` | P0 worktree creation (deprecated shim) |
| `issuesmith/steps/repair.py` | Repair step wrapper (deprecated shim) |
| `issuesmith/steps/scope_gate.py` | Scope root resolver (deprecated shim) |
| `issuesmith/steps/sub1_create.py` | Sub-issue creation step |
| `issuesmith/targets.py` | Target repository resolution |
| `issuesmith/template_ids.py` | Template id constants |
| `issuesmith/verbs/__init__.py` | Workflow verb package |
| `issuesmith/verbs/finalize.py` | Finalize verb |
| `issuesmith/verbs/merge.py` | Merge verb |
| `issuesmith/verbs/publish.py` | Publish verb |
| `issuesmith/verbs/worktree.py` | Worktree verb |
| `issuesmith/worktree.py` | Worktree creation and base-branch fetch |

## Configuration

### Environment variables

| Name | Default | Description |
|---|---|---|
| `ISSUESMITH_CONFIG` | (none) | Absolute path to `issuesmith.yaml` |
| `ISSUESMITH_QUEUE_DIR` | (none) | Override directory for queue / triage files |
| `ISSUESMITH_ENGINE_WAIT_POLL_SEC` | `60` | While all engines are paused, re-check quota every N seconds (clamped 1–60) |
| `ISSUESMITH_ENGINE_WAIT_INTERVAL_SEC` | `60` | Legacy alias for `ISSUESMITH_ENGINE_WAIT_POLL_SEC` when unset |
| `ISSUESMITH_ENGINE_WAIT_MAX_SEC` | `21600` | Max seconds to wait for an engine to leave pause |
| `ISSUESMITH_PYTEST_TIMEOUT_SEC` | (none) | Per-invocation pytest timeout for worktree `tests` gate |
| `ISSUESMITH_TIMEOUT_SEC` | role `timeout_sec` from config | Total wall budget for engine wait + LLM call |
| `ISSUESMITH_REPAIR_ACTIVE` | (unset) | Set internally during repair dispatch to prevent nested repair |
| `METRICS_JSONL_PATH` | `paths.metrics` from config | Override path for engine metrics JSONL |
| `P0_FETCH_LOCK_WAIT` | `30` | Seconds to wait for P0 base-fetch lock |
| `GHDAG_TASK_UUID` | (none) | Current DAG task UUID for dispatch correlation |
| `AGENT_SKILLS_DIR` | `~/.agents/skills` | Skills directory for doctor/preflight |

Config file resolution order: explicit path argument → `ISSUESMITH_CONFIG` → walk up from cwd → package repo-root `issuesmith.yaml` → builtin defaults.

### `issuesmith.yaml` top-level keys

| Key | Default | Description |
|---|---|---|
| `repo` | (required) | Primary GitHub repository (`owner/name`) |
| `label_namespace` | (required) | Managed label prefix |
| `timezone` | (required) | IANA timezone (e.g. `Asia/Tokyo`) |
| `supported_repos` | `[]` | Repositories allowed for cross-repo Issues |
| `paths` | see `PathsConfig` | Queue, workflow, template, metrics, and worktree paths |
| `engines` | design + implementation roles | Allowed engines, models, and timeouts per role |
| `concurrency` | `default: 1` | Per-engine concurrency limits |
| `milestone_chain` | `enabled: false` | Milestone chain automation |
| `triage` | `enabled: true` | Queue tick LLM reorder |
| `phases` | draft/sub/develop/merge | Phase → role → entry step mapping |
| `language_pack` | (unset: built-in English pack `EN`) | Path to a language pack YAML (relative to the config file); see [Language packs](#language-packs) |
| `sections` | (deprecated) | Legacy section heading map; use `language_pack` |
| `sub_design_subsections` | (deprecated) | Legacy sub-design subsections; use `language_pack` |
| `steps` | `m2-role-dispatch` | Step definitions (`module`, `template`, `requires`) |
| `forbidden_pr_paths` | `jobs/**`, `logs/**`, … | Paths excluded from PR scope |
| `scope_gate` | `enabled: true`, `max_files: 80`, … | P0 allow_paths size gate |
| `scope_coupling` | `enabled: true` | Caller/test coupling gate |
| `scope_size` | `enabled: true`, `max_files: 8`, … | B1 Issue size gate |
| `tests` | `flaky_reruns: 2` | Branch-side reruns for newly failing tests (`0` disables flaky detection) |
| `derived_allow` | `enabled: true` | Derived allow_paths for newly failing tests |
| `external_leak` | `cjk_free_external_targets: false` | Cross-repo CJK leak gate |
| `terminal_labels` | `issuesmith:merge-done`, `bump:done` | Labels that mark terminal state |
| `observe` | stall/task timeouts, `main_health` | Observe layer configuration |
| `api_brake` | `enabled: false` | GitHub API rate-limit brake |

Nested `paths` keys (relative to config root unless absolute): `queue`, `queue_state`, `queue_lock`, `triage_log`, `seed`, `night_state`, `exec_jsonl`, `done_dir`, `quota_state`, `metrics`, `worktrees_dir`, `external_dir`, `workflow`, `template_dir`, `engine_state`, `brake_state` (defaults to `quota_state`).

Legacy `scope_size` vocabulary keys `delete_words`, `new_words`, `sub_plan_header` and `no_deps_word` are deprecated as well (see below).

Nested `observe.main_health` keys: `worktree` (required when enabled), `command`, `base_branch` (`main`), `timeout_seconds` (`1800`).

### Language packs

A language pack holds the vocabulary issuesmith uses to read the host's Issue bodies and the text it posts to GitHub (Issue comments, andon summaries, PR titles). The package ships the English pack `issuesmith.language.EN` only; a host that writes Issues in another language keeps its own pack file and points `language_pack:` at it:

```yaml
language_pack: configs/issuesmith-lang.xx.yaml
```

The pack file is a YAML mapping whose keys are the `LanguagePack` field names. Fields the pack leaves out, and keys it leaves out of `sections` and `messages`, fall back to the `EN` values, so a pack written for an older release keeps loading after a release adds a field or message. Unknown keys and wrong types in the values the pack does supply raise `ConfigError` when the config is loaded. The resolved pack is `get_config().language`; `get_config().sections`, `.sub_design_subsections` and `.scope_size.*` vocabulary are derived from it.

| Field | Type | Meaning |
|---|---|---|
| `sections` | mapping (10 keys) | H2 section headings: `acceptance_criteria`, `migration`, `migration_state_survey`, `sub_plan`, `design`, `background`, `dependencies`, `impact_survey`, `milestone`, `changed_files` |
| `sub_design_subsections` | list (4) | Bold-label subsections of a sub design: scope, design policy, changed files, acceptance criteria |
| `sub_header_prefix` | string | Sub-design header prefix: `#### <prefix>N:` (`contract.sub_header_re()`) |
| `sub_plan_columns` | list (5) | Sub-issue plan table columns: `#`, title, target repo, content, depends on |
| `change_table_columns` | list (4) | Change table columns: repository, file path, change type, description |
| `no_deps_word` | string | Depends-on cell value meaning "no dependency" |
| `delete_words` | list | Change-type words for deletions (case-insensitive substring) |
| `new_words` | list | Change-type words for new files |
| `removal_words` | list | Scope verbs that announce a removal (`scope_coupling`) |
| `placeholder_words` | list | Placeholder words of an unfilled value (milestone V3) |
| `vague_ac_words` | list | Words that make an acceptance criterion too vague (`b1_milestone_subdesign`) |
| `derived_from_phrase` | string | Phrase a child Issue body uses to name its parent sub design |
| `parent_issue_label` | string | Label in front of the parent Issue reference in a child body |
| `dependencies_table_header` | string | Header row of the dependency table SUB1 writes into a child body |
| `out_of_scope_heading` | string | H2 heading of the out-of-scope section (`relocate_sub_plan` inserts the milestone section before it) |
| `messages` | mapping | GitHub-posted text keyed `<module>.<id>`; values are `str.format` templates with keyword placeholders. Copy the keys from `issuesmith.language.EN.messages` |

Logs, exception messages, CLI help / stderr and Violation `message` / `fix_hint` stay English and are not part of a pack. Machine markers (HTML comments, `PIPELINE_STATUS:` lines, `Refs #N`) are appended outside the templates.

**Legacy keys (deprecated, removed in the next release).** Without `language_pack`, the keys `sections`, `sub_design_subsections` and `scope_size.delete_words` / `new_words` / `sub_plan_header` / `no_deps_word` still override the matching `EN` fields and emit a `DeprecationWarning`. With `language_pack` set they are ignored (with a warning).

## Error Reference

| Type | Module | Base | When |
|---|---|---|---|
| `ConfigError` | `issuesmith.config` | `ValueError` | Invalid `issuesmith.yaml` or gate `requires` chain |
| `QueueValidationError` | `issuesmith.queue_store` | `ValueError` | Invalid queue request payload |
| `GateBuildError` | `issuesmith.gates` | `ValueError` | Gate cannot be instantiated (e.g. missing worktree) |
| `MainHealthError` | `issuesmith.observe.main_health` | `RuntimeError` | Base-branch health command failed |
| `GateMaterializationError` | `issuesmith.ac_contract` | `RuntimeError` | Gate root worktree could not be materialized |
| `WorktreeError` | `issuesmith.worktree` | `Exception` | Worktree creation or validation failed |
| `RetrySignal` | `issuesmith.engine` | `RuntimeError` | Engine deferred for quota/rate-limit retry (not a failure) |
| `DerivedAllowPathsError` | `issuesmith.pr_scope` | `ValueError` | Derived allow_paths could not be computed from test output |
| `MergeStateTimeoutError` | `issuesmith.merge` | `Exception` | PR merge state polling timed out |

`TemplateVariableError` (`ghdag.pipeline.order`) may propagate from `issuesmith.engine` when a template variable is missing at render time.

## License

MIT (SPDX `MIT`). See [LICENSE](./LICENSE).
