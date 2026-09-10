# issuesmith 基盤 / nexus ポリシー境界

issuesmith を「ghdag 上の汎用ワークフロー基盤」へ寄せるための境界マップ。
コードは変更しない。後続の設定化 milestone が本表を参照する。

分類基準:

| 分類 | 意味 |
|---|---|
| **基盤** | 他プロジェクトへ導入してもそのまま使える汎用ロジック |
| **nexus ポリシー** | sumipan/nexus 運用に固有の値・名称・パス（ほぼ全体がポリシー） |
| **混在** | 1 ファイル内に基盤ロジックと nexus ポリシーが同居 |

nexus ポリシーの典型例: (a) `sumipan/*` リポジトリ名のハードコード、(b) `jobs/` `logs/` `.claude/worktrees` 等の配置パス、(c) `draft` / `sub` / `develop` / `merge` のフェーズ名リテラル、(d) 日本語セクション名（`受け入れ条件`・`設計`・`変更対象ファイル` 等）の直接参照。

調査時点: issuesmith パッケージ（本リポジトリ）の `src/issuesmith/`（`find … -name '*.py' | sort` で 41 ファイル）。

---

## 第 1 表: モジュール分類表

`src/issuesmith/` 直下・`gate_rules/`・`ops/`・`steps/` の全 `.py`（`__init__.py`・`__main__.py` 含む）。

