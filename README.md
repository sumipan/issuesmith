# issuesmith

issuesmith is a label-driven GitHub Issue workflow framework built on [ghdag](https://github.com/sumipan/ghdag). ghdag runs the DAGs and polls labels; issuesmith supplies the Issue side of that loop: deterministic design and merge gates, a request queue with triage, lane planning, LLM role switching, label projection, andons (typed stop-the-line signals), and the single CLI that workflow templates call.

Unlike a generic task runner or an LLM agent harness, issuesmith never calls an LLM API itself and never schedules work: every gate is a pure function of the Issue body, its labels and a git worktree, and every LLM step runs through an external engine CLI that ghdag dispatches.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-0.157.1-blue)
![ci](https://github.com/sumipan/issuesmith/actions/workflows/ci.yml/badge.svg?branch=main)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

The current release is **0.157.1** (`pyproject.toml`, pre-1.0). Public interfaces may change in any `0.Y.0` release; see [Public API Stability](#public-api-stability) and [CHANGELOG.md](./CHANGELOG.md).

## What it is not

| issuesmith is not | Use instead |
|---|---|
| A DAG scheduler or label poller | ghdag `WorkflowDispatcher` |
| An LLM client | The engine CLIs (`claude`, `codex`, `cursor`) that `issuesmith engine` launches |
| A GitHub client | ghdag's forge layer (`ghdag.forge.get_forge`), which issuesmith wraps |
| A general-purpose Issue linter | Its gates read the Issue-body sections declared by the active language pack |

## Installation

```bash
pip install "issuesmith[ghdag] @ git+https://github.com/sumipan/issuesmith.git@v0.157.1"
```

Almost every command imports ghdag (gates, queue, observe, andon, lanes, publish, preflight). Install the `ghdag` extra unless another package already provides ghdag at the pinned version. The base package alone is:

```bash
pip install "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.157.1"
```

| Item | Value |
|---|---|
| Python | `>=3.10` (classifiers: 3.10, 3.11, 3.12) |
| Runtime dependencies | `pyyaml`, `ruamel.yaml`, `packaging`, `python-dotenv` |
| `[ghdag]` extra | `ghdag @ git+https://github.com/sumipan/ghdag.git@v0.101.2` |
| `[dev]` extra | `pytest>=7.0.0`, `pytest-cov>=4.0.0`, `mypy>=1.10.0`, `ruff>=0.4.0`, ghdag `v0.101.2` |
| Console script | `issuesmith` (`issuesmith.cli:main`) |
| Build backend | setuptools, `src/` layout, ships `py.typed` |

## Quick Start

The commands below are the ones `tests/test_cli.py` runs as subprocesses.

1. Write a minimal `issuesmith.yaml`. `repo` and `phases` are required; `phases[].name`, `role`, `entry_step` and `handler` are required per phase. Everything else has a default.

```yaml
repo: example/cli
phases:
  - name: draft
    role: design
    entry_step: b1
    handler: brushup
```

2. Point the CLI at it and inspect the resolved configuration and engine assignments:

```bash
export ISSUESMITH_CONFIG=/path/to/issuesmith.yaml   # optional when the file is in cwd or a parent
python3 -m issuesmith config show
python3 -m issuesmith engine show
```

3. Run a gate against an Issue body file. The `gate <name>` form is equivalent to `gate-preflight --gate <name>`:

```bash
printf '## Acceptance Criteria\n\n```yaml\npaths_must_exist: []\n```\n' > body.md
python3 -m issuesmith gate cp1 --body-file body.md
python3 -m issuesmith gate-preflight --gate cp1 --body-file body.md   # same JSON output
```

Both forms print one JSON list of violations (`rule_id`, `severity`, `message`, `location`, `auto_fixable`, `fix_hint`) and exit 0; the list is empty when the gate passes.

Day-to-day operation against a live repository uses `queue enqueue` / `queue tick`, `resume`, `andon list`, `observe --json`, `lanes check --json` and `metrics rework`.

## CLI Reference

Run a command as `python3 -m issuesmith <command>` or through the `issuesmith` console script. The `andon` command is handled by `issuesmith.__main__` before the shared handler table, so it is available only through `python3 -m issuesmith`.

### Top-level commands

| Command | Arguments | Implemented by | What it does |
|---|---|---|---|
| `context`, `context_hook`, `context-hook` | `<issue_number>` | `context_hook.main` | Print the ghdag context JSON for an Issue (YAML metadata, worktree paths, template variables) |
| `gate` | `<gate_id> --body-file FILE`, `cp1 <issue>`, `m2 <issue> [--repo-roots REPO=PATH ...]` | `cli._cmd_gate` | Run one gate: body-file form via ghdag's gate runner, Issue form via `cp1_gate` / `m2_gate` |
| `gate-preflight` | `--gate <gate_id> --body-file FILE` | `ghdag.workflow.gates.__main__` with `issuesmith.gate_rules` registered | Evaluate a registered gate rule on a body file |
| `cp1-gate` | `<issue_number>` | `cp1_gate.main` | CP1 pattern gate on a live Issue; may autofix `allow_paths` |
| `m2-gate` | `<issue_number> [--repo-roots REPO=PATH ...]` | `m2_gate.main` | M2 acceptance-criteria checkbox and contract gate |
| `verify` | `b1 <issue_number> [--prev-report FILE] [--no-apply-deterministic] [--dry-run]` | `b1_verify.main` | Deterministic verify of B1 output with optional recovery helpers |
| `b1-verify` | same as `verify b1` | `b1_verify.main` | Alias |
| `queue` | see [queue](#queue-subcommands) | `queue.main` | Request queue: enqueue, tick, status, audit, migrate |
| `deps` | `check <issue_number>` | `dep_extractor.main` | Extract and verify the Issue's dependency section |
| `tier` | `b1 --issue N \| --body-file FILE`, `cp2 --diff-lines N --unchecked-ac N --p2-all-pass BOOL` | `b1_tier.main`, `cp2_tier.main` | Decide the `light` / `heavy` tier for B1 or CP2 |
| `comments` | JSON `{body, comments}` on stdin | `pipeline_comments.main` | Drop pipeline-generated comments before an LLM reads them |
| `gh` | ghdag `github_cli` arguments | `github_api.main` | ghdag GitHub CLI wrapper that refuses direct `*-ready` label writes and cross-repo `issue create` |
| `engine` | see [engine](#engine-subcommands) | `engine.main` | Show, check and switch LLM roles; run templates through an engine |
| `dispatch` | `<step_id> [KEY=VALUE ...]` | `ops.dispatch.main` | Run a step module or shell step body with requires / repair / andon handling |
| `publish` | `--issue N --branch B --base BASE --worktree DIR --repo R --issue-repo R [--allow-paths LIST] [--target-count N]` | `ops.publish.main` | Deterministic push, version bump and PR creation |
| `labels` | `reconcile [--fix] [--json]` | `ops.labels.reconcile` | Report or fix managed-label divergence on open Issues |
| `doctor` | none | `ops.preflight.main` | Runtime environment check (ghdag import, upstream APIs, skills manifests, requires chain, `advance_when`) |
| `smoke` | `[issue ...] [--workflow NAME]` | `ops.smoke.main` | Static and live smoke test of workflow templates |
| `gen-live` | `[--check]` | `ops.gen_live_dispatch.main` | Generate shell-step trampoline templates, or check drift |
| `version-bump` | `--worktree DIR --base BRANCH` | `ops.version_bump.main` | Deterministic `pyproject.toml` version bump and CHANGELOG fold |
| `resume` | `<issue> --from STEP \| --phase PHASE [--workflow W] [--handler H] [--mark-done STEP ...] [--force]` | `resume.resume` | Release in-flight state, then recover from a DAG step or redispatch a phase |
| `recover` | `<issue> [--from STEP] [--dry-run] [--json]` | `recovery.main` | Deprecated alias of `resume --from` |
| `redispatch` | `<issue> --phase PHASE [--reason R] [--dry-run]` | `recovery.main` | Deprecated alias of `resume --phase` |
| `convert-to-milestone` | `<issue> [--dry-run]` | `convert_to_milestone.main` | Move an Issue from the develop path back to the milestone path |
| `milestone` | `status <parent>`, `resume <parent>`, `prune [--dry-run]`, `consolidate <child> --into <sibling>` | `milestone.main` | Milestone chain operations |
| `config` | `show` | `cli._cmd_config_show` | Dump the resolved `IssuesmithConfig` as JSON and validate step `requires` |
| `observe` | `[--apply] [--json]` | `observe.observe`, `observe.policy` | Collect pipeline events; with `--apply` execute policy actions |
| `metrics` | `rework [--since YYYY-MM-DD] [--issue N] [--audit PATH] [--json]` | `metrics_rework.main` | Aggregate rework metrics from the metrics JSONL |
| `main-health` | none | `observe.main_health.check` | Run the configured base-branch health command and persist its state |
| `lanes` | `check [--apply] [--json]`, `report [--json]` | `lanes` | Lane ledger planning and patrol report |
| `andon` | `list [--all] [--json]`, `show <id>`, `answer <id> <action>`, `note <id> --key K --value V`, `auto-answer [--apply] [--json]` | `andon` | List, inspect, answer and annotate open andons |
| `apply`, `ingest-review` | any | `cli._cmd_stash_moved` | Exit 2: these tools moved out of the package |

### `queue` subcommands

Global options: `--queue-path`, `--state-path`, `--lock-path`.

| Subcommand | Options | Purpose |
|---|---|---|
| `enqueue` | `--issue N --phase P --source S --actor-kind human\|automation --priority high\|normal\|low --requested-by WHO [--force] [--after N ...]` | Add a request; `--phase` choices come from `phases[].name` |
| `tick` | `[--seed PATH]` | Dispatch the next eligible request(s) |
| `status` | none | Show queue, in-flight and wait reasons |
| `doctor` | none | Check for untracked in-flight Issues |
| `reset` | `[--keep-last-issue]` | Clear the halt flag |
| `skip` | `--issue N [--reason R]` | Skip an Issue |
| `dequeue` | `--request-id ID [--reason R]` | Remove a request |
| `audit` | `[--offline]` | Store integrity and GitHub in-flight audit |
| `triage-log` | `[--last N] [--path PATH]` | Show recent triage decisions |
| `migrate` | `[--from-night-queue-state PATH] [--seed PATH] [--dry-run]` | Import a legacy night-queue state |
| `release` | `--issue N` | Release a design-slot in-flight entry (recovery only) |

### `engine` subcommands

| Subcommand | Arguments | Purpose |
|---|---|---|
| `show` | none | Print the state source and the engine / model per role |
| `check` | none | Validate commands, models and workflow drift; prints `ACCEPTED` or `REJECTED:<reason>` |
| `switch` | `<role> <engine> [--model M] [--light-model M]` | Switch one role's engine |
| `resolve` | `<role> [--field engine\|model] [--tier light\|heavy]` | Resolve the effective engine / model |
| `run` | `<role> <template_path> [--cwd DIR] [--tier T] [VAR=VALUE ...]` | Render a template and run it through the role's engine |
| `exec` | `<role> <input_path> [--cwd DIR] [--tier T] [--engine E]` | Run a prepared prompt file |
| `run-guarded` | `<role> <template_path> --failure-status S [--success S ...] [--emit-status S] [--requires-step STEP] ...` | Run with status guard and the step's `requires` gates |
| `run-verified` | `<role> <template_path> --failure-status S --verify CMD --recover TEMPLATE [--max-loops N] [--recover-tier T] [--skip-verify-on S ...] ...` | Verify, recover and re-verify loop |

Roles and tiers come from configuration: roles are the keys of `engines` (`design` and `implementation` by default), tiers are `light` and `heavy`.

## Public API

Everything listed here is exported through a module-level `__all__`. The package root `issuesmith.__all__` is empty: import from the module that owns the name.

### `issuesmith.gates`

| Name | Kind | Role |
|---|---|---|
| `Verdict` | dataclass | `passed` flag plus `reasons` |
| `GateBuildContext` | dataclass | Worktree path, `allow_paths`, base branch, derived paths, step id and variables passed to a gate factory |
| `GateBuildError` | exception | Gate cannot be instantiated from the context |
| `GateEntry` | dataclass | Registry entry: `input_kind`, `build`, `pre_llm`, `repairable` |
| `RequiresGate` | Protocol | `check(body, labels) -> list` |
| `GATE_REGISTRY` | dict | Gate id to `GateEntry` |
| `resolve_gate` | function | Resolve a registry id or `module.path:attr` to a `GateEntry` |
| `validate_step_requires` | function | Validate `steps.<id>.requires` / `accepts` against the registry |
| `check_scope`, `check_pr_scope`, `check_m2`, `check_deps` | functions | Verdict-returning wrappers around the scope, PR scope, M2 and dependency gates |

Submodules: `gates.base` (`ContractInput`, `Gate`), `gates.scope` (`check_scope`, `ScopeGate`), `gates.pr_scope` (`check_pr_scope`, `PrScopeGate`), `gates.m2` (`check_m2`), `gates.dep` (`check_deps`, `DepsGate`, `dependents_of`, `on_dep_merge_done`), `gates.ac_contract` (`AcContractGate`), `gates.m1` (`RULE_ID`, `VersionBehindBaseGate`), `gates.worktree` (`changed_files`, `derive_test_allow_paths`, `derive_ledger_allow_paths`, `check_derived_test_guard`, `LintGate`, `TestsGate`, `ExternalLeakGate`, `line_has_cjk`, `cjk_added_lines`, `external_target_state`, `is_external_target`, `BaseFreshnessGate`, `ImporterImportGate`, `WORKTREE_GATES`, `_run_pytest`, `_parse_failed_ids`, `_func_id`, `_baseline_failed_func_ids`).

### Gate helpers

| Module | Exported names |
|---|---|
| `issuesmith.scope_gate` | `ScopeMeasure`, `ScopeVerdict`, `evaluate`, `format_comment`, `measure_scope`, `override_from_metadata`, `parse_allow_paths_from_ctx`, `record_p0_trip_metric`, `resolve_scope_root` |
| `issuesmith.pr_scope` | `DEFAULT_FORBIDDEN_PR_PATHS`, `DIFF_LINES_FALLBACK`, `DerivedAllowPathsError`, `allow_paths_from_issue_body`, `check_pr_diff_scope`, `check_pr_scope_with_derived`, `derived_allow_paths_from_result`, `filenames_from_pr_files`, `find_pr_for_branch`, `normalize_allow_path`, `pr_diff_lines`, `unchecked_ac_count` |
| `issuesmith.m2_gate` | `has_acceptance_criteria_section`, `get_unchecked_count`, `check_gate`, `check_gate_multi_root`, `synthesize_contract_failures`, `main` |
| `issuesmith.repair` | `RequiresResult`, `evaluate_requires`, `apply_auto_fixes`, `record_metrics` |
| `issuesmith.body_editor` | `count_heading`, `filter_section_by_paths`, `get_section`, `get_section_by_keyword`, `get_subsections`, `normalize_sub_headers`, `relocate_sub_plan`, `apply_milestone_normalizers`, `replace_allow_paths`, `split_h2_sections`, `upsert_section` |

### Git, merge and publish

| Module | Exported names |
|---|---|
| `issuesmith.worktree` | `WorktreeError`, `assert_jobs_clean`, `clone_if_missing`, `ensure_base_included`, `fetch_base_or_raise`, `fetch_base_with_retry`, `github_client`, `handle_milestone`, `prepare_cross_repo_worktree`, `prepare_local_worktree`, `prepare_worktree`, `require_yaml_metadata`, `resolve_base_ref`, `validate_branch` |
| `issuesmith.merge` | `CompanionReadyResult`, `LocalMergeVerifyResult`, `MergeStateInfo`, `MergeStateTimeoutError`, `PostMergeTestResult`, `PrSearchResult`, `check_companion_ready`, `find_companion_pr`, `find_pr`, `get_merge_state`, `graphql_merge_state`, `is_already_merged`, `list_pulls`, `run_post_merge_pytest`, `verify_local_merge`, `wait_merge_state` |
| `issuesmith.ops.publish` | `PublishResult`, `publish`, `run_version_bump`, `main`, `_bump_versions_in_range`, `_run_version_bump` |
| `issuesmith.verbs` | `prepare_worktree`, `publish_branch`, `find_pr`, `merge_state`, `merge_pr`, `cleanup_worktrees`, `cleanup_branches`, `close_issue` |

`issuesmith.verbs` is the stable, step-independent surface: each verb is a thin extraction from `worktree`, `ops.publish`, `merge` and the finalize step, with the same names re-exported by `verbs.worktree`, `verbs.publish`, `verbs.merge` and `verbs.finalize`.

### Metrics

| Module | Exported names |
|---|---|
| `issuesmith.metrics_events` | `append_event`, `record_step_started` |
| `issuesmith.metrics_rework` | `compute`, `format_text`, `load_jsonl`, `main`, `parse_since`, `resolve_cause_target` |

### Gate ids

`GATE_REGISTRY` is built at import time from three sources. These ids are valid in `steps.<id>.requires` and `steps.<id>.accepts`.

| Gate id | Source | Input kind | Notes |
|---|---|---|---|
| `deps` | `gates.dep.DepsGate` | issue | Dependency Issues must be terminal |
| `deps_format` | `gate_rules.deps_format` | issue | Unparsed `#N` references in the dependencies section |
| `lint`, `tests`, `external_leak`, `base_freshness`, `importer_import` | `gates.worktree.WORKTREE_GATES` | worktree | `base_freshness` runs before the LLM (`pre_llm`) |
| `scope` | `gates.scope.ScopeGate` | worktree | `allow_paths` size limits (`scope_gate` config) |
| `pr_scope` | `gates.pr_scope.PrScopeGate` | worktree | Diff must stay inside `allow_paths` and outside `forbidden_pr_paths` |
| `ac_contract` | `gates.ac_contract.AcContractGate` | worktree | `paths_must_exist` / `paths_must_not_exist` contract |
| `allow_paths_forbidden`, `scope_breadth` | `gate_rules` | issue | `pre_llm`, not repairable |
| `b1_ac_format`, `b1_migration`, `b1_milestone_subdesign`, `cp1`, `m2`, `milestone_consistency`, `pin_bump`, `scope_coupling`, `scope_size` | `gate_rules` | issue | Issue-body rules registered in ghdag's `GATE_REGISTRY` |
| `review` | `gates.review.build_review_gate` | worktree | Step-bound LLM review; requires `steps.<id>.review` |

Phase `advance_when` predicates registered in `issuesmith.preconditions.PRECONDITION_REGISTRY`: `deps_terminal`, `closing_pr_exists`, `pins_landed`.

## Architecture

```text
src/issuesmith/
├── __init__.py            package docstring; __all__ is empty
├── __main__.py            python -m issuesmith entry; routes andon / labels, then cli.main
├── cli.py                 unified CLI handler table (_HANDLERS)
├── config.py              issuesmith.yaml loader and frozen config dataclasses
├── language.py            LanguagePack: Issue-body vocabulary and GitHub-posted text (EN built in)
├── contract.py            StepContext / StepResult / Andon / Verdict and Issue-body extractors
├── context_hook.py        ghdag context_hook: Issue YAML metadata to template variables
├── engine.py              LLM role switcher and runner (show / check / switch / run-*)
├── andon.py               andon model, comment serialisation, list / answer / auto-answer
├── queue.py               queue CLI: enqueue, tick, status, dispatch
├── queue_store.py         JSONL request store, state file and exclusive lock
├── queue_triage.py        deterministic gates and LLM triage for queued requests
├── lanes.py               lane ledger planning, capacity and patrol report
├── quota_gate.py          GitHub API rate-limit brake state
├── resume.py              central resume entry (--from step / --phase)
├── recovery.py            legacy recover / redispatch planning (deprecated CLI)
├── convert_to_milestone.py move an Issue back to the milestone path
├── milestone.py           milestone chain automation and sub-issue split plans
├── projection.py          pure projection: issue state to managed label set
├── preconditions.py       phase advance predicates and PRECONDITION_REGISTRY
├── pins.py                requires_pins reading and the pins_landed predicate
├── targets.py             multi-target Issue model (host and companion repositories)
├── dep_extractor.py       dependency extraction and verification
├── ac_contract.py         acceptance-criteria YAML contract extraction and execution
├── scope_gate.py          allow_paths scope measurement
├── pr_scope.py            PR diff scope check (allow_paths / forbidden_pr_paths)
├── cp1_gate.py            CP1 gate CLI wrapper with allow_paths autofix
├── m2_gate.py             M2 acceptance-criteria gate CLI wrapper
├── b1_tier.py             B1 tier decision (light / heavy)
├── cp2_tier.py            CP2 tier decision from diff size and AC completion
├── b1_verify.py           deterministic verify of B1 output
├── body_editor.py         Issue-body section editing (ghdag shim plus host extensions)
├── branch_reuse.py        locate and record reusable previous-generation branches
├── worktree.py            git worktree preparation helpers
├── merge.py               merge-state polling, local merge verification, companion PRs
├── repair.py              requires evaluation and repair orchestration
├── pipeline_comments.py   filter pipeline-generated Issue comments
├── github_api.py          guarded wrapper around ghdag's GitHub CLI
├── forge_api.py           typed raw REST entry point for forge clients
├── forge_guard.py         read-only label forge for label_write_guard
├── metrics_events.py      metrics JSONL event writers
├── metrics_rework.py      rework metrics aggregation
├── template_ids.py        string.Template identifier extraction (Python 3.10 compatible)
├── gate_rules/            Issue-body rules registered in ghdag GATE_REGISTRY
│   ├── __init__.py        imports every rule module (registration side effect)
│   ├── allow_paths_forbidden.py  allow_paths overlapping forbidden_pr_paths
│   ├── b1_ac_format.py    acceptance-criteria YAML block format
│   ├── b1_migration.py    migration-procedure sections for scope:migration Issues
│   ├── b1_milestone_subdesign.py  sub-issue split plan and per-sub design sections
│   ├── cp1.py             CP1 pattern checks and YAML contract validation
│   ├── deps_format.py     dependency section format
│   ├── m2.py              acceptance-criteria checkbox completion
│   ├── milestone_consistency.py  split plan without scope:milestone label
│   ├── pin_bump.py        reject dependency pin changes in feature Issues
│   ├── scope_breadth.py   allow_paths breadth limit
│   ├── scope_coupling.py  CP1/B1 scope coupling between source and tests
│   └── scope_size.py      Issue size limits from the change table
├── gates/                 unified gate API with Verdict
│   ├── __init__.py        GATE_REGISTRY, GateEntry, resolve_gate, validate_step_requires
│   ├── base.py            Gate protocol and ContractInput
│   ├── ac_contract.py     paths_must_exist / paths_must_not_exist on a worktree
│   ├── dep.py             dependency gate and dependents lookup
│   ├── m1.py              version_behind_base gate
│   ├── m2.py              M2 gate wrapper
│   ├── pr_scope.py        PR scope gate wrapper
│   ├── review.py          LLM review gate for requires evaluation
│   ├── scope.py           scope gate wrapper
│   └── worktree.py        lint, tests, external_leak, base_freshness, importer_import
├── observe/               pipeline observation layer
│   ├── __init__.py        observe() and github_api_status()
│   ├── dag_state.py       DAG liveness from exec.jsonl and markers
│   ├── events.py          typed observe events
│   ├── main_health.py     base-branch health check and state file
│   └── policy.py          evaluate events into actions and execute them
├── ops/                   operational commands
│   ├── __init__.py        package marker
│   ├── dispatch.py        step dispatcher with requires / repair / andon mapping
│   ├── doctor.py          requires-chain and advance_when validation reports
│   ├── gen_live_dispatch.py  shell-step trampoline template generator
│   ├── labels.py          phase / attention label projection and reconciliation
│   ├── preflight.py       runtime environment health check (doctor command)
│   ├── publish.py         push, version bump and PR creation
│   ├── repair_step.py     explicit repair step module
│   ├── smoke.py           workflow smoke test
│   └── version_bump.py    deterministic pyproject.toml version bump
└── verbs/                 public verb API extracted from step implementations
    ├── __init__.py        re-exports the verbs below
    ├── finalize.py        cleanup_worktrees, cleanup_branches, close_issue
    ├── merge.py           find_pr, merge_state, merge_pr
    ├── publish.py         publish_branch
    └── worktree.py        prepare_worktree
```

## Configuration

### Config file resolution

`issuesmith.config.load_config` resolves `issuesmith.yaml` in this order and caches the result for `get_config`:

1. An explicit `path` argument.
2. The `ISSUESMITH_CONFIG` environment variable.
3. `issuesmith.yaml` found by walking up from the current directory.
4. `issuesmith.yaml` at the installed package's repository root.

`repo` and `phases` are required; a missing `phases` key raises `ConfigError`. Relative paths resolve against the directory containing the config file.

### Environment variables

| Variable | Read by | Default | Effect |
|---|---|---|---|
| `ISSUESMITH_CONFIG` | `config`, `engine` | unset | Path to `issuesmith.yaml` (step 2 of resolution) |
| `ISSUESMITH_QUEUE_DIR` | `queue_store`, `queue` | unset | Directory for the queue JSONL, state, lock and triage log instead of `paths.*` |
| `ISSUESMITH_TIMEOUT_SEC` | `engine` | `engines.<role>.timeout_sec` | Per-run LLM timeout override when the engine state has none |
| `METRICS_JSONL_PATH` | `engine` | `paths.metrics` | Metrics JSONL destination override |
| `ISSUESMITH_ENGINE_WAIT_MAX_SEC` | `engine` | `21600` | Upper bound on waiting for a paused engine before rejecting |
| `ISSUESMITH_ENGINE_WAIT_POLL_SEC` | `engine` | `60` | Re-check period while every engine of a role is paused (1-60) |
| `ISSUESMITH_ENGINE_WAIT_INTERVAL_SEC` | `engine` | `60` | Compatibility alias read only when the poll variable is unset |
| `ISSUESMITH_PYTEST_TIMEOUT_SEC` | `gates.worktree` | `1500` | Timeout for the `tests` gate's pytest run (exit 124 on expiry) |
| `ISSUESMITH_REPAIR_ACTIVE` | `ops.dispatch`, `ops.repair_step` | unset | Set by the dispatcher while the repair step runs; a nested repair becomes an andon |
| `GHDAG_TASK_UUID` | `engine`, `metrics_events`, `ops.dispatch` | unset | Task id exported by ghdag; correlates metrics rows and deferred retries |
| `P0_FETCH_LOCK_WAIT` | `worktree` | `30` | Seconds to wait for the fetch lock during worktree preparation |
| `AGENT_SKILLS_DIR` | `ops.preflight` | `~/.agents/skills` | Directory whose `*/SKILL.md` manifests `doctor` validates |

`context_hook` also loads the nearest `.env` file through `python-dotenv` without overriding exported values, so forge credentials reach subprocesses started by ghdag.

### `issuesmith.yaml` keys

| Key | Type | Default | Purpose |
|---|---|---|---|
| `repo` | `owner/name` | required | Repository that hosts the Issues |
| `label_namespace` | string | `issuesmith` | Prefix of managed labels (`<ns>:<phase>-ready` / `-running` / `-done`) |
| `timezone` | IANA name | `Asia/Tokyo` | Timezone for reports and `--since` parsing |
| `supported_repos` | list | empty | Repositories allowed as cross-repo targets |
| `paths` | mapping | see below | Queue, state, metrics, worktree and template locations |
| `engines.<role>` | mapping | `design`, `implementation` | `allowed`, `default_model`, `light_model`, `timeout_sec`, `pause_ttl_sec` |
| `concurrency` | mapping | `default: 1` | `default`, `per_engine`, `strict_order`, `max_dispatch_per_tick` |
| `milestone_chain` | mapping | disabled | `enabled`, `child_priority`, `auto_develop`, `auto_close_parent` |
| `triage` | mapping | enabled | LLM triage `engine`, `model`, `timeout`, `body_chars`, circuit breaker |
| `phases[]` | list | required | `name`, `role`, `entry_step`, `handler`, `preconditions`, `excludes`, `writes_files`, `advance_when`, `steps` |
| `steps.<id>` | mapping | `p1` default | `module`, `template`, `requires`, `input_kind`, `accepts`, `andon_when`, `repair` (`max`, `push`), `review` |
| `forbidden_pr_paths` | list | `jobs/**`, `logs/**`, `.sessions/**`, `*.jsonl`, `*.pid`, `*.lock` | Paths a PR diff may never touch |
| `forbidden_pr_paths_except` | list | empty | Exceptions to the list above |
| `scope_gate` | mapping | enabled | `max_files`, `max_lines`, `hard_max_files` |
| `scope_coupling` | mapping | enabled | `search_dirs`, `data_file_tests` |
| `scope_size` | mapping | enabled | `max_files`, `max_concerns`, `max_slices`, `delete_with_new`, `exclude_prefixes`, `atomic_phrases` |
| `tests` | mapping | `flaky_reruns: 2` | Pytest gate reruns |
| `metrics` | mapping | `done_step: m2` | `repair_templates`, `cause_targets` |
| `derived_allow` | mapping | enabled | `ledger_globs` for derived test allow_paths |
| `external_leak` | mapping | off | `cjk_free_external_targets` |
| `terminal_labels` | list | empty | Labels that count a dependency as finished |
| `terminal_without_merge` | list | empty | Terminal labels that do not imply a merge |
| `observe` | mapping | see dataclass | `stall_minutes`, `task_timeout_minutes`, `systemic_*`, `forge_max_consecutive_errors`, `max_api_calls`, `main_health`, `workflows` |
| `api_brake` | mapping | disabled | `enabled`, `min_remaining` |
| `language_pack` | path | built-in English | YAML file loaded by `language.load_language_pack` |
| `label_write_guard` | `warn` or `enforce` | `warn` | `enforce` raises `LabelWriteForbidden` when a step writes labels |
| `installs` | mapping | empty | Package name to install directory, used by `pins_landed` and `pin_bump` |
| `andon.auto_answer[]` | list | empty | Rules with `name`, `match` (`step`, `kind`, `rule_id`), `action`, `max_per_issue` |
| `path_env_prefixes` | list | empty | Path prefixes treated as environment-provided in milestone validation |

`paths` keys and defaults, relative to the config file directory:

| Key | Default |
|---|---|
| `queue` | `jobs/issuesmith-queue.jsonl` |
| `queue_state` | `logs/issuesmith-queue-state.json` |
| `queue_lock` | `logs/issuesmith-queue.lock` |
| `triage_log` | `jobs/issuesmith-triage.jsonl` |
| `seed` | `configs/night-queue.yaml` |
| `night_state` | `logs/night-queue-state.json` |
| `exec_jsonl` | `jobs/exec.jsonl` |
| `done_dir` | `jobs/done` |
| `quota_state` | `jobs/quota-gate.json` |
| `metrics` | `jobs/metrics.jsonl` |
| `worktrees_dir` | `.claude/worktrees` |
| `external_dir` | `.claude/external` |
| `workflow` | `workflows/issuesmith.yml` |
| `template_dir` | `workflows/issuesmith` |
| `engine_state` | `.pipeline-state/issuesmith-engine.yml` |
| `brake_state` | falls back to `quota_state` |
| `lanes` | unset (required by `lanes`) |

### Other files the code reads

| File | Reader | Purpose |
|---|---|---|
| `paths.engine_state` (fallback `.issuesmith-engine` at the config root) | `engine` | Effective engine / model per role |
| `paths.lanes` and `<lanes>.state.json` | `lanes` | Lane ledger and last report state |
| `audit.jsonl` next to `paths.exec_jsonl` | `quota_gate`, `lanes`, `observe` | ghdag audit rows for the GitHub API brake |
| `paths.template_dir/<step>.md` | `ops.dispatch`, `ops.gen_live_dispatch`, `engine` | Step templates |
| `pyproject.toml` | `ops.preflight`, `ops.version_bump`, `gate_rules.pin_bump`, `pins` | Version and dependency pins |
| `.env` | `context_hook` | Credentials for forge access |

## Protocols and extension points

| Extension point | Protocol or hook | How to plug in |
|---|---|---|
| Issue-body gate rule | `GateRule` from `ghdag.workflow.gates` (`check(body, labels) -> list[Violation]`) | Define a class, register it in `GATE_REGISTRY` from `issuesmith.gate_rules`, import it in `gate_rules/__init__.py` |
| Worktree or external gate | `issuesmith.gates.RequiresGate`, optionally `issuesmith.gates.base.Gate` with a narrowing `fix()` | Expose a `GateEntry` and reference it as `module.path:attr` in `steps.<id>.requires` |
| Phase advance predicate | `issuesmith.preconditions.register(name, fn)` | Register at import time; reference the name in `phases[].advance_when` |
| Python step | module with `run(ctx: StepContext, step: StepConfig) -> StepResult` | Set `steps.<id>.module`; `dispatch` imports it and maps the `StepResult` |
| Language pack | `issuesmith.language.LanguagePack` | Point `language_pack` at a YAML file whose keys are the dataclass fields |
| Andon sink | `issuesmith.andon.AndonSink` (`emit(andon)`) | Pass sinks to `observe.policy.execute` |
| Raw forge calls | `issuesmith.forge_api.RawApiForge` via `api_request` | Any forge client exposing a raw request method |

## Public API Stability

issuesmith follows SemVer with the pre-1.0 convention: the major digit stays `0`, so `0.Y.0` may break public interfaces and `0.Y.Z` must not.

The bump is chosen deterministically by `ops.version_bump` on each publish. A `Y` bump is forced when the diff adds, removes or re-signs a public `def` or `class`, changes an `__all__` line, changes a `[project.scripts]` or entry-point key, or when a commit uses a Conventional Commits `!:` subject or a `BREAKING CHANGE:` footer. Everything else is a `Z` bump. Breaking changes are listed under **Removed** or marked **BREAKING** in [CHANGELOG.md](./CHANGELOG.md).

Names that start with an underscore are implementation details even when they appear in an `__all__` list.

## Deprecated API

| Deprecated | Enforced in | Replacement | Signal |
|---|---|---|---|
| `issuesmith recover` | `cli`, `recovery` | `issuesmith resume <issue> --from <step>` | `FutureWarning` |
| `issuesmith redispatch` | `cli`, `recovery` | `issuesmith resume <issue> --phase <phase>` | `FutureWarning` |
| `issuesmith apply`, `issuesmith ingest-review` | `cli` | External stash tools outside this package | Exit 2 with a pointer message |
| `m2-gate --repo-root` | `m2_gate` | `m2-gate --repo-roots REPO=PATH ...` | Marked deprecated in `--help` |
| `ISSUESMITH_ENGINE_WAIT_INTERVAL_SEC` | `engine` (since 0.42.0) | `ISSUESMITH_ENGINE_WAIT_POLL_SEC` | Read only when the poll variable is unset |
| `issuesmith.yaml` keys `sections`, `sub_design_subsections`, `scope_size.delete_words`, `scope_size.new_words`, `scope_size.sub_plan_header`, `scope_size.no_deps_word` | `config` (since 0.146.1) | `language_pack` file | `DeprecationWarning`; ignored when `language_pack` is set |
| Engine state file `.issuesmith-engine` at the config root | `engine` | `paths.engine_state` (default `.pipeline-state/issuesmith-engine.yml`) | Read only when the configured file is absent |

## Error Reference

All exceptions are importable from the module that defines them.

| Exception | Module | Base | Raised when |
|---|---|---|---|
| `ConfigError` | `issuesmith.config` | `ValueError` | `issuesmith.yaml` is invalid, a gate id or predicate is unknown, or a language pack cannot be loaded |
| `ContextHookError` | `issuesmith.context_hook` | `ValueError` | Issue YAML metadata fails security validation; carries `violations` |
| `GateBuildError` | `issuesmith.gates` | `ValueError` | A `GateEntry.build` cannot instantiate its gate (for example, no worktree in context) |
| `GateMaterializationError` | `issuesmith.ac_contract` | `RuntimeError` | The temporary base-branch worktree for the AC contract cannot be created |
| `LabelWriteForbidden` | `issuesmith.contract` | `RuntimeError` | A step writes labels while `label_write_guard` is `enforce` |
| `MainHealthError` | `issuesmith.observe.main_health` | `RuntimeError` | The base-branch health check cannot run; state is left unchanged |
| `MergeStateTimeoutError` | `issuesmith.merge` | `Exception` | `wait_merge_state` does not reach a non-blocked status before its timeout |
| `DerivedAllowPathsError` | `issuesmith.pr_scope` | `ValueError` | `derived_allow_paths_from_result` finds malformed entries |
| `QueueValidationError` | `issuesmith.queue_store` | `ValueError` | A queue request has invalid fields (CLI exit 2) |
| `RetrySignal` | `issuesmith.engine` | `RuntimeError` | A step must be deferred for retry; `dispatch` records the deferral instead of failing |
| `WorktreeError` | `issuesmith.worktree` | `Exception` | Deterministic worktree preparation failure |

## License

MIT (SPDX: `MIT`). See [LICENSE](./LICENSE).
