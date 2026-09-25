# issuesmith

参照版: v0.10.0 / 確認日: 2026-09-10

issuesmith is a GitHub Issue label-driven workflow framework that runs on [ghdag](https://github.com/sumipan/ghdag). It provides gates, queue triage, context hooks, and a unified CLI for pipelines that advance Issues through design → implementation → merge via labels — not CI YAML alone.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-v0.10.0-blue)
![ci](https://github.com/sumipan/issuesmith/actions/workflows/ci.yml/badge.svg?branch=main)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Current release is **v0.10.0** (pre-1.0). Interfaces may evolve before `1.0.0`.

## Installation

```bash
pip install "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.10.0"
```

With ghdag (required for gate-preflight and most runtime paths):

```bash
pip install "issuesmith[ghdag] @ git+https://github.com/sumipan/issuesmith.git@v0.10.0"
```

| Item | Value |
|---|---|
| Python requirement | `>=3.10` |
| Runtime dependencies | `pyyaml`, `ruamel.yaml`, `packaging`, `python-dotenv` |
| Optional / recommended | `ghdag @ git+https://github.com/sumipan/ghdag.git@v0.44.0` (`[ghdag]` or `[dev]`) |
| Dev dependencies | `pip install "issuesmith[dev]"` |

### Usage from nexus

nexus consumes this package via a stable editable install at `/var/tmp/issuesmith` and pins the dependency in its root `pyproject.toml`. Configure the host repository with a root `issuesmith.yaml` (paths, engines, supported repos). Development clones live under `.claude/external/issuesmith/`; do not editable-install from worktree paths.

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

Run the CLI:

```bash
export ISSUESMITH_CONFIG=/path/to/issuesmith.yaml   # optional if yaml is at cwd or above
python3 -m issuesmith doctor
python3 -m issuesmith gate cp1 --body-file body.md
python3 -m issuesmith engine show
```

## CLI Reference

Entry points: `issuesmith` / `python3 -m issuesmith`.

| Command | Description |
|---|---|
| `context` / `context_hook` / `context-hook` | Generate ghdag context for impl/merge handlers |
| `gate` / `gate-preflight` | Run a named gate (e.g. `gate cp1 --body-file ...`) |
| `cp1-gate` / `m2-gate` | Direct CP1 / M2 gate entry points |
| `verify` / `b1-verify` | B1 verification (`verify b1 ...`) |
| `queue` | Night / draft queue tick and status (`draft`, `sub`, `develop`, `merge` phases) |
| `milestone` | Milestone chain status / resume (`milestone status <parent>`, `milestone resume <parent>`) |
| `deps` | Extract Issue dependencies |
| `tier` | Choose B1 / CP2 model tier (`tier b1` / `tier cp2`) |
| `comments` | Pipeline comment helpers |
| `gh` | GitHub Issue/PR helpers via ghdag client (not the `gh` CLI) |
| `engine` | LLM role switcher / runner |
| `run-guarded --requires-step <step>` | Run pre/post gate evaluation around an LLM call for a module-less step |
| `dispatch` | Render and enqueue a workflow template |
| `publish` | Publish / version-bump orchestration |
| `labels reconcile [--fix] [--json]` | report (or fix) managed-label divergences |
| `doctor` | Preflight / environment checks |
| `smoke` | Template smoke against live Issue bodies |
| `gen-live` | Generate live dispatch payloads |
| `version-bump` | Deterministic package version bump |
| `observe [--apply] [--json]` | Collect observe events; `--apply` executes the policy actions |
| `main-health` | Run `observe.main_health.command` on the latest base branch and write `issuesmith-main-health.json` (prints `main_health: <green\|red> <sha12>`; `disabled` when unset; exit 2 on git / timeout errors) |
| `apply` / `ingest-review` | Moved to host `tools/stash/`; exits 2 |

## Public API

Top-level `__all__` is empty; import modules directly. Notable symbols:

| Symbol | Module |
|---|---|
| `get_config` / `load_config` / `reset_config_cache` | `issuesmith.config` |
| `QueueStore` / `QueueValidationError` | `issuesmith.queue_store` |
| `parse_issue_metadata` / `validate_issue_metadata` / `MetadataViolation` | `issuesmith.context_hook` |
| `run_checks` / `extract_key_path_values` | `issuesmith.ac_contract` |
| `main` | `issuesmith.cli` |

## Architecture

```
src/issuesmith/
  cli.py              Unified CLI dispatcher
  config.py           issuesmith.yaml resolution
  context_hook.py     Issue YAML metadata + ghdag context
  contract.py         Canonical parsers for Issue-body contract sections (one per section)
  engine.py           LLM role switcher / metrics
  queue.py            Queue dispatch loop
  queue_store.py      Persistent queue state
  queue_triage.py     LLM triage / title normalization
  milestone.py        Milestone chain automation (sub phase + child develop)
  ac_contract.py      Acceptance-criteria contract DSL
  b1_tier.py / b1_verify.py
  cp1_gate.py / cp2_tier.py / m2_gate.py
  dep_extractor.py    Dependency extraction
  github_api.py       Issue API wrappers
  pipeline_comments.py
  body_editor.py      Issue body edit helpers
  gate_rules/         Named gate rule modules
  ops/                dispatch, publish, doctor, smoke, version-bump, ...
```

Workflow design conventions (one parser per contract section, runtime stop ↔ preflight parity)
are enforced by `tests/test_workflow_conventions.py`; see nexus `docs/ISSUESMITH.md` for the rules.

Orchestration (polling, DAG, label transitions) remains in **ghdag** `WorkflowDispatcher`. This package supplies the Issue-domain tools and gates that templates invoke.

## Configuration

| Name | Kind | Description |
|---|---|---|
| `ISSUESMITH_CONFIG` | env | Absolute path to `issuesmith.yaml` |
| `ISSUESMITH_QUEUE_DIR` | env | Override directory for queue / triage files |
| `AGENT_SKILLS_DIR` | env | Skills directory for doctor/preflight (default `~/.agents/skills`) |
| `METRICS_JSONL_PATH` | env | Override metrics JSONL path for `issuesmith.engine` |
| `ISSUESMITH_TIMEOUT_SEC` | env | Override engine timeout seconds. Total wall budget for wait + LLM (`call_managed`); waiting time is not added on top |
| `ISSUESMITH_ENGINE_WAIT_POLL_SEC` | env | While all engines are paused, re-check quota every N seconds (default `60`, clamped to 1–60). Prefer this over `ISSUESMITH_ENGINE_WAIT_INTERVAL_SEC` |
| `ISSUESMITH_ENGINE_WAIT_INTERVAL_SEC` | env | Legacy alias for the pause re-check interval when `ISSUESMITH_ENGINE_WAIT_POLL_SEC` is unset (same 1–60 clamp) |
| `ISSUESMITH_ENGINE_WAIT_MAX_SEC` | env | Max seconds to wait for an engine to leave pause (default `21600`). Wait also stops early to leave ≥300s for the LLM within `ISSUESMITH_TIMEOUT_SEC` |
| `issuesmith.yaml` | file | Repo / paths / engines / supported_repos / scope_gate (see Quick Start) |

Optional `scope_gate` keys in `issuesmith.yaml` (allow_paths size check, #3349):

| Key | Default | Description |
|---|---|---|
| `enabled` | `true` | When `false`, skip measurement and always proceed |
| `max_files` | `80` | Max tracked files matching `allow_paths` |
| `max_lines` | `20000` | Max text lines (excludes `*.jsonl` and binaries) |
| `hard_max_files` | `200` | Ceiling for Issue YAML `scope_gate.max_files` overrides (CP1 enforces) |

Issue YAML may also declare `scope_mode: internal` (sumipan/nexus#3527): the change keeps the
public interface of the listed files, so the `scope_coupling` gate does not require callers or
tests outside `allow_paths` to follow. Without it, `scope_coupling` requires only callers of
public symbols defined in the changed files and basenames of deleted / moved files; files that
match by string only are listed for reference and never widen `allow_paths`.

Optional `scope_coupling` keys in `issuesmith.yaml` (caller/test coupling check):

| Key | Default | Description |
|---|---|---|
| `enabled` | `true` | When `false`, skip coupling check entirely |
| `search_dirs` | `["tests", "src"]` | Directories to grep for callers and tests. Hits from `tests` go to `tests_outside_allow_paths`; all others go to `callers_outside_allow_paths`. Example: `[tests, src, workflows, tools, scripts]` to cover workflow templates and helper scripts |

Optional `observe.main_health` keys in `issuesmith.yaml` (base-branch health check, #3664).
Without the section the feature is disabled:

| Key | Default | Description |
|---|---|---|
| `worktree` | (required) | Detached worktree of the base branch (create it beforehand) |
| `command` | (required) | Health command; a string is split with `shlex`, a list is used as-is (no shell) |
| `base_branch` | `main` | Branch fetched from `origin` and checked out detached |
| `timeout_seconds` | `1800` | Command timeout; a timeout leaves the state file unchanged |

`issuesmith main-health` skips the command while `origin/<base_branch>` has the SHA already
recorded in `<queue_state dir>/issuesmith-main-health.json`. `observe()` only reads that file:
a red state emits `main_red` on every tick (halt `phase:develop` with `keep_existing`, one
`broken` andon `observe:0:main_red:0`), and a green state emits `main_green` while the halt
was raised by `main_red`, which clears only that halt. Scheduling the command is up to the host.

Optional `derived_allow` keys in `issuesmith.yaml` (derived allow_paths for newly failing tests, #3756):

| Key | Default | Description |
|---|---|---|
| `enabled` | `true` | When `false`, `TestsGate` never derives paths and `pr_scope` checks `allow_paths` only. Other keys raise `ConfigError` |

When a body's change table row or section heading contains a removal keyword (`delete`, `remove`, `削除`, `撤去`, `廃止`), backtick identifiers and `` `${template_var}` `` names in that row, in the heading text, or in the section body under that heading are unconditionally treated as required search keys — even if they have no `def`/`class` definition in the changed files. This catches constants, YAML keys, and dataclass fields being removed. Bare (non-backticked) words are not extracted. Example:

```markdown
## Items to remove: `MY_CONST`

The `${old_step_result}` template variable is no longer used.
```

Both `MY_CONST` and `old_step_result` become required keys; any file outside `allow_paths` that references them generates a violation.

**CP1 narrows automatically, P0 is a safety net (#3487).** `steps.scope_gate.resolve_scope_root`
is the single root resolver both CP1 (`gate_rules.scope_breadth`) and P0 (`steps.p0_worktree`) call,
so both measure the same tree. When CP1 finds allow_paths over threshold, it deterministically
narrows to the union of the change table and the AC `paths_must_exist` list, posts a comment with
the before/after, and continues — it never falls back to the parent's allow_paths and never passes
silently when the root can't be measured (`scope_breadth.root_unavailable` fails closed). P0 runs
the same check again after the worktree exists; because CP1 is declared as the preflight-parity rule
for `SCOPE_TOO_LARGE` (`gate_rules.PREFLIGHT_PARITY`), a trip at P0 means CP1 already should have
caught it — the comment says so explicitly and `scope_gate.p0_trip` is recorded to `jobs/metrics.jsonl`.

Optional `milestone_chain` keys in `issuesmith.yaml`:

| Key | Default | Description |
|---|---|---|
| `enabled` | `false` | Enable milestone chain automation |
| `auto_develop` | `true` | After child validation, enqueue `develop` for each child |
| `auto_close_parent` | `true` | When every child is `CLOSED` with `issuesmith:merge-done`, comment once and close the parent. Set `false` to keep notify-only behavior. Children closed via `TERMINAL_WITHOUT_MERGE` (e.g. rejected) halt the chain for human review instead of closing the parent |

Optional `triage` keys in `issuesmith.yaml` (queue tick LLM reorder):

| Key | Default | Description |
|---|---|---|
| `enabled` | `true` | When `false`, skip LLM and apply deterministic priority order only |
| `engine` | `claude` | LLM engine passed to ghdag `call_text` |
| `model` | `claude-sonnet-4-6` | Model id for triage |
| `timeout` | `60` | LLM timeout seconds |
| `body_chars` | `500` | Max Issue body characters included as `body_head` (use `0` for title-only) |
| `circuit_breaker_threshold` | `3` | Consecutive LLM timeout entries that open the circuit |
| `circuit_breaker_reset_seconds` | `1800` | After this many seconds from the oldest timeout in the window, allow LLM again |

Resolution order for the config file: explicit path → `ISSUESMITH_CONFIG` → walk up from cwd → package repo-root fallback → builtin defaults.

### LLM-only (module-less) steps and `run-guarded --requires-step`

A step whose `module` key is absent (or empty) is an **LLM-only step**. Attempting to dispatch it via `dispatch` raises `andon(broken)` immediately; run it instead with `run-guarded --requires-step <step_id>`, which wraps the LLM call with gate evaluation:

- **Pre-phase**: gates whose `pre_llm=True` flag is set (`base_freshness`, `scope_breadth`) run before the LLM. A non-repairable violation (e.g. `scope_breadth`) raises `andon(decision)` with `widen:<files>`, `split`, and `reject` options.
- **Post-phase**: the full `requires` gate list runs after the LLM via `run_requires_loop`, which fetches a fresh issue body and rebuilds gates from scratch on every evaluation.

`requires: []` (explicit empty list) is valid and means "no gates for this step." A step that omits the `requires:` key entirely is a `validate_requires_chain` violation (except for `repair`).

`andon.answer("widen:<file1>,<file2>")` merges the listed files into `allow_paths` in the issue body YAML block and calls `resume(issue_num, from_step=step)` automatically. If the body has no YAML block, a comment is posted and no resume is triggered.

## Milestone chain (multi-repo)

`validate_children()` checks child Issues before C2 enqueues `develop`:

| Check | Rule |
|---|---|
| V1 `target_repo` | If the parent `### サブイシュー分割計画` table has a `対象リポジトリ` column, each child's YAML `target_repo` must match the plan row whose `タイトル` equals the child title. Values must be in `supported_repos`. Without that column (legacy 4-column plan), children are compared to the parent YAML `target_repo` as before. |
| V2 `allow_paths` | Only change-table rows whose `リポジトリ` equals the child's `target_repo` are required to be covered by the child's `allow_paths`. Rows for other repositories are ignored. |

`issuesmith milestone status <parent>` lists each child's `target_repo` (blank when unset).

## Gates

All gate ids usable in `steps.<id>.requires` in `issuesmith.yaml`. Issue gates evaluate the issue body / labels; worktree gates evaluate the checked-out worktree.

| id | input_kind | implementation |
|---|---|---|
| `b1_ac_format` | issue | gate_rules/b1_ac_format.py |
| `b1_migration` | issue | gate_rules/b1_migration.py |
| `b1_milestone_subdesign` | issue | gate_rules/b1_milestone_subdesign.py |
| `base_freshness` | worktree | gates/worktree.py (BaseFreshnessGate) |
| `cp1` | issue | gate_rules/cp1.py |
| `deps` | issue | gates/dep.py (DepsGate) |
| `external_leak` | worktree | gates/worktree.py (ExternalLeakGate) |
| `lint` | worktree | gates/worktree.py (LintGate) |
| `m2` | issue | gate_rules/m2.py |
| `milestone_consistency` | issue | gate_rules/milestone_consistency.py |
| `pr_scope` | worktree | gates/pr_scope.py (PrScopeGate) |
| `scope` | worktree | gates/scope.py (ScopeGate) |
| `scope_breadth` | issue | gate_rules/scope_breadth.py |
| `scope_coupling` | issue | gate_rules/scope_coupling.py |
| `tests` | worktree | gates/worktree.py (TestsGate) |

### Derived allow_paths for newly failing tests (#3756)

In the `run-guarded --requires-step` loop, `TestsGate` computes `derived_allow_paths`: test files
the repair step may edit even though they are outside `allow_paths`. A file qualifies only when
all of the following hold (deterministic; see `gates.worktree.derive_test_allow_paths`):

- it is under `tests/` and does not already match `allow_paths`;
- it has a test that passes on `origin/<base>` but fails on the branch (collection errors
  included), and the file exists on `origin/<base>`. No baseline → nothing is derived;
- its text references a changed file: the path itself, the dotted module name of a changed
  `src/**.py`, the file stem, or a public `def`/`class` name on a `+`/`-` diff line.

`run_requires_loop` keeps the union for the generation in `context["derived_allow_paths"]`,
`pr_scope` accepts those files, and the repair instruction lists them. On success
`run-guarded` prints a `derived_allow_paths:` block before the `PIPELINE_STATUS:` line, and CP2
reads it from `jobs/<p1_result_filename>` when checking the PR scope. The Issue body's
`allow_paths` and the queue's overlap check are unchanged.

Guard: `pr_scope` runs `check_derived_test_guard` on derived files. Fewer test functions or
`assert` statements than on base (or a file that no longer parses) is
`derived_allow.test_weakened`; more `skip` / `skipif` / `xfail` / `skipTest` references is
`derived_allow.test_skipped`. Both are repairable failures that end in andon(decision) after
`max_repairs`.

## Error Reference

| Type | Module | When |
|---|---|---|
| `QueueValidationError` | `issuesmith.queue_store` | Invalid queue request payload |
| `MetadataViolation` | `issuesmith.context_hook` | Issue YAML metadata fails validation |
| `ValueError` | various | Config / gate / engine argument errors |
| `TemplateVariableError` | `issuesmith.engine` | Missing template variables at render time |
| `RuntimeError` | `issuesmith.engine` | Engine pause / agent list-models failures |
| `FileNotFoundError` / `KeyError` | `issuesmith.ops.dispatch` | Missing template or substitution key |

## License

MIT (`MIT` SPDX). See [LICENSE](./LICENSE).
