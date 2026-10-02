# issuesmith

issuesmith is a GitHub Issue label-driven workflow toolkit that runs on [ghdag](https://github.com/sumipan/ghdag). ghdag owns DAG execution, polling, and label transitions; issuesmith supplies Issue-domain gates, queue triage, context hooks, and a unified CLI that templates invoke.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-v0.98.0-blue)
![ci](https://github.com/sumipan/issuesmith/actions/workflows/ci.yml/badge.svg?branch=main)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Current release is **v0.98.0** (pre-1.0). Public interfaces may change before `1.0.0`.

## Installation

```bash
pip install "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.98.0"
```

With ghdag (required for `gate-preflight`, dispatch, and most runtime paths):

```bash
pip install "issuesmith[ghdag] @ git+https://github.com/sumipan/issuesmith.git@v0.98.0"
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
      claude: claude-sonnet-4-6
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
```

Common entry points: `python3 -m issuesmith doctor`, `python3 -m issuesmith gate-preflight --gate cp1 --body-file body.md`, `python3 -m issuesmith resume <issue> --from <step>`, and `python3 -m issuesmith config show`.

## CLI Reference

Entry points: `issuesmith` / `python3 -m issuesmith`.

Top-level commands are registered in `issuesmith.cli._HANDLERS`. `andon` and `labels` are dispatched from `issuesmith.__main__` before the main handler table.

| Command | Description |
|---|---|
| `context` / `context_hook` / `context-hook` | Generate ghdag context for impl/merge handlers |
| `gate` / `gate-preflight` | Run a named gate (`gate cp1 --body-file ...` aliases `gate-preflight`) |
| `cp1-gate` / `m2-gate` | Direct CP1 / M2 gate entry points |
| `verify` / `b1-verify` | B1 verification (`verify b1 ...`) |
| `queue` | Night / draft queue tick and status (`draft`, `sub`, `develop`, `merge` phases) |
| `deps` | Extract Issue dependencies |
| `tier` | Choose B1 / CP2 model tier (`tier b1` / `tier cp2`) |
| `comments` | Pipeline comment helpers |
| `gh` | GitHub Issue/PR helpers via ghdag client (not the `gh` CLI) |
| `engine` | LLM role switcher / runner (see subcommands below) |
| `dispatch` | Render and enqueue a workflow template |
| `publish` | Publish / version-bump orchestration |
| `labels reconcile [--fix] [--json]` | Report (or fix) managed-label divergences |
| `doctor` | Preflight / environment checks |
| `smoke` | Template smoke against live Issue bodies |
| `gen-live` | Generate live dispatch payloads |
| `version-bump` | Deterministic package version bump |
| `resume <issue> --from <step>` / `--phase <phase>` | Resume a workflow from a step or phase |
| `recover` | Deprecated; use `resume --from <step>` instead |
| `redispatch` | Deprecated; use `resume --phase <phase>` instead |
| `convert-to-milestone` | Convert an Issue into a milestone chain |
| `milestone` | Milestone chain status / resume (`milestone status <parent>`, `milestone resume <parent>`) |
| `config show` | Print resolved `issuesmith.yaml` as JSON and validate `steps.*.requires` |
| `observe [--apply] [--json]` | Collect observe events; `--apply` executes policy actions |
| `main-health` | Run `observe.main_health.command` on the latest base branch and write state |
| `apply` / `ingest-review` | Moved to host `tools/stash/`; exits 2 |

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

### `andon` subcommands (`python3 -m issuesmith andon ...`)

| Subcommand | Description |
|---|---|
| `andon list [--all] [--json]` | List open (unanswered) andons |
| `andon show <id>` | Show a specific andon |
| `andon answer <id> <action>` | Post answer, remove label, call resume hook |
| `andon note <id> --key <k> --value <v>` | Record a note on an open andon (no label change) |

## Public API

Top-level `issuesmith.__all__` is empty; import modules directly.

| Symbol | Module | Notes |
|---|---|---|
| `get_config` / `load_config` / `reset_config_cache` | `issuesmith.config` | Load and cache `issuesmith.yaml` |
| `ConfigError` | `issuesmith.config` | Invalid configuration |
| `QueueStore` / `QueueValidationError` | `issuesmith.queue_store` | Persistent queue state |
| `parse_issue_metadata` / `validate_issue_metadata` | `issuesmith.context_hook` | Issue YAML metadata |
| `MetadataViolation` | `issuesmith.context_hook` | Dataclass (not an exception) for metadata violations |
| `run_checks` / `extract_key_path_values` | `issuesmith.ac_contract` | Acceptance-criteria contract DSL |
| `GATE_REGISTRY` / `Verdict` / `GateBuildError` | `issuesmith.gates` | Unified gate registry |
| `main` | `issuesmith.cli` | CLI entry point |

`TemplateVariableError` is defined in `ghdag.pipeline.order` and may be raised by `issuesmith.engine` when template variables are missing.

## Architecture

Orchestration (polling, DAG construction, label transitions, idempotency) lives in **ghdag** `WorkflowDispatcher`. issuesmith provides Issue-domain tools, gates, and steps that workflow templates call.

| Module | Role |
|---|---|
| `issuesmith/__init__.py` | Package docstring; empty `__all__` |
| `issuesmith/__main__.py` | Dispatches `andon` / `labels` before `cli.main` |
| `issuesmith/ac_contract.py` | Acceptance-criteria contract DSL helpers |
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
| `issuesmith/github_api.py` | Issue API wrappers |
| `issuesmith/m2_gate.py` | M2 gate CLI entry |
| `issuesmith/milestone.py` | Milestone chain status and resume |
| `issuesmith/pipeline_comments.py` | Pipeline comment helpers |
| `issuesmith/pr_scope.py` | PR diff scope helpers |
| `issuesmith/queue.py` | Queue dispatch loop |
| `issuesmith/queue_store.py` | Persistent queue state |
| `issuesmith/queue_triage.py` | LLM triage / title normalization |
| `issuesmith/quota_gate.py` | Quota gate state |
| `issuesmith/recovery.py` | Deprecated recover/redispatch implementation |
| `issuesmith/repair.py` | Repair-step helpers |
| `issuesmith/resume.py` | Resume workflow from step or phase |
| `issuesmith/targets.py` | Target repository resolution |
| `issuesmith/template_ids.py` | Template id constants |
| `issuesmith/gate_rules/__init__.py` | Re-exports ghdag gate types |
| `issuesmith/gate_rules/b1_ac_format.py` | B1 acceptance-criteria format rules |
| `issuesmith/gate_rules/b1_migration.py` | B1 migration plan rules |
| `issuesmith/gate_rules/b1_milestone_subdesign.py` | B1 milestone sub-design rules |
| `issuesmith/gate_rules/cp1.py` | CP1 design gate rules |
| `issuesmith/gate_rules/m2.py` | M2 merge gate rules |
| `issuesmith/gate_rules/milestone_consistency.py` | Milestone consistency rules |
| `issuesmith/gate_rules/scope_breadth.py` | Scope breadth (pre-LLM) rules |
| `issuesmith/gate_rules/scope_coupling.py` | Scope coupling rules |
| `issuesmith/gate_rules/scope_size.py` | Issue size rules |
| `issuesmith/gates/__init__.py` | Unified `GATE_REGISTRY` and `Verdict` |
| `issuesmith/gates/base.py` | Shared gate base types |
| `issuesmith/gates/dep.py` | Dependency gate (`DepsGate`) |
| `issuesmith/gates/m1.py` | M1 version-behind-base gate |
| `issuesmith/gates/m2.py` | M2 merge checks |
| `issuesmith/gates/pr_scope.py` | PR scope gate |
| `issuesmith/gates/scope.py` | Scope gate |
| `issuesmith/gates/worktree.py` | Lint, tests, external_leak, base_freshness gates |
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
| `issuesmith/ops/publish.py` | Publish / version-bump orchestration |
| `issuesmith/ops/smoke.py` | Template smoke tests |
| `issuesmith/ops/version_bump.py` | Deterministic version bump |
| `issuesmith/steps/__init__.py` | Steps package |
| `issuesmith/steps/base.py` | Step base types |
| `issuesmith/steps/m1_merge.py` | M1 merge step |
| `issuesmith/steps/m2_finalize.py` | M2 finalize step |
| `issuesmith/steps/p0_worktree.py` | P0 worktree creation |
| `issuesmith/steps/repair.py` | Repair step wrapper |
| `issuesmith/steps/scope_gate.py` | Scope root resolver for P0/CP1 |
| `issuesmith/steps/sub1_create.py` | Sub-issue creation step |
| `issuesmith/verbs/__init__.py` | Workflow verb package |
| `issuesmith/verbs/finalize.py` | Finalize verb |
| `issuesmith/verbs/merge.py` | Merge verb |
| `issuesmith/verbs/publish.py` | Publish verb |
| `issuesmith/verbs/worktree.py` | Worktree verb |

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
| `sections` | Japanese section headings | Issue body section name map |
| `sub_design_subsections` | fixed tuple | Required sub-design subsections |
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

Nested `observe.main_health` keys: `worktree` (required when enabled), `command`, `base_branch` (`main`), `timeout_seconds` (`1800`).

## Error Reference

| Type | Module | Base | When |
|---|---|---|---|
| `ConfigError` | `issuesmith.config` | `ValueError` | Invalid `issuesmith.yaml` or gate `requires` chain |
| `QueueValidationError` | `issuesmith.queue_store` | `ValueError` | Invalid queue request payload |
| `GateBuildError` | `issuesmith.gates` | `ValueError` | Gate cannot be instantiated (e.g. missing worktree) |
| `MainHealthError` | `issuesmith.observe.main_health` | `RuntimeError` | Base-branch health command failed |
| `GateMaterializationError` | `issuesmith.steps.m2_finalize` | `RuntimeError` | M2 gate materialization failed |
| `WorktreeError` | `issuesmith.steps.p0_worktree` | `Exception` | P0 worktree creation failed |

`TemplateVariableError` (`ghdag.pipeline.order`) may propagate from `issuesmith.engine` when a template variable is missing at render time.

## License

MIT. See [LICENSE](./LICENSE).
