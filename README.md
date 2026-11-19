# issuesmith

issuesmith is a suite for building an agent-first software factory: a production line that takes a GitHub Issue from design through implementation, inspection, and merge. Python gates check what the LLM wrote; when progress cannot continue, typed andons stop the line and ask for input; after an answer, the run resumes and the Issue can reach merge. issuesmith is meant to be adopted as a whole suite, not as isolated pieces.

The suite includes design and merge gates, a request queue with triage, lane planning, LLM role switching, label projection, andons, and the single CLI that workflow templates call. [ghdag](https://github.com/sumipan/ghdag) runs the DAGs, polls labels, and moves Issues between phases; issuesmith supplies the Issue side of that loop.

**What it is not:** issuesmith is not a scheduler or a DAG runner (that is ghdag), and it does not call an LLM API directly. LLM steps run through the engine CLIs (`claude`, `codex`, `cursor`) that ghdag dispatches. Production hosts still own their wiring (`issuesmith.yaml`, `workflow.yml`); the installed package also ships a **reference develop line** (`issuesmith.lines.develop`) with wiring you extract into your host repo.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-v0.200.1-blue)
![ci](https://github.com/sumipan/issuesmith/actions/workflows/ci.yml/badge.svg?branch=main)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

The current release is **v0.200.1** (pre-1.0). Public interfaces can change in any `0.Y.0` release. Breaking changes are listed in [CHANGELOG.md](./CHANGELOG.md).

## Installation

```bash
pip install "issuesmith[ghdag] @ git+https://github.com/sumipan/issuesmith.git@v0.200.1"
```

The gates, forge access, `observe`, `andon`, `lanes` and `metrics` import ghdag, so install the `ghdag` extra unless another package already provides ghdag. The base package alone is:

```bash
pip install "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.200.1"
```

| Item | Value |
|---|---|
| Python | `>=3.10` (tested on 3.10, 3.11, 3.12) |
| Runtime dependencies | `pyyaml`, `ruamel.yaml`, `packaging`, `python-dotenv` |
| `[ghdag]` extra | same ghdag git tag as pinned in this repo's `pyproject.toml` (`[project.optional-dependencies].ghdag`) |
| `[dev]` extra | `pytest`, `pytest-cov`, `mypy`, `ruff`, plus the same ghdag pin as `[ghdag]` |
| Console script | `issuesmith` (`issuesmith.cli:main`) |

See [TESTING.md](./TESTING.md) for what tests should and should not pin.

## Quick Start

### Reference develop line (packaged)

The package bundles one minimal develop phase. Step modules live at `issuesmith.lines.develop.*`; wiring files live under `issuesmith.lines.develop.files` inside the wheel and are written to your host with `config extract-line`.

The host repository root does not have to be a checkout of the repository you change. Set `host_is_repo_checkout: false`, list every repository you target in `supported_repos`, and keep `paths.external_dir` pointing at where clones live (default `.claude/external`). Pipeline work then runs in a clone at `paths.external_dir/<repo-name>` and its `worktrees/` subtree; pull requests are opened on `target_repo`, while Issues on the configured `repo` are closed after merge.

| Piece | Role |
|---|---|
| `issuesmith.lines.develop.prepare` … `merge` | Step modules referenced from `steps.<id>.module` |
| `issuesmith.lines.develop.hooks` (`DevelopLineHooks`) | ghdag runner hooks: failure record on the Issue |
| `config extract-line develop <dir>` | Writes `issuesmith.yaml`, `workflows/issuesmith.yml`, templates, and prompts under `<dir>` |
| Extracted `workflows/issuesmith.yml` | ghdag workflow (`issuesmith:develop-ready` → prepare → implement → verify → review → merge) |
| Extracted `workflows/develop/templates/*.md` | Shell templates that call `python3 -m issuesmith dispatch …` |
| Extracted `workflows/develop/prompts/*.md` | LLM orders |

**Host setup (summary):**

1. Install issuesmith at the tag you pin (see [Installation](#installation)); no checkout of this repository is required for the reference modules.
2. From your host repository root, extract wiring once (fails if any target path already exists):

```bash
python3 -m issuesmith config extract-line develop .
```

3. Edit the extracted `issuesmith.yaml`: set `repo`, `supported_repos`, engines, and paths as needed. Step `module` values already point at `issuesmith.lines.develop.*`.

```yaml
steps:
  prepare:
    module: issuesmith.lines.develop.prepare
  implement:
    module: issuesmith.lines.develop.implement
  verify:
    module: issuesmith.lines.develop.verify
  review:
    module: issuesmith.lines.develop.review
  merge:
    module: issuesmith.lines.develop.merge
```

4. Run the watcher (from the host repository root):

```bash
mkdir -p jobs logs .pipeline-state
export ISSUESMITH_CONFIG=$PWD/issuesmith.yaml
python3 -m ghdag watch workflows --exec-jsonl jobs/exec.jsonl
python3 -m ghdag run jobs/exec.jsonl --hooks issuesmith.lines.develop.hooks
```

5. Label an Issue `issuesmith:develop-ready` with a leading YAML block that names the host repository, base branch, and change scope:

```yaml
target_repo: your-org/your-host
base_branch: main
allow_paths:
  - src/**
  - tests/**
```

`target_repo` must match an entry in `supported_repos`. Optionally add a `## Acceptance Criteria` section with a fenced `yaml` block (`paths_must_exist`, `paths_must_not_exist`, `greps_must_be_empty`) so the `ac_format` and `ac_contract` gates can enforce it. The line uses `python3 -m issuesmith context`, then packaged prepare through merge steps.

### Reference line gates by step

Each step’s `requires` gates are listed in the extracted `issuesmith.yaml`. Gate ids are defined in [Gates](#gates).

| Step | `requires` gates |
|---|---|
| `prepare` | `deps`, `design_lint`, `ac_format`, `scope_coupling`, `allow_paths_forbidden`, `scope_breadth` |
| `verify` | `base_freshness`, `lint`, `tests`, `pr_scope`, `ac_contract` |
| `review` | `review` (bound gate; see [Gates](#gates)) |

**When the line stops:** Issues labeled `scope:milestone` (split parents) stop at `prepare` with a `decision` andon before any worktree is created. Violations from issue-shaped gates on `prepare` (`design_lint`, `ac_format`, `scope_breadth`, and similar) are not auto-repaired. Dispatch raises an andon of kind `decision`; the summary looks like `non-repairable gate violation in step prepare: <message>`. Fix the Issue body, then `python3 -m issuesmith andon answer <id> resume` to continue from `prepare` (the worktree is kept). On `verify`, repairable gate failures run the repair step up to twice; if they persist, an andon `decision` opens (`pr_scope` adds `widen:<files>` to the options list). The line does not call `ensure_base_included` on `prepare`; catching up to the base branch is handled by the `base_freshness` gate on `verify` (including resume from `implement`).

Check the resolved config:

```bash
python3 -m issuesmith doctor
python3 -m issuesmith config show
```

For custom pipelines, define your own `steps.<id>.module` host modules (see [Protocols and extension points](#protocols-and-extension-points)). Day-to-day operation also uses `python3 -m issuesmith andon list`, `python3 -m issuesmith observe --json`, and `python3 -m issuesmith queue tick`.

## CLI Reference

Run a command as `python3 -m issuesmith <command>` or through the `issuesmith` console script. Both entry points use the same `issuesmith.cli._HANDLERS` table.

| Command | Description |
|---|---|
| `context` | ghdag context hook: Issue YAML metadata and step context |
| `gate <name> --body-file F` | Same as `gate-preflight --gate <name> --body-file F` |
| `gate design_lint <issue>` / `gate ac_complete <issue>` | Run the design-lint / AC-complete gate entry points on an Issue |
| `gate-preflight` | Rule-class gate runner with the issuesmith rules registered |
| `verify <step_id>` | Deterministic Verify for a step with `steps.<id>.verify: design_doc` (`VERIFY_RECOVERY:` markers; `--prev-report FILE` adds oscillation detection) |
| `queue <subcommand>` | Request queue (see [`queue`](#queue-subcommands)) |
| `deps` | Extract and check Issue dependencies |
| `tier <step_id>` | Pick light or heavy model tier when `steps.<id>.tier` is set (`design_doc` or `pr_diff`) |
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
| `andon <subcommand>` | Andon commands (see [`andon`](#andon-subcommands)) |
| `convert-to-milestone <issue> [--dry-run]` | Move an Issue that entered the develop path back to the milestone path |
| `milestone <subcommand>` | Milestone chain (see [`milestone`](#milestone-subcommands)) |
| `config show` | Print the resolved configuration as JSON and validate `steps.*.requires` (exit 1 when invalid) |
| `config extract-line <name> <dir>` | Write bundled reference-line wiring into `<dir>` (refuses when paths exist) |
| `observe [--apply] [--json]` | Collect observe events; `--apply` also runs the policy actions |
| `lanes <subcommand>` | Lane ledger planning (see [`lanes`](#lanes-subcommands)) |
| `metrics rework [--since YYYY-MM-DD] [--issue N] [--audit PATH] [--json]` | Rework report from `paths.metrics` and an optional ghdag audit JSONL; `--since` uses `timezone` |
| `main-health` | Run `observe.main_health.command` on the base branch; writes `issuesmith-main-health.json` and command log `issuesmith-main-health.log` next to queue state (exit 2 on `MainHealthError`) |

`paths.metrics` JSONL contracts (backward compatible additions):

- `requires_repair` may include optional `violations`: a list of `{"rule_id", "location", "message"}` objects (same shape as `serialize_violations`).
- `verify_check` is written by design-doc verify (`issuesmith verify <step> <issue>`): `event`, `step`, `issue`, `rule_ids`, and `violations` (empty lists on PASS). Rework aggregation ignores `verify_check` rows.
- `andon_answered` rows (from `andon.answer` / `andon answer`, `apply_auto_answers`, or `answer_if_open`) include `action` (the chosen action string) and `answered_by` (who answered). Vocabulary for `answered_by`:

  | Value | Meaning | Written by |
  |---|---|---|
  | `human` | A person | `answer` default / `andon answer` default |
  | `agent` | An agent | `answer(..., answered_by="agent")` / `andon answer ... --by agent` |
  | `rule:<name>` | An `andon.auto_answer` rule (`<name>` is the rule name) | `apply_auto_answers` |
  | `observe` | Observe auto-closed the andon when its condition cleared | `answer_if_open` only |

`version-bump` prints `VERSION_BUMP_TYPE` and `VERSION_BUMP_REASON`, then commits `chore: bump version to <new>`. It bumps Y (minor) when the diff adds or removes a public `def` / `class`, an `__all__` entry or a `[project.scripts]` / entry-point key, or when a commit carries a Conventional Commits breaking marker (`type!:` or `BREAKING CHANGE:`); otherwise it bumps Z (patch). It never changes X (major). When `CHANGELOG.md` has a non-empty `## Unreleased` section, the bump inserts the new release heading under it, so the Unreleased entries become the release notes of the bumped version (`ops.version_bump.fold_unreleased`).

### `andon` subcommands

| Subcommand | Description |
|---|---|
| `andon list [--all] [--json]` | List open (unanswered) andons; `--json` adds `raised_at` and `notes` |
| `andon show <andon-id>` | Show one andon |
| `andon answer <andon-id> <action> [--by human\|agent]` | Post the answer, remove the label and call the resume hook |
| `andon note <andon-id> --key K --value V` | Record a note on an open andon (no label change) |
| `andon auto-answer [--apply] [--json]` | Plan (or apply) the `andon.auto_answer` rules |

### `engine` subcommands

| Subcommand | Description |
|---|---|
| `engine show [--json]` | Print the effective engine and model per role |
| `engine check` | Validate commands, models and workflow drift |
| `engine clear <engine> [--kind {budget,failure,manual,drain}]` | Clear quota pause sources for an engine (all kinds when `--kind` is omitted) |
| `engine switch <role> <engine> [--model M] [--light-model M]` | Switch one role (`design` or `implementation`) |
| `engine run-guarded <role> <template> --failure-status S [--cwd D] [--tier T] [--success S] [--emit-status S] [--requires-step ID] [vars...]` | Run an LLM template, then evaluate the step's `requires` gates |
| `engine run-verified <role> <template> --failure-status S --verify V --recover R [--max-loops N] [--recover-tier T] [--skip-verify-on S] ...` | Run with a verify / recover loop (`--max-loops` default 2, `--recover-tier` default `light`) |
| `engine run` / `engine exec` / `engine resolve` | Internal dispatch helpers (hidden from `--help`) |

`--tier` takes `light` or `heavy`. A step without a `module` runs as `engine run-guarded --requires-step <step_id>`.

The `implementation` role does not pass `GITHUB_TOKEN`, `GH_TOKEN`, `GITHUB_REPOSITORIES`, or `GITHUB_REPOSITORY` into the LLM child process. Order templates may read the issue design from a file via `${issue_body_file}` when the engine supplies that variable.

### `queue` subcommands

Global options: `--queue-path`, `--state-path`, `--lock-path`.

| Subcommand | Description |
|---|---|
| `queue enqueue --issue N --phase P --source S --actor-kind {human,automation} --priority {high,normal,low} --requested-by X [--force] [--after N]` | Queue an Issue for a configured phase (`--after` is repeatable) |
| `queue tick [--seed PATH]` | Triage and dispatch queued requests |
| `queue status [--json]` | Show queue state including `blocks[]` (scope, kind, reason, until, set_by); `--json` also emits legacy `halt` fields (read-only migration) plus a dispatch `plan` snapshot |
| `queue doctor [--json]` | Report untracked in-flight Issues |
| `queue reset` | Acknowledge reset without changing queue blocks (use `queue unblock` to remove blocks) |
| `queue unblock [--kind K] [--scope S]` | Remove matching entries from `blocks[]` |
| `queue dequeue --request-id ID [--reason R]` | Remove a request |
| `queue audit` | Check queue store integrity |
| `queue triage-log [--last N] [--path P]` | Show recent triage log entries (default 20) |
| `queue migrate [--from-night-queue-state P] [--seed P] [--dry-run]` | Migrate from the legacy night-queue state |
| `queue release --issue N [--json]` | Remove an in-flight entry of any role when its DAG is not running (recovery only). Running labels may remain; the next tick will not re-register the issue until its DAG is running again. |

During `queue tick`, dispatch respects configured `concurrency`, lane `in_flight` caps, and `pipeline_not_idle`; it does not block the next dispatch until a prior Issue reaches a terminal label.

The night-queue seed file (`paths.seed`, default `configs/night-queue.yaml`) may set `window.start`, `window.end`, and `window.extra_sources`. Automation requests whose `source` is `night-queue`, `night-queue-seed`, or listed in `extra_sources` dispatch only inside that window; other automation sources are not window-gated unless listed there.

### `lanes` subcommands

`lanes` reads the ledger at `paths.lanes` and exits 2 when that key is not set or the ledger is invalid.

| Subcommand | Description |
|---|---|
| `lanes check [--apply] [--json]` | Plan slots, candidates, enqueues and escalations per lane; `--apply` enqueues the plan (skipped while the GitHub API budget is low) |
| `lanes report [--json]` | Patrol report: the lane plan plus `andon.auto_answer` escalations and merged Issues since `report_since`; state is kept in `<ledger>.state.json` |

Ledger keys: `lanes` (lane name to Issue numbers), `hold`, `backlog`, `high`, `high_labels`, `auto_lanes.enabled` (`false`), `queued_stale_minutes` (`30`), `api_min_remaining` (`api_brake.min_remaining`), `report_every_minutes` (`120`), `report_since` (ISO timestamp or `daily`).

`lanes check --json` top-level keys: `slots`, `candidates`, `enqueue`, `escalations`, `waits`, `halt`. Each `candidates` row includes `lane`, `issue`, `phase`, `depth`, `priority`. Each `enqueue` row includes `lane`, `issue`, `phase`, `engine`, `priority`.

`lanes report --json` adds `merge_done_since`, `report_due`, `quiet`, and `auto_answers` to that shape (same `slots`, `candidates`, `enqueue`, `escalations`, `waits`, `halt` fields as check).

### `milestone` subcommands

| Subcommand | Description |
|---|---|
| `milestone status <parent>` | Show the milestone chain status |
| `milestone resume <parent>` | Resume milestone chain progress |
| `milestone prune [--dry-run]` | Prune finished milestone chain state |
| `milestone consolidate <child> --into <sibling>` | Mark a child as consolidated into a sibling so the chain treats it as merged when the sibling merges |

## Output contract

Commands routed through `issuesmith.cli` print usage errors to **stderr** in a machine-readable form:

- The first line is `error[<CODE>]: <message>` where `<CODE>` is one of the codes below.
- When a fix is obvious, following lines use `hint: <text>` (for example a list of valid commands or options).
- Invalid user input must not print a Python traceback.
- `--help` / `-h` exits **0** and prints usage on stdout.

### Error codes

| Code | Exit | When |
|---|---|---|
| `UNKNOWN_COMMAND` | 2 | Unknown command or subcommand name |
| `UNKNOWN_ARGUMENT` | 2 | Undeclared option or extra positional argument |
| `MISSING_ARGUMENT` | 2 | Required argument missing (including omitted subcommand) |
| `INVALID_VALUE` | 2 | Value shape is wrong (for example a non-integer Issue number) |
| `NOT_FOUND` | 1 | Named object does not exist (andon, body file, queue entry, …) |
| `CONFIG_ERROR` | 1 | Resolved configuration fails validation |
| `FAILED` | 1 | External failure (for example forge API error) |
| `CONFLICT` | 1 | Target exists but the operation is not allowed in the current state |

Commands implemented in `issuesmith.cli` and the module-level parsers for `queue`, `engine`, `metrics rework`, `publish`, `smoke`, and `version-bump` use this contract on usage mistakes. `gh`, `dispatch`, `tier`, `verify`, and `convert-to-milestone` still use their legacy argparse stderr shapes.

### `--json` commands

| Command | JSON top-level shape |
|---|---|
| `andon list --json` | `list` of andon records (adds `raised_at` and `notes` vs plain list) |
| `andon auto-answer --json` | plan object |
| `config show` | resolved configuration object (always JSON on stdout) |
| `doctor --json` | `list` of `{"name", "ok", "detail"}` checks |
| `labels reconcile --json` | report object |
| `lanes check --json` / `lanes report --json` | keys listed under [lanes subcommands](#lanes-subcommands) |
| `metrics rework --json` | rework report object |
| `milestone status <parent> --json` | `parent`, `state`, `stage`, `children`, `next`, … |
| `observe --json` | `list` of observe events |
| `queue status --json` | queue snapshot plus `plan` entries |
| `queue doctor --json` | `ok`, `orphan_active_order_ids`, `untracked_running_issues`, `dispatch_blocked` |
| `queue release --issue N --json` | release result object |
| `engine show --json` | `state_source`, `roles` (`role` / `engine` / `model` per row) |

## Public API

Supported Python imports are limited to seven modules (`issuesmith.__all__` is empty). The CLI (`python3 -m issuesmith` / `issuesmith`) and `module.path:attr` hooks in `issuesmith.yaml` are separate extension surfaces. Each module below lists its `__all__` names; names starting with `_` are internal.

| Module | Symbols |
|---|---|
| `issuesmith.contract` | `StepContext`, `StepResult`, `StepConfig`, `Andon`, `RetrySignal`, `RetryReason`, `parse_issue_metadata`, `validate_issue_metadata`, `MetadataViolation` |
| `issuesmith.verbs` | `AcceptanceError`, `AcceptanceVerdict`, `ReviewFacts`, `ChildPlan`, `ChildPlanEntry`, `ChildPlanError`, `WorktreeError`, `block_design_only_issue`, `check_acceptance`, `check_contract`, `cleanup_branches`, `cleanup_worktrees`, `close_issue`, `create_child`, `ensure_base_included`, `exec_file`, `extract_contract`, `find_pr`, `map_step_result`, `merge_pr`, `merge_state`, `plan_children`, `prepare_worktree`, `publish_branch`, `resolve`, `resolve_base_ref`, `route`, `review_tier`, `run_guarded`, `run_verified`, `unchecked_acceptance_count`, `validate_children`, `widen_allow_paths` |
| `issuesmith.gates` | `Verdict`, `GateBuildContext`, `GateBuildError`, `GateEntry`, `RequiresGate`, `GATE_REGISTRY`, `NON_REPAIRABLE_RULE_IDS`, `is_non_repairable_rule_id`, `resolve_gate`, `validate_step_requires`, `check_scope`, `check_pr_scope`, `check_ac_complete`, `check_deps`, `Violation`, `Gate`, `PreconditionContext`, `normalize_allow_path`, `external_target_state` |
| `issuesmith.config` | `get_config`, `load_config`, `IssuesmithConfig`, `LanguagePack` (phase → role via IssuesmithConfig.phase(name).role) |
| `issuesmith.andon` | `Andon`, `AndonSink`, `AutoAnswerPlan`, `raise_andon`, `list_open`, `list_open_records`, `answer`, `from_comment`, `to_comment`, `plan_auto_answers`, `apply_auto_answers` |
| `issuesmith.queue` | `QueueStore`, `DispatchResult`, `dispatch_one` |
| `issuesmith.forge` | `ForgePort`, `GitHubApiError`, `GitHubClient`, `LocalForge`, `get_forge` |

Deeper import paths (for example `issuesmith.engine`, `issuesmith.merge`, or other subpackages) may still resolve while the interface shrinks, but they are not part of the supported public API and may change or be removed in any release.

Gate ids usable in `steps.*.requires`: `ac_complete`, `ac_contract`, `ac_format`, `allow_paths_forbidden`, `base_freshness`, `deps`, `deps_format`, `design_lint`, `external_leak`, `importer_import`, `lint`, `migration_plan`, `milestone_consistency`, `milestone_subdesign`, `pin_bump`, `pr_scope`, `publish_diff`, `scope`, `scope_breadth`, `scope_coupling`, `scope_size`, `tests` (`GATE_REGISTRY`), plus `review` (`STEP_BOUND_GATES`; needs `steps.<id>.review`). A `module.path:attr` reference loads an external gate.

### Protocols and extension points

- `steps.<id>.module` names a host-side Python module that implements `run(ctx: StepContext, step: StepConfig) -> StepResult` (types in `issuesmith.contract`). When `module` is omitted, the step is an LLM step executed via `engine run-guarded`.
- `issuesmith.gates.base.Gate`: `check(body, labels) -> list` returns violations; `fix(inp: ContractInput) -> ContractInput` may only narrow its input (drop files, run `ruff --fix`, fast-forward a branch) and never widens allow_paths.
- `phases[].advance_when` and `steps.*.requires` accept `module.path:attr` references, so a host can add predicates and gates without changing issuesmith.
- `issuesmith.preconditions.register(name, fn)` adds a named predicate.
- Language packs replace the Issue-body vocabulary and posted text (see [Language packs](#language-packs)).

### Public API Stability

issuesmith is pre-1.0 and follows `0.Y.Z`: a `Y` bump may change or remove public API, a `Z` bump does not. The seven modules in the [Public API](#public-api) table are the supported surface; anything else may change in any release. Bundled reference-line step modules and the files under `issuesmith.lines.*/files/` may change in `0.Y.0` releases; module path changes are listed under Breaking in [CHANGELOG.md](./CHANGELOG.md).

## Architecture

ghdag's `WorkflowDispatcher` owns orchestration: polling, DAG construction, label transitions and idempotency. issuesmith provides the gates, steps and tools that the workflow templates call. A phase may advance when its `preconditions` labels are present, its `excludes` labels are absent and every `advance_when` predicate passes. After a step finishes, its `requires` gates run; auto-fixable violations are fixed in place, other blocking violations start a repair run (`steps.*.repair`) or raise an andon (`steps.*.andon_when`). Label writes from steps go through `forge_guard`; managed labels are derived by `projection` and written only by `ops/labels.project_issue`.

| Module | Role |
|---|---|
| `issuesmith/__init__.py` | Package marker; empty `__all__` |
| `issuesmith/__main__.py` | `python3 -m issuesmith`: calls `cli.main` |
| `issuesmith/ac_complete_entry.py` | `ac_complete` gate CLI and library entry (rules in `gate_rules/ac_complete_rules.py`) |
| `issuesmith/ac_contract.py` | Extract and run the acceptance-criteria YAML contract |
| `issuesmith/andon.py` | Andons (typed stop-the-line signals in Issue comments) and auto-answer rules |
| `issuesmith/body_editor.py` | Issue body H2 section editing and milestone normalizers |
| `issuesmith/branch_reuse.py` | Find and record reusable branches from earlier runs |
| `issuesmith/bump_commits.py` | Publish version-bump commit detection and rewrite helpers |
| `issuesmith/cli_errors.py` | Shared CLI usage-error contract (`fail`, `ContractParser`, `ERROR_EXIT`) |
| `issuesmith/cli.py` | Unified CLI (`_HANDLERS`) |
| `issuesmith/config.py` | `issuesmith.yaml` loading, validation and defaults |
| `issuesmith/context_hook.py` | ghdag context hook and Issue YAML metadata parsing |
| `issuesmith/contract.py` | Step contract types and Issue-body section parsers |
| `issuesmith/convert_to_milestone.py` | Move an Issue back to the milestone path |
| `issuesmith/dep_extractor.py` | Dependency extraction and checks |
| `issuesmith/design_lint_entry.py` | `design_lint` gate CLI and library entry (rules in `gate_rules/design_lint_rules.py`) |
| `issuesmith/engine.py` | LLM role switcher, `run-guarded`, `run-verified`, rate-limit pause and engine wait |
| `issuesmith/engine_route.py` | Pure `build_route`: assemble `(engine, model)` candidates into a ghdag `call(route=...)` list for heavy / light tier and pause filtering |
| `issuesmith/forge.py` | Public re-exports of `ghdag.forge` for host consumers |
| `issuesmith/forge_api.py` | Typed raw REST calls on forge clients |
| `issuesmith/forge_guard.py` | Label-write guard for step dispatch |
| `issuesmith/gate_registry.py` | Rule-class gate registry types (`Violation`, `GateRule`, `GATE_REGISTRY`) |
| `issuesmith/gate_rules/__init__.py` | Imports every rule module to fill ghdag's gate registry |
| `issuesmith/gate_rules/ac_complete_rules.py` | `ac_complete` gate rules (M2 merge readiness) |
| `issuesmith/gate_rules/ac_format_rules.py` | `ac_format` gate rules |
| `issuesmith/gate_rules/allow_paths_forbidden.py` | allow_paths vs forbidden_pr_paths overlap (pre-LLM) |
| `issuesmith/gate_rules/change_table_scope.py` | Change-table path scope vs allow_paths / host_allow_paths |
| `issuesmith/gate_rules/common.py` | Shared helpers for gate rule modules (strip fenced code from Issue bodies) |
| `issuesmith/gate_rules/deps_format.py` | Dependencies section format: unparsed `#N` refs without forge calls |
| `issuesmith/gate_rules/design_lint_rules.py` | `design_lint` / CP1 design rules |
| `issuesmith/gate_rules/migration_plan_rules.py` | `migration_plan` gate rules |
| `issuesmith/gate_rules/milestone_consistency.py` | Split plan without the `scope:milestone` label |
| `issuesmith/gate_rules/milestone_subdesign_rules.py` | `milestone_subdesign` gate rules and dependency inference |
| `issuesmith/gate_rules/pin_bump.py` | Reject dependency pin changes in feature Issues |
| `issuesmith/gate_rules/scope_breadth.py` | allow_paths breadth rules |
| `issuesmith/gate_rules/scope_coupling.py` | Caller / test coupling and deletion reference rules |
| `issuesmith/gate_rules/scope_size.py` | Issue size rules and promotion of oversized Issues to sub plans |
| `issuesmith/gates/__init__.py` | Unified gate API: `GATE_REGISTRY`, `resolve_gate`, `validate_step_requires` |
| `issuesmith/gates/ac_complete.py` | `check_ac_complete` wrapper around `ac_complete_entry.check_gate` |
| `issuesmith/gates/ac_contract.py` | Worktree gate: acceptance contract `paths_must_exist` / `paths_must_not_exist` / `greps_must_be_empty` |
| `issuesmith/gates/base.py` | `Gate` protocol and `ContractInput` |
| `issuesmith/gates/dep.py` | Dependency gate |
| `issuesmith/gates/pr_scope.py` | PR scope gate |
| `issuesmith/gates/review.py` | `review` gate: runs `steps.<id>.review` through `run-guarded` and maps its problems to violations |
| `issuesmith/gates/scope.py` | Scope gate |
| `issuesmith/gates/worktree.py` | `lint`, `tests`, `external_leak`, `base_freshness`, `importer_import` and `publish_diff` gates |
| `issuesmith/github_api.py` | `gh` command: Issue / PR helpers with label and target guards |
| `issuesmith/lanes.py` | Lane ledger planning: capacity, candidates, enqueue and patrol report |
| `issuesmith/language.py` | Language packs |
| `issuesmith/lines/__init__.py` | Bundled reference lines (`LINES`) and `extract_line` for `config extract-line` |
| `issuesmith/lines/develop/__init__.py` | Reference develop line package marker |
| `issuesmith/lines/develop/prepare.py` | Reference develop step: prepare |
| `issuesmith/lines/develop/implement.py` | Reference develop step: implement (and repair) |
| `issuesmith/lines/develop/verify.py` | Reference develop step: verify |
| `issuesmith/lines/develop/review.py` | Reference develop step: review (adds Issue body and diff to the review dispatch) |
| `issuesmith/lines/develop/merge.py` | Reference develop step: merge |
| `issuesmith/lines/develop/hooks.py` | ghdag runner hooks for the reference develop line (`DevelopLineHooks`) |
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
| `issuesmith/pins.py` | `requires_pins` parsing and the `pins_landed` predicate |
| `issuesmith/pipeline_comments.py` | Pipeline comment filter |
| `issuesmith/pr_scope.py` | PR diff scope: allow_paths, `forbidden_pr_paths` and `forbidden_pr_paths_except` |
| `issuesmith/preconditions.py` | Phase advance predicates (`PRECONDITION_REGISTRY`, `evaluate`) |
| `issuesmith/projection.py` | Pure label projection: Issue state to managed labels |
| `issuesmith/queue.py` | `queue` command: enqueue, triage, dispatch, migrate |
| `issuesmith/queue_store.py` | Queue store: JSONL requests, state and lock |
| `issuesmith/queue_triage.py` | Deterministic checks and LLM triage for the queue |
| `issuesmith/quota_gate.py` | GitHub API rate-limit brake |
| `issuesmith/recovery.py` | `plan`, `list_recover_steps`, `requeue_phase` |
| `issuesmith/repair.py` | `requires` evaluation and repair orchestration |
| `issuesmith/resume.py` | `resume` entry point |
| `issuesmith/rule_ids.py` | Legacy rule id alias normalization (`cp1`→`design_lint`, `m2`→`ac_complete`, …) |
| `issuesmith/scope_gate.py` | allow_paths scope measurement |
| `issuesmith/step_tier.py` | Config-driven tier CLI backend for `steps.<id>.tier` |
| `issuesmith/step_verify.py` | Config-driven verify CLI backend for `steps.<id>.verify: design_doc` |
| `issuesmith/targets.py` | Multi-target Issue model |
| `issuesmith/template_ids.py` | `string.Template` identifier extraction (Python 3.10 compatible) |
| `issuesmith/verbs/__init__.py` | Public verb API |
| `issuesmith/verbs/acceptance.py` | `check_acceptance`, `unchecked_acceptance_count`, `extract_contract`, `check_contract`, `review_tier` |
| `issuesmith/verbs/children.py` | `plan_children`, `create_child`, `validate_children`, `widen_allow_paths` |
| `issuesmith/verbs/finalize.py` | `cleanup_worktrees`, `cleanup_branches`, `close_issue` |
| `issuesmith/verbs/merge.py` | `find_pr`, `merge_state`, `merge_pr` |
| `issuesmith/verbs/publish.py` | `publish_branch` |
| `issuesmith/verbs/worktree.py` | `prepare_worktree`, `ensure_base_included`, `block_design_only_issue` |
| `issuesmith/worktree.py` | Worktree creation and base-branch fetch |
| `issuesmith/worktree_python.py` | Resolve a worktree-specific Python `bin` directory via `tests.python_bin_command` |

## Gates

Gate ids in `GATE_REGISTRY` (usable in `steps.*.requires`). `input_kind` is the step `input_kind` the gate expects. `pre_llm` gates run before the LLM in `engine run-guarded --requires-step`. Non-repairable violations raise an andon or block without a repair loop.

| id | input_kind | pre_llm | repairable | Responsibility |
|---|---|---|---|---|
| `ac_complete` | issue | no | yes | M2 merge readiness rules |
| `ac_contract` | worktree | no | yes | Enforce acceptance-contract `paths_must_exist` / `paths_must_not_exist` / `greps_must_be_empty` on the worktree |
| `ac_format` | issue | no | yes | Acceptance-criteria YAML format in the Issue body |
| `allow_paths_forbidden` | issue | yes | no | allow_paths / host_allow_paths overlap with `forbidden_pr_paths` (no repair loop) |
| `base_freshness` | worktree | yes | yes | Worktree includes the configured base branch (P0 fetch / rebase) |
| `change_table_scope` | issue | no | yes | Change-table paths (target / host rows) stay within allow_paths / host_allow_paths |
| `deps` | issue | no | yes | Declared Issue dependencies are satisfied |
| `deps_format` | issue | no | yes | Dependencies section lists `#N` refs in table/list form, not prose-only |
| `design_lint` | issue | no | yes | CP1 design rules |
| `external_leak` | worktree | no | yes | Persona / CJK leak checks on external `target_repo` branches |
| `importer_import` | worktree | no | yes | Changed modules remain importable (importer smoke) |
| `lint` | worktree | no | yes | `ruff check` on changed files (safe auto-fix may narrow scope) |
| `migration_plan` | issue | no | yes | Migration plan rules for B1 |
| `milestone_consistency` | issue | no | yes | Split plan present without the `scope:milestone` label |
| `milestone_subdesign` | issue | no | yes | Milestone sub-design blocks, dependency inference and fixes |
| `pin_bump` | issue | no | yes | Reject dependency pin changes in feature Issues |
| `pr_scope` | worktree | no | yes | PR diff paths stay within allow_paths (plus derived test paths) |
| `publish_diff` | worktree | no | yes | pyproject.toml version-line changes and exact version asserts under tests/ in the branch diff (same checks as publish) |
| `scope` | worktree | no | yes | Changed files stay within allow_paths |
| `scope_breadth` | issue | yes | no | P0 allow_paths breadth; oversized scope must split (no repair loop) |
| `scope_coupling` | issue | no | yes | Caller / test coupling and deletion-reference rules |
| `scope_size` | issue | no | yes | B1 Issue size limits and sub-plan promotion (`split: atomic` or a design-section `atomic_phrases` match skips promotion) |
| `tests` | worktree | no | yes | `pytest` on derived test paths (flaky reruns per config) |

`review` is bound to `steps.<id>.review` (`STEP_BOUND_GATES`) and is not a `GATE_REGISTRY` key. Hosts may also reference `module.path:attr` gates.

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
| `ISSUESMITH_CALLER_STEP` | (unset) | Set for the duration of `dispatch` to the dispatched step id; `ensure_base_included` reads it when reconciling the base branch |
| `ISSUESMITH_QUEUE_DIR` | (unset) | Directory for the queue, queue state, lock and triage log (overrides the `paths` defaults) |
| `ISSUESMITH_ENGINE_WAIT_POLL_SEC` | `60` | Re-check period while every engine is paused; values outside 1-60 give 60 |
| `ISSUESMITH_ENGINE_WAIT_INTERVAL_SEC` | `60` | Legacy name, read only when `ISSUESMITH_ENGINE_WAIT_POLL_SEC` is unset |
| `ISSUESMITH_ENGINE_WAIT_MAX_SEC` | `21600` | Longest wait for an engine to leave pause |
| `ISSUESMITH_TIMEOUT_SEC` | (unset) | LLM run timeout used when the engine state has no `timeout_sec` for the role; falls back to `engines.<role>.timeout_sec` |
| `ISSUESMITH_PYTEST_TIMEOUT_SEC` | `1500` | Timeout of one pytest run in the `tests` gate |
| `ISSUESMITH_REPAIR_ACTIVE` | (unset) | Set internally during a repair run to stop nested repairs |
| `METRICS_JSONL_PATH` | `paths.metrics` | Engine metrics JSONL path |
| `ISSUESMITH_FETCH_LOCK_WAIT` | `30` | Seconds to wait for the base-fetch lock |
| `ISSUESMITH_FETCH_TIMEOUT_SEC` | `300` | Wall-clock limit (seconds) for a single base `git fetch` in P0 |
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
| `host_is_repo_checkout` | `true` | Whether the config directory is a checkout of the issue repository. When false, issues targeting that repository (or with no target_repo) use the clone under paths.external_dir like other targets, and the repository must be listed in supported_repos |
| `paths` | see below | Queue, state, metrics, worktree, template and ledger paths |
| `engines.<role>` | see below | Per role (`design`, `implementation`): `heavy`, optional `light`, `timeout_sec`, `pause_ttl_sec` |
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
| `scope_size` | `enabled: true` | B1 Issue size: `max_files` (`8`), `max_concerns` (`4`), `max_slices` (`3`), `delete_with_new` (`false`), `exclude_prefixes`, `atomic_phrases` (`[]`, phrases matched in the design section skip sub-plan promotion, same as `split: atomic`) |
| `tests` | `flaky_reruns: 2`, `python_command: [python]` | Reruns of newly failing tests (`0` turns flaky detection off); `python_command` is the argv prefix used to launch pytest (baseline and flaky reruns too) |
| `metrics` | `done_step: m2` | Rework metrics: `done_step`, `repair_templates`, `cause_targets` |
| `derived_allow` | `enabled: true`, `ledger_globs: [tests/conventions/known_*.txt]` | Let repair (and impl ahead of repair) edit tests outside allow_paths when they reference in-scope changes, or shrink matching ledgers; newly failing tests still extend the set via TestsGate, including repository `.md` files those tests name in source (`[]` turns ledger derivation off) |
| `external_leak` | `cjk_free_external_targets: false` | Fail added CJK lines on branches whose `target_repo` is not `repo` |
| `observe` | see below | Observe thresholds and `main_health` |
| `api_brake` | `enabled: false` | GitHub API brake: `min_remaining` (`800`) |
| `andon.auto_answer` | `[]` | Auto-answer rules: `name` (unique), `match` (non-empty; keys `step`, `kind`, `rule_id`), `action`, `max_per_issue` (`1`) |
| `language_pack` | (unset: `EN`) | Path to a language pack YAML (see [Language packs](#language-packs)) |
| `installs` | `{}` | Package name to install directory, or `"@env"` to use the version installed in this process (`importlib.metadata`); used by `pins_landed` for line-environment versions |
| `pin_packages` | `installs` keys | Package names the `pin_bump` gate watches for pin changes in feature Issues; omit to copy `installs` keys, or set `[]` to watch none |

`tests`, `metrics`, `derived_allow`, `external_leak`, `steps.*.repair` and `steps.*.review` reject unknown keys. `scope_coupling.ignore_symbols` is no longer supported and raises `ConfigError`.

When the host environment that runs the pipeline differs from the target repository’s runtime dependencies, set `tests.python_command` to a launcher that selects the right interpreter for the worktree (pytest’s cwd). Relative launcher paths that contain `/` resolve against the directory of `issuesmith.yaml`; bare names are looked up on `PATH`. If the configured launcher cannot be started, the `tests` gate fails with `tests.collection_error` and names the command.

#### `engines.<role>` fields

| Field | Default (`design` / `implementation`) | Description |
|---|---|---|
| `heavy` | `[[claude, claude-opus-4-6], [codex, gpt-5.6-sol]]` / `[[claude, claude-sonnet-4-6], [cursor, auto]]` | Ordered `[engine, model]` pairs for the heavy tier (state selection is tried first, then the rest in list order) |
| `light` | `[[claude, claude-sonnet-4-6], [codex, gpt-5.5]]` / omitted | Optional light-tier route; omitted means the heavy route is used for light tier |
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
| `tier` | (unset) | Tier selection for `issuesmith tier <id>`: `kind: design_doc` (issue body) or `pr_diff` (`light_max_diff_lines`, default `200`) |
| `verify` | (unset) | When set to `design_doc`, `issuesmith verify <id>` runs the design-doc gate bundle (`VERIFY_RECOVERY:` markers) |
| `review` | (unset) | Required when `requires` or `accepts` lists `review`: `role`, `template`, `success_status`, `failure_status` (required), `tier`, `problems_heading` (`Problems:`) |

#### `paths` keys

Relative to the config file directory: `queue` `jobs/issuesmith-queue.jsonl`, `queue_state` `logs/issuesmith-queue-state.json`, `queue_lock` `logs/issuesmith-queue.lock`, `triage_log` `jobs/issuesmith-triage.jsonl`, `seed` `configs/night-queue.yaml`, `night_state` `logs/night-queue-state.json`, `exec_jsonl` `jobs/exec.jsonl`, `done_dir` `jobs/done`, `quota_state` `jobs/quota-gate.json`, `metrics` `jobs/metrics.jsonl`, `worktrees_dir` `.claude/worktrees`, `external_dir` `.claude/external`, `workflow` `workflows/issuesmith.yml`, `template_dir` `workflows/issuesmith`, `engine_state` `.pipeline-state/issuesmith-engine.yml`, `lanes` (unset; the ledger read by the `lanes` command).

#### `observe` keys

`stall_minutes` `120`, `task_timeout_minutes` `90`, `systemic_min_issues` `2`, `systemic_window_minutes` `60`, `forge_max_consecutive_errors` `3`, `max_api_calls` `8`, `workflows` (`[]`). `workflows` lists non-issuesmith ghdag workflows to watch for `dag_terminated`: each entry needs `name` (workflow id) and optional `label_namespace` (defaults to `name`; used for running-label and andon namespacing). Entries whose `name` equals the host `label_namespace` are ignored; duplicate names raise `ConfigError`. `observe.main_health` is off when unset; it takes `worktree` and `command` (both required), `base_branch` (`main`), `timeout_seconds` (`1800`), and `halt_on_red` (`true`). When the worktree path is missing, issuesmith creates a detached worktree from `repo_root` before running the command. The check writes full command output to `issuesmith-main-health.log` and persists `output_tail` in state when status is red. When `halt_on_red` is `false`, main red still raises the `main_red` andon but does not halt `phase:develop`, and clears an existing halt raised by `main_red`.

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

Logs, exception messages, CLI help and violation text stay English and are not part of a pack.

## Error Reference

| Type | Module | Base | Raised when |
|---|---|---|---|
| `AcceptanceError` | `issuesmith.verbs.acceptance` | `RuntimeError` | Acceptance gate checks could not materialize the temporary `origin/<base>` worktree |
| `BumpCommitsError` | `issuesmith.bump_commits` | `RuntimeError` | Git could not list or rewrite publish version-bump commits |
| `ChildPlanError` | `issuesmith.verbs.children` | `RuntimeError` | Child-plan validation or creation failed with structured violations |
| `ConfigError` | `issuesmith.config` | `ValueError` | Invalid `issuesmith.yaml` section, language pack, gate or predicate reference; missing `phases:` or `phases[].handler`; a step in two phases; `review` gate without `steps.<id>.review` |
| `ContextHookError` | `issuesmith.context_hook` | `ValueError` | Issue YAML metadata failed security validation before ghdag context-hook template expansion |
| `DerivedAllowPathsError` | `issuesmith.pr_scope` | `ValueError` | `derived_allow_paths_from_result` finds malformed entries |
| `GateBuildError` | `issuesmith.gates` | `ValueError` | A gate cannot be built from its `GateBuildContext` |
| `GateMaterializationError` | `issuesmith.ac_contract` | `RuntimeError` | The temporary `origin/<base>` worktree for gate checks could not be created |
| `GrepError` | `issuesmith.ac_contract` | `RuntimeError` | `git grep` for acceptance-contract `greps_must_be_empty` failed with a non-zero exit other than 1 (no matches) |
| `LabelWriteForbidden` | `issuesmith.contract` | `RuntimeError` | A step wrote labels while `label_write_guard` is `enforce` |
| `MainHealthError` | `issuesmith.observe.main_health` | `RuntimeError` | The base-branch health check could not run (git, worktree, timeout); state is not updated |
| `MergeStateTimeoutError` | `issuesmith.merge` | `Exception` | The PR merge state did not settle before the timeout |
| `QueueValidationError` | `issuesmith.queue_store` | `ValueError` | Invalid queue request input (CLI exits 2) |
| `RetrySignal` | `issuesmith.contract` | `RuntimeError` | Rate limit or quota pause: dispatch prints `PIPELINE_STATUS: WAIT_QUOTA` and ghdag keeps the task pending; not a step failure |
| `WorktreeError` | `issuesmith.worktree` | `Exception` | Worktree preparation failed |
| `WorktreePythonError` | `issuesmith.worktree_python` | `RuntimeError` | `python_bin_command` failed or returned an unusable path |

Some shape errors raise plain `ValueError` instead of `ConfigError`: a missing `repo`, a non-mapping config file, an empty or malformed `phases` list, a non-positive or non-integer `engines.<role>.pause_ttl_sec`, a non-list `forbidden_pr_paths`, `forbidden_pr_paths_except`, `terminal_labels` or `terminal_without_merge`, and an incomplete `observe.main_health`. `TemplateVariableError` (`ghdag.pipeline.order`) can propagate from `issuesmith.engine` when a template variable is missing.

## License

MIT (SPDX `MIT`). See [LICENSE](./LICENSE).
