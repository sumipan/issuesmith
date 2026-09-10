```yaml
target_repo: sumipan/issuesmith
base_branch: main
allow_paths:
  - "src/issuesmith/**"
  - "tests/**"
  - "issuesmith.yaml"
```

## 背景・目的

issuesmith を「ghdag 上の汎用ワークフロー基盤」にするため、パッケージに焼き込まれた nexus 固有ポリシーを設定へ外出しする。

## 設計

### 方針

- 4 サブは同じ `src/issuesmith/**` を触るため直列。
- 各サブで `config.py` にキーを追加し、消費側はすべて `get_config()` 経由で読む。

### サブイシュー分割計画

| # | タイトル | 内容 | 依存 |
|---|--------|------|------|
| 1 | フェーズ定義を外出し | phases 設定化 | なし |
| 2 | セクション名を外出し | sections 設定化 | 1 |
| 3 | nexus 配置を外す | supported_repos 空既定 | 2 |
| 4 | ステップ実装を外出し | steps 設定化 | 3 |

#### Sub 1: フェーズ定義を phases 設定に外出しする

**スコープ**: PhaseConfig を定義し queue / recovery / milestone が config から導出する
**設計方針**: 既定は現行 4 フェーズ
**変更対象ファイル**:
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/issuesmith` | `src/issuesmith/config.py` | 修正 | PhaseConfig 追加 |

**受け入れ条件**:
- [ ] phases 未設定で現行動作
- [ ] カスタム phases で反映
- [ ] 3 エンジンで engine resolve 不変

#### Sub 2: セクション名を sections 設定に外出しする

**スコープ**: sections / sub_design_subsections を config 化
**設計方針**: 既定は現行日本語見出し
**変更対象ファイル**:
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/issuesmith` | `src/issuesmith/config.py` | 修正 | sections 追加 |

**受け入れ条件**:
- [ ] sections 未設定で現行動作
- [ ] カスタム見出しでゲートが追従
- [ ] re.escape 適用を確認

#### Sub 3: 既定値から nexus 配置を外す

**スコープ**: supported_repos 既定を空にし repo 必須化
**設計方針**: get_config 遅延評価でバリデーション
**変更対象ファイル**:
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/issuesmith` | `src/issuesmith/config.py` | 修正 | supported_repos 空既定 |

**受け入れ条件**:
- [ ] repo 未設定で明示エラー
- [ ] config show が JSON を返す
- [ ] nexus issuesmith.yaml は既存 repo で通る

#### Sub 4: ステップ実装を steps 設定に外出しする

**スコープ**: StepConfig と steps キー
**設計方針**: dispatch は config から導出
**変更対象ファイル**:
| リポジトリ | ファイルパス | 変更種別 | 変更内容 |
|---|---|---|---|
| `sumipan/issuesmith` | `src/issuesmith/ops/dispatch.py` | 修正 | steps 導出 |

**受け入れ条件**:
- [ ] steps 未設定で現行動作
- [ ] カスタム step でモジュール解決
- [ ] m2 compaction テンプレ名が設定化

## やらないこと

- skills/system-issuesmith の手順変更
