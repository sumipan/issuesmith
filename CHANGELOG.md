# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Changed

- Milestone chain: when all children are `CLOSED` with `issuesmith:merge-done`, auto-close the parent (config `milestone_chain.auto_close_parent`, default `true`). Children closed without merge-done halt the chain for human review (#2959)

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