| モジュールパス | 分類 | nexus ポリシー行 | 備考 |
|---|---|---|---|
| `__init__.py` | 基盤 | — | パッケージ説明のみ |
| `__main__.py` | 基盤 | — | CLI エントリ |
| `ac_contract.py` | 混在 | L95: `受け入れ条件` セクション抽出 regex | 契約実行（paths_must_exist 等）は基盤。見出し文字列の設定化が必要 |
| `b1_tier.py` | nexus ポリシー | L14: `## 背景・目的` / `## 設計` | ファイル全体が見出しリテラル依存。設定化容易 |
| `b1_verify.py` | 基盤 | — | gate レジストリのオーケストレーションのみ（`milestone_consistency` 含む） |
| `body_editor.py` | 基盤 | — | ghdag shim。`normalize_sub_headers` / `relocate_sub_plan` は決定論正規化（#3077） |
| `cli.py` | 基盤 | — | サブコマンド配線（`convert-to-milestone` 含む） |
| `config.py` | 混在 | L27–37: `_DEFAULT_SUPPORTED_REPOS`（sumipan 9 リポ）; L41–57: `_DEFAULT_REL_PATHS`（`jobs/`・`logs/`・`.claude/worktrees` 等）; L284: 既定 `repo=sumipan/nexus` | YAML ロード骨格は基盤。既定値が nexus 焼き込み |
| `context_hook.py` | 混在 | L294: `## やらないこと` 参照; L16 docstring / worktree パス組み立ては config 経由 | diary / cross-repo 文脈は nexus 寄り。パス自体は config 化済み |
| `convert_to_milestone.py` | 混在 | develop/sub ラベル名・`scope:milestone`・`NNNN-YYYYMMDD` milestone タイトル | 復旧 CLI。DAG cancel / ラベル / milestone / in_flight / redispatch（#3077） |
| `cp1_gate.py` | 基盤 | — | `gate_rules.cp1` / `b1_migration` / `milestone_consistency` への薄ラッパ |
| `cp2_tier.py` | 基盤 | — | diff 行数・AC 未チェック数の閾値判定（見出し非依存） |
| `dep_extractor.py` | 混在 | L80–81: `依存（先行）` セクション名 | 依存グラフ抽出ロジックは基盤 |
| `engine.py` | 基盤 | — | LLM role switcher。L47 コメントの `jobs/metrics.jsonl` は説明のみ（実パスは config） |
| `github_api.py` | 混在 | L8: `draft\|develop\|merge`-ready 禁止 regex; L24: Issue 作成を nexus のみ許可 | ghdag CLI ラッパ。作成先制限は組織ポリシー |
| `m2_gate.py` | 混在 | docstring / help が `受け入れ条件`・`sumipan/nexus` を言及。実チェックは `gate_rules.m2` へ委譲 | マルチ root 契約合成は基盤 |
| `milestone.py` | 混在 | L46: `**変更対象ファイル**`; L233: `## 依存（先行）`; L615: phase 順 `merge/develop/draft/sub`; L265+: `supported_repos` | チェーン骨格は基盤。見出し・フェーズ・supported_repos がポリシー |
| `pipeline_comments.py` | 混在 | L11–15: `## B1 ` 等のパイプラインコメント接頭辞; L30–31: `## B1 事前検証失敗:` / `未マージ:` | フィルタ骨格は基盤。接頭辞は nexus テンプレ契約 |
| `queue_store.py` | 混在 | L17: `Phase = Literal["draft","sub","develop","merge"]`; L21: `PHASES` | JSONL / lock / enqueue 骨格は基盤 |
| `queue_triage.py` | 混在 | L39–54: phase→ready/running/done ラベル表 | トリアージ骨格は基盤。ラベル名空間は config 化候補 |
| `queue.py` | 混在 | L55–59: `PHASE_ROLE`; L1508: `--phase choices`; 各所の phase 分岐 | ディスパッチ骨格は基盤。フェーズ名が全域に浸透 |
| `recovery.py` | 混在 | L31: `jobs/` を含む order パス regex; phase→step と `--phase choices`（`sub` 含む） | 復旧計画骨格は基盤。`redispatch --phase sub` / `remove_in_flight`（#3077） |
| `targets.py` | 混在 | L35/46/51/54: エラー・doc が「受け入れ条件」を言及（抽出は `ac_contract` 経由） | multi-target モデル自体は基盤 |
| `gate_rules/__init__.py` | 基盤 | — | ルールモジュールの import 副作用登録 |
| `gate_rules/b1_ac_format.py` | nexus ポリシー | L13: `## 受け入れ条件`; L28: `## 設計` + 変更対象ファイルテーブル | AC YAML 形式ゲート全体がポリシー |
| `gate_rules/b1_migration.py` | nexus ポリシー | L54: `## マイグレーション手順`; L148+: `受け入れ条件` YAML 契約文言 | migration 専用。見出し・契約キーが固定 |
| `gate_rules/b1_milestone_subdesign.py` | nexus ポリシー | L9: `_REQUIRED_SUBSECTIONS`（スコープ/設計方針/変更対象ファイル/受け入れ条件）; L45+: `## 設計` | マイルストーン分割設計の日本語スキーマ |
| `gate_rules/milestone_consistency.py` | nexus ポリシー | `scope:milestone` / `サブイシュー分割計画` / `#### Sub N:` | 分割計画とラベル矛盾・正規化違反の検知（#3077） |
| `gate_rules/cp1.py` | 混在 | L19–20: fix hint に `sumipan/nexus`; L163+: `**受け入れ条件**`; L238: 「変更対象ファイル」 | GateRule 骨格は基盤。検証内容はポリシー |
| `gate_rules/m2.py` | nexus ポリシー | L10/L16: `## 受け入れ条件` 存在・未チェック数 | M2 checkbox ゲート本体 |
| `ops/__init__.py` | 基盤 | — | パッケージマーカー |
| `ops/dispatch.py` | 混在 | L41–43: `_STEP_MODULES = {"m2-role-dispatch": "m2_finalize"}` | ライブ再展開ランナー骨格は基盤。step→module 固定がポリシー |
| `ops/gen_live_dispatch.py` | 基盤 | — | dispatch テンプレ生成。パスは config |
| `ops/label_hygiene.py` | 混在 | L34–64: `issuesmith:draft-*` / `develop-*` / `merge-*` 共存禁止表 | 衛生チェック骨格は基盤。フェーズラベル名がポリシー |
| `ops/preflight.py` | 混在 | L40: `sumipan/ghdag.git` pin regex | 環境検査骨格は基盤 |
| `ops/publish.py` | 混在 | L21–31: `RUNTIME_*_EXCLUDES` に `jobs/**`・`logs/**` 等 | PR 作成骨格は基盤。実行時ログ除外パスが nexus 配置前提 |
| `ops/smoke.py` | 混在 | L45/L169–180: draft/develop/merge/sub ラベル遷移ペア | smoke 骨格は基盤 |
| `ops/version_bump.py` | 基盤 | — | CHANGELOG / version 決定論バンプ |
| `steps/__init__.py` | 基盤 | — | パッケージマーカー |
| `steps/base.py` | 基盤 | — | `StepContext` / `StepResult` |
| `steps/m2_finalize.py` | 混在 | L271: `m2-compact.md` 直接参照; L337: `/\.claude/worktrees/issue-…` cleanup regex; 各所の develop/merge ラベルと日本語コメント | finalize 骨格は基盤。テンプレ名・worktree パス・フェーズラベルがポリシー |

