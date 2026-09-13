# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## Unreleased

### Added

- `pr_diff_scope` gate (`issuesmith.pr_scope.check_pr_diff_scope`): CP2 冒頭で
  PR 変更ファイルを `allow_paths` / `forbidden_pr_paths`（`jobs/**`・`*.jsonl` 等）と照合し、
  違反時はコメント + `CP2_FAILED` で M1/M2 を止める。P0 は worktree 作成後に `jobs/` dirty
  を検査する（#3178）
- `gate_rules.milestone_consistency`: 本文の分割計画パターン（`### サブイシュー分割計画` /
  `#### サブN:` / `#### Sub N:`）と `scope:milestone` ラベルの矛盾を検知する。
  CP1 `check_gate()` と B1 Verify に組み込み、誤って develop 高速経路へ進むのを防ぐ（#3077）
- `body_editor.normalize_sub_headers` / `relocate_sub_plan`: 英語サブ見出しの正規化と、
  `## 設計` 配下の分割計画を `## マイルストーン` へ移す決定論正規化（#3077）
- `issuesmith convert-to-milestone <N>`: develop 誤投入 Issue を milestone 経路へ
  1 コマンドで復旧（DAG cancel・ラベル・milestone オブジェクト・in_flight 除去・
  draft redispatch）。`--dry-run` 対応（#3077）
- `redispatch --phase sub`: milestone 経路の sub フェーズ再投入。`cmd_redispatch` は
  冒頭で stale `in_flight` を除去する（#3077）
- `gate_rules.cp1.check_test_version_exact_assert`: tests/ の `version == "X.Y"` /
  `git+https://…@vX.Y` 完全一致 assert を unified diff から検出する（#3065）
- Milestone chain multi-repo validation: V1 expects child `target_repo` from the parent
  split-plan `対象リポジトリ` column (falls back to parent `target_repo` when the column
  is absent); unsupported repos fail V1; V2 `allow_paths` checks only change-table rows
  for the child's `target_repo` (#2961)
- `issuesmith milestone status` shows each child's `target_repo`

### Fixed

- publish: `_ensure_rebased` が rebase 前に `RUNTIME_DIR_EXCLUDES`（`jobs/**`・
  `chat/**`・`sessions/**`・`logs/**`）配下の未コミット変更を破棄する。allow_paths
  内に汚れが残る場合は `PUBLISH_STATUS: DIRTY_WORKTREE`、本物の競合は
  `REBASE_CONFLICT`（競合ファイル一覧付き・`--abort`）を返す。nexus worktree で
  実行時ファイル汚れにより P3 が全件 `REBASE_CONFLICT` になる障害を防ぐ（#3227）
- publish: `_check_commit_diff_gates` の差分を二点ドット（`origin/<base>..HEAD`）から
  三点ドット（`origin/<base>...HEAD`、merge-base 起点）に変更。base が進んだだけで
  逆方向の `version =` 差分が写り `P3_GATE_FAILED` になる偽陽性を防ぐ。publish 前に
  `git fetch` + 必要時 `rebase` し、競合時は `PUBLISH_STATUS: REBASE_CONFLICT` を返す（#3221）
- M1: PR マージ成功後に `post_merge_test` だけ失敗したとき `issuesmith:merge-running` を付与し、
  M1r → M2 のラベル遷移欠落を防ぐ（#3221）
- M2 finalizer: `develop-done` / `merge-ready` / `merge-running` から `merge-done` へ到達可能にし、
  `transition()` がフェーズラベル欠落で失敗したときは `issue_update` で直付替する fallback を追加（#3221）
- queue: design engine（claude）が paused でも implementation ロールの
  `develop` / `merge` / `sub` は投入できるよう、paused 判定をフェーズ別ロールに変更。
  CP2（design）は `resume_at=null` でも固定インターバルで sleep & retry し、
  既定最大 6h まで REJECT を遅らせる。`call_managed` の timeout にも待機分を加算（#3091）
- queue: `draft-done` 直後の design スロット解放がラベルのみに依存していたため、
  brushup/impl の未完了 exec がある Issue が `in_flight` から消え、watcher が
  起動した impl DAG が追跡外になり `_dispatch_pipeline_ready` が queue 全体を
  止めていた。未完了 exec がある間は解放せず、`develop-running` なのに
  `in_flight` 不在の Issue は tick 時に再登録する。`queue status` /
  `queue doctor` で追跡外実行中 Issue を表示する（#3092）
- publish が commit 後・version bump 前に `check_version_line_in_diff` と
  `check_test_version_exact_assert` を実行し、LLM による `pyproject.toml` の
  `version =` 変更および tests/ の版・pin 完全一致 assert を `P3_GATE_FAILED` で止める（#3065）
- `pyproject.toml` の ghdag 依存バージョンを v0.35.0 から v0.44.0 に更新（`[ghdag]` / `[dev]` extras）
- `README.md` のバージョン表記・インストール手順を v0.1.0 → v0.10.0 に更新、冒頭に参照版と確認日を追加
- `src/issuesmith/github_api.py` のエラー文を旧ドット形式（`issuesmith.queue enqueue`）から統一 CLI 形式（`issuesmith queue enqueue`）に修正
- `engine check` が `DEFAULT_LIGHT_MODELS`（`issuesmith.yaml` の `engines.<role>.light_model.<engine>`）全項目を許可リストと突合し、外れていれば修正キーを案内する。`engine resolve --tier light` に `allowlist_valid` / `reason` を追加。`--engine` 上書き経路でも allowlist 外 light は heavy にフォールバックする（nexus #2981）
- `_iter_issuesmith_exec_records` が世代付き冪等キー（`issuesmith:impl:2959:1`、redispatch 時に ghdag が付与）の末尾要素を issue 番号と誤解釈し、再投入中の DAG を in_flight 外の「孤児」とみなして `pipeline not idle` で全 dispatch を止めていた。`issuesmith:<handler>:<issue>[:<generation>]` を正しく解釈する（2026-09-09、nexus #2959 の再投入で実測）

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
