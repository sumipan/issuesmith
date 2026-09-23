# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).


## 0.55.1 - 2026-09-23

### Fixed

- `dispatch`: the `QuotaGate` used to register a deferred task now gets `brake_state_path`
  (`paths.brake_state`). Without it `release_ready` saw every engine as available and re-queued
  a brake-paused task on the next tick, so the step ran, deferred, and ran again (sumipan/nexus#3627
  cp2 launched twice within four minutes while all engines were brake-paused)

## 0.53.1 - 2026-09-23

### Fixed

- `observe.policy.execute` raises each andon **once per occurrence** instead of re-emitting it to
  the sinks on every tick (a halted milestone produced a Slack post every 30 minutes,
  sumipan/nexus#3621). `QueueStore.sync_observe_andons` remembers the ids whose condition is
  still present; an id is forgotten when the condition clears, so a recurrence is reported once
  more. `AndonAction.key` gives the condition a stable identity (`stall:<phase>`,
  `orphan:<uuid>`, `chain_halted`, `timeout:<uuid>`, `systemic:<step>:<class>`,
  `forge_unavailable`, `skew:<package>`) independent of counters in the summary
- `execute(..., client=)` makes the andon canonical for Issue-bound actions (Issue comment +
  attention label through `raise_andon`, sinks included); `observe --apply` passes the forge
  client. Calls without `client` keep the sink-only behaviour

## 0.53.0 - 2026-09-23

### Added

- CI runs `mypy src/issuesmith/` and `pytest --cov=issuesmith --cov-fail-under=72`
  (OSS_QUALITY chapter 8 section 8.5, sumipan/nexus#3574). `[tool.mypy]` (python 3.10,
  `warn_unused_ignores`, missing imports ignored only for ghdag / yaml) and `[tool.coverage.run]`
  in `pyproject.toml`; `pytest-cov` and `mypy` in the `dev` extra
- `src/issuesmith/py.typed` (PEP 561), shipped through `[tool.setuptools.package-data]`

### Changed

- Coverage floor is 72: measured 72.2% on 2026-09-23 (9922 statements, 2757 missed, full suite),
  above the 70 floor ghdag uses, so the measured value is the one that must not drop
- mypy baseline: 35 errors on 33 lines silenced with `# type: ignore[<code>]  # TODO(#3611)`
  (12 `ForgePort.api_request` attr-defined, 3 `Template.get_identifiers`, 14 Optional handling,
  1 `FailureClass` call-arg, 1 no-redef); 10 stale `type: ignore` comments removed. The dead
  `ops.label_hygiene` import in `cli.py` is silenced by a `[tool.mypy.overrides]` block tagged
  `TODO(#3612)` because its error code depends on the environment.
  Reducing the backlog is tracked in sumipan/nexus#3611
- CI installs ghdag from the pyproject pin instead of a hardcoded `v0.52.0`
## 0.52.1 - 2026-09-23

### Added

- `tests/conventions/`: structural tests (OSS_QUALITY chapter 8, sumipan/nexus#3571). CLI table vs
  usage / docs / deprecation advice, lazily imported handler modules, `__all__` resolution with a
  shrinking list of private cross-module imports, no nexus workflow vocabulary in verbs / gates /
  observe / andon / labels / dispatch / engine, every ghdag name used exists in the pinned ghdag, and
  write / read-back round trips on `LocalForge` (andon raise / list / answer, label apply,
  `MERGE_DONE` projection vs `labels.project`, queue enqueue / in-flight / complete). Each
  detector has a reproduction test for the incident it guards against (#3566, #3515, #3507)
- `doctor` prints `upstream_apis: ok` / `upstream_apis: missing: ...` from
  `ops.preflight.REQUIRED_UPSTREAM_APIS` (ghdag names reached at runtime through getattr)

### Known violations (strict xfail, remove the entry when fixed)

- `cli.py` `labels` handler imports the removed `issuesmith.ops.label_hygiene` (sumipan/nexus#3612)

## 0.52.0 - 2026-09-23

### Changed

- `dispatch`: a `RetrySignal` now registers the running task with `QuotaGate.defer` (uuid from
  `GHDAG_TASK_UUID`, role engines from `ROLE_ENGINES`) and prints `PIPELINE_STATUS: DEFERRED`,
  so ghdag marks the task `DONE_DEFERRED` and `release_ready` re-queues it once an engine is
  available again (sumipan/nexus#3515). Previously the task exited 0 with no status and stayed
  waiting until a manual `resume`
- ghdag pin raised to `v0.68.0` (exports `GHDAG_TASK_UUID` to launched tasks)

## 0.51.0 - 2026-09-22

### Changed

- `gate_rules.scope_coupling` requires follow-up only for callers / tests of public symbols
  defined in the files the Issue changes and for basenames of deleted or moved files
  (declaration-derived keys). Basenames of `allow_paths` entries and identifiers defined elsewhere
  are reported for reference in `fix_hint` and never widen `allow_paths`, so the requirement no
  longer grows with `allow_paths`. When widening would exceed `scope_breadth.max_files`, the
  message says so (sumipan/nexus#3527).
- Issue YAML `scope_mode: internal` declares an unchanged public interface; `scope_coupling`
  returns no violations for it (sumipan/nexus#3527).

## 0.50.4 - 2026-09-22

### Fixed

- `observe`: `orphan_exec` reads the issue number from the third segment of the idempotency key,
  so generation-suffixed keys (`issuesmith:impl:3548:1`) no longer report issue `#1`
  (sumipan/nexus#3523).

## 0.50.3 - 2026-09-22

### Fixed

- `ops.labels.project`: the `queued` marker is additive. A queued issue keeps its phase label
  (for example `draft-done`, which `queue._phase_preconditions` requires before `develop`), so
  `labels reconcile --fix` no longer strips a label that dispatch depends on (sumipan/nexus#3601).

## 0.50.2 - 2026-09-22

### Fixed

- `observe --json` no longer raises `TypeError: Object of type frozenset is not JSON serializable`:
  `LabelDriftEvent.add` / `.remove` are sorted tuples and the CLI passes a JSON default for sets
  (sumipan/nexus#3590).
- `resume --from <step>` accepts `--workflow` (default: stem of `paths.workflow`, e.g. `issuesmith`)
  and `--handler`, and forwards `--workflow` to `ghdag dag recover`. Hosts with several workflow
  files previously got `multiple workflows found ... specify --workflow` on every call
  (sumipan/nexus#3590).

## 0.50.1 - 2026-09-22

### Added

- `tests/conftest.py`: session-scoped autouse `_no_side_effects` fixture that guards against
  filesystem writes outside pytest's tmp directory. The fixture redirects module-level path
  constants (`QUOTA_STATE_PATH`, `BRAKE_STATE_PATH`, `EXEC_PATH`, `DONE_DIR`, `JOBS_DIR`,
  `DEFAULT_TRIAGE_LOG_PATH`) to `basetemp` and sets `ISSUESMITH_QUEUE_DIR` so `dispatch_one`
  and triage writes stay within the test sandbox. Any write outside `basetemp` raises
  `AssertionError("side effect outside tmp: <path>")`. A session finalizer asserts that
  `jobs/`, `logs/`, and `.pipeline-state/` do not exist at the repo root after the suite.
- `tests/test_no_side_effects.py`: smoke tests verifying the write-guard harness (AC-1) and
  that no runtime directories appear at repo root (AC-2).

## 0.48.1 - 2026-09-22

### Added

- `scope_coupling.enabled` (default `true`): set `false` in `issuesmith.yaml` to turn the
  CP1/B1 scope coupling gate off. The gate demands every caller of a core module and
  contradicts `scope_breadth` (nexus #3431 / #3504 / #3522); it stays off until redesigned
  in nexus #3527.

## 0.43.1 - 2026-09-21

### Fixed

- `steps.cp2_checkpoint`: `success_statuses` no longer lists `CP2_SKIPPED`. Since 0.43.0 (#3505)
  `engine.run_guarded` raises `ValueError` for `*_SKIPPED` success markers, so every CP2 failed in
  its deterministic prefix before the review started and raised an empty `decision` andon
  (sumipan/nexus#3506, 2026-09-21). `CP2_SKIPPED` now falls through to `CP2_FAILED` (R2).
- `steps.cp2_checkpoint.run` re-raises `engine.RetrySignal` so a paused engine is deferred by
  `ops.dispatch` instead of being reported as a review failure; other exceptions print a
  `REASON:` line before `CP2_FAILED`.

## 0.42.0 - 2026-09-21

### Changed

- `engine._execute` no longer sleeps up to `ISSUESMITH_ENGINE_WAIT_MAX_SEC` while every allowed engine is
  paused; it raises `RetrySignal(reason=QUOTA_PAUSED, after=resume_at)` immediately and `ops.dispatch`
  registers a ghdag `QuotaGate` defer and applies the `<namespace>:waiting` label (exit 0, no
  `DEP_FAILED`). Merged as PR #63 for sumipan/nexus#3485 without a version bump, so v0.41.0 did not
  include it; this release exists to publish it.

### Added

- `issuesmith.contract`: single canonical parser for the change table (bold label and heading forms)
  used by the B1 gate, milestone helpers and SUB1 alike; `CONTRACT_EXTRACTORS` names the functions
  that may only be defined there (#3487)
- `gate_rules.PREFLIGHT_PARITY` + `tests/test_workflow_conventions.py`: deterministic detector for
  the workflow design conventions (R1 one parser per contract section, R3 every runtime stop status
  has a preflight rule or an explicit runtime-only reason) (#3487)
- `b1_milestone_subdesign.change_paths_unreadable`: B1 fails when a `#### サブN` change table yields
  no path for its repo — the exact condition under which SUB1 cannot build a child (#3487)
- `scope_breadth.root_unavailable`: CP1 fails closed when the cross-repo clone is missing (#3487)
- `tests/contract/`: fixture parity tests feeding the same body to gate and step (#3487)
- `steps.scope_gate.resolve_scope_root`: single root resolver shared by CP1 (`gate_rules.scope_breadth`),
  `gate-preflight`, and P0 (`steps.p0_worktree`) — replaces the private `_resolve_root` copy that
  diverged from what P0 measured (#3487)
- CP1 / `b1_verify` narrow oversized `allow_paths` deterministically to the union of the change table
  and the AC `paths_must_exist` list and continue, instead of failing, when the narrower set is itself
  within threshold; the rewritten allow_paths are persisted to the Issue body and a comment records the
  before/after (#3487)
- `scope_gate.record_p0_trip_metric` + P0 comment wording: a P0 trip after CP1 passed is flagged as a
  gate contradiction (workflow defect) and recorded as `scope_gate.p0_trip` in `jobs/metrics.jsonl` (#3487)

### Fixed

- SUB1 never inherits the parent's `allow_paths`; a row whose change table is unreadable fails
  instead of creating a child with the parent's `tests/**` (nexus #3483 tripped P0 with 91 files) (#3487)
- `scope_breadth` measured `.claude/external/<owner>/<repo>` (never exists) and passed silently;
  now uses `paths.external_dir/<repo>`, the layout `context_hook` actually clones into (#3487)

- `tests/test_no_cjk.py`: CI gate enforcing zero CJK characters in the fully-migrated test files
  (`steps/test_m1_merge.py`, `test_body_editor_shim.py`, `gate_rules/test_cp1_gate.py`) (#3385)
- `tests/gate_rules/test_cp1_gate.py`: ASCII-only duplicate of core CP1 gate tests, no Japanese
  fixture data (#3385)

### Changed

- `tests/steps/test_m1_merge.py`: Replace Japanese PR/issue titles in live-capture fixtures
  with English equivalents (#3385)
- `tests/test_body_editor_shim.py`: Replace Japanese section names with English in all assertions
  (#3385)
- `tests/test_body_editor_normalize.py`: Inject English config in `relocate_sub_plan` tests;
  replace hardcoded Japanese section names with config-driven English names (#3385)

### Fixed

- `steps.sub1_create._run_guarded_body`: `resolve("implementation", "default")` が TIERS（light / heavy）外で
  ValueError になり、子 Issue の LLM 本文生成が常にスキップされて雛形（設計空・AC「(from parent)」）のまま
  起票されていた。tier を渡さない（state のモデルをそのまま使う）ように修正（nexus #3379 / #3322）
- `steps.sub1_create._resolve_dependencies`: 依存の連番参照を substring 置換していたため `#2, #3` が
  `#3382` 置換後に `#3` が `#3382` 内へマッチして `#3383382` になり、子 Issue 検証（V4）で chain が
  halted になっていた。トークン単位の正規表現置換に変更し、milestone 参照の除外も同じ経路に統合（nexus #3379 / #3301）

### Fixed

- `ac_contract.run_checks`: `references_must_resolve` の plain string 形式（`- docs/FOO.md`）を
  「ファイルが存在すること」の検査として受理し、`key_path` 省略の dict も同様に扱う。
  従来は `TypeError: string indices must be integers` で M2 が落ちていた（nexus #3290）
- `steps.m2_finalize._evaluate_dual_root`: 契約実行中の例外を fail-open（stderr ログ + 基本判定を返す）にし、
  契約フォーマット変異で impl ステップ全体がクラッシュしないようにする（nexus #3290）

### Added

- P0 **scope_gate** (`steps.scope_gate` / `config.ScopeGateConfig`): worktree 作成後に
  `allow_paths` 一致の追跡ファイル数・行数を計測し、既定閾値（80 files / 20000 lines）超過時は
  Issue コメント + `issuesmith:scope-too-large` + `SCOPE_TOO_LARGE` で P1 を止める。
  Issue YAML の `scope_gate.max_files` で個別上書き可（`hard_max_files` は CP1 が検証）（#3349）
- `paths.brake_state`: issuesmith budget gate（既定フォールバックは `quota_state`）を追加し、
  queue / engine の pause 判定を global quota gate と budget gate の和集合にする。
  `call_managed` と `rate_limit_detected` の書き込み先は `quota_state` に固定する（#3263）
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

- engine: 全エンジン pause 時に `resume_at=None`（budget-brake 等）でも最大 30〜60 分
  一括 sleep せず、`ISSUESMITH_ENGINE_WAIT_POLL_SEC`（既定 60 秒）ごとに
  `QuotaGate.snapshot()` を再取得する。待機と LLM 実行の合計は `ISSUESMITH_TIMEOUT_SEC`
  に収め、待機予測分を `call_managed` timeout に加算しない。2026-09-13 の nexus #3252
  CP2 で codex pause 解除後も長時間再確認されず `DAG_TASK_TIMEOUT` と二重実行した事象の修正（#3256）
- publish: rebase 後の再 push で non-fast-forward にならないよう、リモート tip が
  `HEAD` の祖先でないときは `git push --force-with-lease=<branch>:<remote_sha>` で
  更新する。lease 失敗（他者の未知コミット等）は `PUBLISH_STATUS: PUSH_DIVERGED`
  を返す。push 前に `fetch` し `rev-list` の ahead/behind を stdout に残す（#3237）
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