**突合用 `find` 出力（AC-1）:**

```text
src/issuesmith/__init__.py
src/issuesmith/__main__.py
src/issuesmith/ac_contract.py
src/issuesmith/b1_tier.py
src/issuesmith/b1_verify.py
src/issuesmith/body_editor.py
src/issuesmith/cli.py
src/issuesmith/config.py
src/issuesmith/context_hook.py
src/issuesmith/convert_to_milestone.py
src/issuesmith/cp1_gate.py
src/issuesmith/cp2_tier.py
src/issuesmith/dep_extractor.py
src/issuesmith/engine.py
src/issuesmith/gate_rules/__init__.py
src/issuesmith/gate_rules/b1_ac_format.py
src/issuesmith/gate_rules/b1_migration.py
src/issuesmith/gate_rules/b1_milestone_subdesign.py
src/issuesmith/gate_rules/cp1.py
src/issuesmith/gate_rules/m2.py
src/issuesmith/gate_rules/milestone_consistency.py
src/issuesmith/github_api.py
src/issuesmith/m2_gate.py
src/issuesmith/milestone.py
src/issuesmith/ops/__init__.py
src/issuesmith/ops/dispatch.py
src/issuesmith/ops/gen_live_dispatch.py
src/issuesmith/ops/label_hygiene.py
src/issuesmith/ops/preflight.py
src/issuesmith/ops/publish.py
src/issuesmith/ops/smoke.py
src/issuesmith/ops/version_bump.py
src/issuesmith/pipeline_comments.py
src/issuesmith/queue_store.py
src/issuesmith/queue_triage.py
src/issuesmith/queue.py
src/issuesmith/recovery.py
src/issuesmith/steps/__init__.py
src/issuesmith/steps/base.py
src/issuesmith/steps/m2_finalize.py
src/issuesmith/targets.py
```

（41 ファイル。上表と 1:1。）

---

## 第 2 表: nexus ポリシー項目一覧

第 1 表から導出した項目。設定化 milestone 向けのキー名案付き。

### フェーズ名リテラル

| 項目 | 現在の定義箇所 | 設定化キー名案 | 既定値 | 参照する消費者モジュール |
|---|---|---|---|---|
| `Phase` / `PHASES` | `queue_store.py` L17, L21 | `phases` | `["draft","sub","develop","merge"]` | `queue.py`, `queue_triage.py`, `milestone.py`, `recovery.py` |
| `PHASE_ROLE`（phase→engine role） | `queue.py` L55–59 | `phases.<name>.role` | draft→design / 他→implementation | `queue.py`（enqueue / dispatch） |
| CLI `--phase choices` | `queue.py` L1508; `recovery.py` L460 | （`phases` から導出） | 同上（recovery は sub 除外） | CLI 利用者 |
| phase→failed step | `recovery.py` L411 | `phases.<name>.failed_step` | draft→b1 / develop→cp2 / merge→m2 | `recovery.py` |
| phase 優先順（子ラベル判定） | `milestone.py` L615 | `phases` の順序 | merge→develop→draft→sub | `milestone.py` |
| phase→ready/running/done ラベル | `queue_triage.py` L39–54 | `phases.<name>.labels.{ready,running,done}` | `issuesmith:{phase}-{ready\|running\|done}` | `queue.py`, `milestone.py`, `ops/smoke.py`, `ops/label_hygiene.py`, `steps/m2_finalize.py` |
| ready ラベル直接付与禁止 | `github_api.py` L8 | （上記 labels.ready から導出） | `draft\|develop\|merge`-ready | `github_api.py` |

