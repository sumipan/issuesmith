# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Fixed

- `engine check` が `DEFAULT_LIGHT_MODELS`（`issuesmith.yaml` の `engines.<role>.light_model.<engine>`）全項目を許可リストと突合し、外れていれば修正キーを案内する。`engine resolve --tier light` に `allowlist_valid` / `reason` を追加。`--engine` 上書き経路でも allowlist 外 light は heavy にフォールバックする（nexus #2981）
- `_iter_issuesmith_exec_records` が世代付き冪等キー（`issuesmith:impl:2959:1`、redispatch 時に ghdag が付与）の末尾要素を issue 番号と誤解釈し、再投入中の DAG を in_flight 外の「孤児」とみなして `pipeline not idle` で全 dispatch を止めていた。`issuesmith:<handler>:<issue>[:<generation>]` を正しく解釈する（2026-09-09、nexus #2959 の再投入で実測）

### Added

- Milestone chain multi-repo validation: V1 expects child `target_repo` from the parent
  split-plan `対象リポジトリ` column (falls back to parent `target_repo` when the column
  is absent); unsupported repos fail V1; V2 `allow_paths` checks only change-table rows
  for the child's `target_repo` (#2961)
- `issuesmith milestone status` shows each child's `target_repo`

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
