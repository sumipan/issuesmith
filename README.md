# issuesmith

issuesmith is a GitHub Issue label-driven workflow framework that runs on [ghdag](https://github.com/sumipan/ghdag). It provides gates, queue triage, context hooks, and a unified CLI for pipelines that advance Issues through design → implementation → merge via labels — not CI YAML alone.

## Status

![stability](https://img.shields.io/badge/stability-pre--1.0-orange)
![version](https://img.shields.io/badge/version-v0.1.0-blue)
![ci](https://github.com/sumipan/issuesmith/actions/workflows/ci.yml/badge.svg?branch=main)
![python](https://img.shields.io/badge/python-%3E%3D3.10-blue)
![license](https://img.shields.io/badge/license-MIT-green)

Current release is **v0.1.0** (pre-1.0). Interfaces may evolve before `1.0.0`.

## Installation

```bash
pip install "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.1.0"
```

With ghdag (required for gate-preflight and most runtime paths):

```bash
pip install "issuesmith[ghdag] @ git+https://github.com/sumipan/issuesmith.git@v0.1.0"
```

| Item | Value |
|---|---|
| Python requirement | `>=3.10` |
| Runtime dependencies | `pyyaml`, `ruamel.yaml`, `packaging`, `python-dotenv` |
| Optional / recommended | `ghdag @ git+https://github.com/sumipan/ghdag.git@v0.35.0` (`[ghdag]` or `[dev]`) |
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
| `queue` | Night / draft queue tick and status |
| `deps` | Extract Issue dependencies |
| `tier` | Choose B1 / CP2 model tier (`tier b1` / `tier cp2`) |
| `comments` | Pipeline comment helpers |
| `gh` | GitHub Issue/PR helpers via ghdag client (not the `gh` CLI) |
| `engine` | LLM role switcher / runner |
| `dispatch` | Render and enqueue a workflow template |
| `publish` | Publish / version-bump orchestration |
| `labels hygiene` | Label hygiene maintenance |
| `doctor` | Preflight / environment checks |
| `smoke` | Template smoke against live Issue bodies |
| `gen-live` | Generate live dispatch payloads |
| `version-bump` | Deterministic package version bump |
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
  engine.py           LLM role switcher / metrics
  queue.py            Queue dispatch loop
  queue_store.py      Persistent queue state
  queue_triage.py     LLM triage / title normalization
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

Orchestration (polling, DAG, label transitions) remains in **ghdag** `WorkflowDispatcher`. This package supplies the Issue-domain tools and gates that templates invoke.

## Configuration

| Name | Kind | Description |
|---|---|---|
| `ISSUESMITH_CONFIG` | env | Absolute path to `issuesmith.yaml` |
| `ISSUESMITH_QUEUE_DIR` | env | Override directory for queue / triage files |
| `AGENT_SKILLS_DIR` | env | Skills directory for doctor/preflight (default `~/.agents/skills`) |
| `METRICS_JSONL_PATH` | env | Override metrics JSONL path for `issuesmith.engine` |
| `ISSUESMITH_TIMEOUT_SEC` | env | Override engine timeout seconds |
| `issuesmith.yaml` | file | Repo / paths / engines / supported_repos (see Quick Start) |

Resolution order for the config file: explicit path → `ISSUESMITH_CONFIG` → walk up from cwd → package repo-root fallback → builtin defaults.

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