### 日本語セクション名

| 項目 | 現在の定義箇所 | 設定化キー名案 | 既定値 | 参照する消費者モジュール |
|---|---|---|---|---|
| 受け入れ条件（H2） | `ac_contract.py` L95; `gate_rules/m2.py` L10/L16; `gate_rules/b1_ac_format.py` L13 | `section_names.acceptance_criteria` | `受け入れ条件` | `ac_contract.py`, `m2_gate.py`, `targets.py`, `gate_rules/b1_migration.py`, `gate_rules/cp1.py`, `steps/m2_finalize.py` |
| 設計（H2） | `b1_tier.py` L14; `gate_rules/b1_ac_format.py` L28; `gate_rules/b1_milestone_subdesign.py` L45 | `section_names.design` | `設計` | `b1_tier.py`, `gate_rules/b1_ac_format.py`, `gate_rules/b1_milestone_subdesign.py` |
| 背景・目的（H2） | `b1_tier.py` L14 | `section_names.background` | `背景・目的` | `b1_tier.py` |
| 変更対象ファイル | `milestone.py` L46; `gate_rules/b1_milestone_subdesign.py` L9/L87/L128; `gate_rules/cp1.py` L238 | `section_names.change_files` | `変更対象ファイル` | `milestone.py`, `gate_rules/b1_milestone_subdesign.py`, `gate_rules/b1_ac_format.py`, `gate_rules/cp1.py` |
| やらないこと（H2） | `context_hook.py` L294 | `section_names.out_of_scope` | `やらないこと` | `context_hook.py` |
| 依存（先行）（H2） | `dep_extractor.py` L80–81; `milestone.py` L233 | `section_names.dependencies` | `依存（先行）` | `dep_extractor.py`, `milestone.py` |
| マイグレーション手順（H2） | `gate_rules/b1_migration.py` L54 | `section_names.migration` | `マイグレーション手順` | `gate_rules/b1_migration.py`, `cp1_gate.py`（scope:migration 時） |
| サブ設計必須サブセクション | `gate_rules/b1_milestone_subdesign.py` L9 | `section_names.milestone_subsections` | `スコープ` / `設計方針` / `変更対象ファイル` / `受け入れ条件` | `gate_rules/b1_milestone_subdesign.py` |

### nexus 配置パス

| 項目 | 現在の定義箇所 | 設定化キー名案 | 既定値 | 参照する消費者モジュール |
|---|---|---|---|---|
| supported_repos | `config.py` L27–37 | `supported_repos`（既存キー。既定の焼き込み解消） | sumipan 9 リポ | `context_hook.py`, `milestone.py`, `config` 利用者全般 |
| 既定 repo | `config.py` L284 | `repo`（既存） | `sumipan/nexus` | `config.py` → 全モジュール |
| queue / state / lock / triage | `config.py` L42–45 | `paths.queue` 等（既存） | `jobs/issuesmith-queue.jsonl` 等 | `queue_store.py`, `queue_triage.py` |
| exec_jsonl / done_dir / quota / metrics | `config.py` L48–51 | `paths.exec_jsonl` 等（既存） | `jobs/exec.jsonl` 等 | `queue.py`, `recovery.py`, `engine.py` |
| worktrees_dir / external_dir | `config.py` L52–53 | `paths.worktrees_dir` / `paths.external_dir`（既存） | `.claude/worktrees` / `.claude/external` | `context_hook.py`, `steps/m2_finalize.py`（L337 はパス文字列を再ハードコード） |
| template_dir / workflow | `config.py` L54–55 | `paths.template_dir` 等（既存） | `workflows/issuesmith` | `ops/dispatch.py`, `ops/smoke.py`, `ops/gen_live_dispatch.py`, `steps/m2_finalize.py` |
| publish 実行時ログ除外 | `ops/publish.py` L21–31 | `publish.runtime_excludes` | `jobs/**`, `logs/**`, `chat/**`, `sessions/**` 等 | `ops/publish.py` |
| order パス中の `jobs/` | `recovery.py` L31 | （`paths` から導出する regex） | `…jobs/….md` | `recovery.py` |
| ghdag git URL（sumipan） | `ops/preflight.py` L40 | `deps.ghdag_git_url` | `git+https://github.com/sumipan/ghdag.git@…` | `ops/preflight.py` |
| Issue 作成許可 repo | `github_api.py` L24 | `issue_create.allowed_repo` | `DEFAULT_REPO`（nexus）のみ | `github_api.py` |

### ステップ実装固定

| 項目 | 現在の定義箇所 | 設定化キー名案 | 既定値 | 参照する消費者モジュール |
|---|---|---|---|---|
| step_id → Python モジュール | `ops/dispatch.py` L41–43 | `steps.<id>.module` | `m2-role-dispatch` → `m2_finalize` | `ops/dispatch.py` |
| M2 コンパクションテンプレ名 | `steps/m2_finalize.py` L271 | `steps.m2.compaction_template` | `m2-compact.md` | `steps/m2_finalize.py` |
| worktree cleanup パスパターン | `steps/m2_finalize.py` L337 | `paths.worktrees_dir` から組み立て（現状ハードコード） | `/\.claude/worktrees/issue-{n}(-\|/\|$)` | `steps/m2_finalize.py` |
| パイプラインコメント接頭辞 | `pipeline_comments.py` L11–15 | `pipeline_comment_prefixes` | `## B1 ` / `## CP1 ` / … | B1/CP1 テンプレ（nexus 側） |

---

## 第 3 表: nexus テンプレ由来の移設候補

nexus リポジトリ（`sumipan/nexus`）の `workflows/issuesmith/*.md` に残る決定論ロジックのうち、issuesmith パッケージへ移す候補。

| テンプレファイル | 該当行 | 内容 | 移設先案 |
|---|---|---|---|
| `workflows/issuesmith/sub-ready.md` | L627–730 | SUB1 pre-creation validation（V1–V5）。`milestone.validate_children`（`milestone.py` L257+）と二重実装 | `issuesmith.milestone`（単一実装に統合し、テンプレは Python 呼び出しのみ） |
| `workflows/issuesmith/m1-merge.md` | L102–224（`[1/5]` PR 検索: Stage 0 / 0.5 / 1 / 2 / 3） | PR 特定の多段フォールバック（コメント branch・PR URL・head・全文・timeline） | `issuesmith.ops` または新モジュール `issuesmith.pr_resolve`（決定論 CLI） |
| `workflows/issuesmith/p0-worktree.md` | L75–186（`prepare_worktree` と cross-repo / diary 分岐） | worktree 冪等作成・既存検証・base ref 解決。パス規約は nexus 配置前提 | `issuesmith.ops.worktree`（config の `paths.worktrees_dir` / `external_dir` を使用） |

補足（移設候補の周辺）:

- `m1-merge.md` の `[-/5]`〜`[3/5]`（L29–270 付近）もトークン権限・companion PR・既マージ・mergeStateStatus を含む決定論ブロックで、同様にパッケージ化候補。
- `sub1-role-dispatch.md` 自体は `engine run-guarded` の薄いラッパ（9 行）で、ロジック本体は `sub-ready.md` 側。

---

## やらないこと（本ドキュメントの範囲外）

- コード・テストの変更
- `issuesmith.yaml` へのキー追加実装
- README の改訂（release-watcher の X.Y バンプ起票が担う）
