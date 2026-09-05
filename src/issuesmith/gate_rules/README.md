# gate_rules

issuesmith のゲートチェック機構。各ゲートが `GateRule` プロトコルを実装し、`GATE_REGISTRY` に登録されることで、Issue body・ラベルに対するバリデーションが実行される。

## GateRule プロトコル

```python
class GateRule(Protocol):
    def check(self, body: str, labels: list[str]) -> list[Violation]: ...
```

| 引数 | 型 | 説明 |
|------|----|------|
| `body` | `str` | Issue body 全文（Markdown テキスト） |
| `labels` | `list[str]` | Issue に付与されたラベル名のリスト |
| 返り値 | `list[Violation]` | 検出した違反のリスト（問題なければ空リスト） |

## Violation

```python
@dataclass
class Violation:
    rule_id: str
    severity: str
    message: str
    location: str | None
    auto_fixable: bool
    fix_hint: str | None
```

| フィールド | 型 | 説明 |
|------------|----|------|
| `rule_id` | `str` | ルール識別子（例: `cp1.forbidden_word.todo`） |
| `severity` | `str` | 重大度。`"fail"` はブロッキング違反、`"warn"` は警告 |
| `message` | `str` | 人間向けエラーメッセージ |
| `location` | `str \| None` | 違反箇所のテキスト（省略可） |
| `auto_fixable` | `bool` | B1 フェーズで自動修正できるか |
| `fix_hint` | `str \| None` | 修正のヒント（省略可） |

## GATE_REGISTRY

```python
GATE_REGISTRY: dict[str, type[GateRule]] = {}
```

キーはゲート名（`"cp1"`, `"m2"` など）、値は `GateRule` を実装したクラス（インスタンスではなくクラス自体）。

`gate_rules/__init__.py` の末尾で各ゲートモジュールが import され、副作用として `GATE_REGISTRY` への登録が行われる。

## 新規ルール追加手順

1. `gate_rules/<gate_name>.py` を新規作成する
2. `GateRule` プロトコルに適合するクラスを定義し、`check()` を実装する

   ```python
   from issuesmith.gate_rules import GATE_REGISTRY, GateRule, Violation

   class MyGateRules:
       def check(self, body: str, labels: list[str]) -> list[Violation]:
           violations: list[Violation] = []
           # チェックロジックを実装
           return violations
   ```

3. ファイル末尾で `GATE_REGISTRY` に登録する

   ```python
   GATE_REGISTRY["<gate_name>"] = MyGateRules
   ```

4. `gate_rules/__init__.py` の末尾に import を追加する

   ```python
   import issuesmith.gate_rules.<gate_name>  # noqa: E402, F401
   ```

既存の実装例として `cp1.py`（CP1 ゲート）と `m2.py`（M2 ゲート）を参照。

## ユーティリティ（ghdag.workflow.gates.common）

実装は ghdag へ移設済み（`common.py` / `preflight.py` は本ディレクトリには存在しない）。

```python
from ghdag.workflow.gates.common import strip_code_regions

stripped = strip_code_regions(body)
```

`strip_code_regions(body: str) -> str` は、Issue body からフェンスコードブロック（` ``` ` で囲まれた範囲）とインラインコードスパン（`` `...` ``）を除去したテキストを返す。コードブロック内の禁則語を誤検知しないよう、`check()` 内でパターンマッチを行う前に呼び出す。

## 既存ルール一覧

### cp1 — 禁則語・意図的保留

Issue body（コードブロックを除く）に以下のパターンが含まれると `severity="fail"` 違反を返す。

| rule_id | 検出パターン | 備考 |
|---------|------------|------|
| `cp1.forbidden_word.todo` | `TODO:` | |
| `cp1.forbidden_word.tbd` | `TBD` | |
| `cp1.forbidden_word.youkakunin` | `要確認` | |
| `cp1.forbidden_word.mitei` | `未定`（`未定義` は除外） | 正規表現 `未定(?!義)` |
| `cp1.forbidden_word.kentouchuu` | `検討中` | |
| `cp1.forbidden_word.user_confirm` | `ユーザーに確認` | |
| `cp1.intentional_hold` | YAML frontmatter に `cp1_must_fail: true` | 意図的な保留として手動解除が必要（`auto_fixable: false`） |

禁則語違反はすべて `auto_fixable: true`（B1 フェーズで自動修正可）。

### m2 — 受け入れ条件セクション検証

| rule_id | 条件 | severity |
|---------|------|---------|
| `m2.ac_section_missing` | `## 受け入れ条件` セクションが存在しない | `warn` |
| `m2.unchecked_ac` | セクション内に未チェック checkbox（`- [ ]`）が 1 件以上ある | `fail` |

セクションが存在し、未チェック checkbox が 0 件なら空リストを返す。

## gate-preflight CLI

CLI 本体は `ghdag.workflow.gates.__main__` に移設済み。`python -m issuesmith gate-preflight` は issuesmith.gate_rules を import してルール登録した上で ghdag 側 CLI に委譲する薄いエントリポイントで、単一ゲートを任意の Issue body ファイルに対して実行できる。

```bash
# CP1 ゲートを body.md に対して実行
python -m issuesmith gate-preflight --gate cp1 --body-file body.md

# ラベルも考慮する場合（labels.txt は 1 行 1 ラベル）
python -m issuesmith gate-preflight --gate cp1 --body-file body.md --labels-file labels.txt

# M2 ゲートの実行例
python -m issuesmith gate-preflight --gate m2 --body-file body.md
```

出力は JSON 配列。違反なしの場合は `[]`、違反ありの場合は `Violation` オブジェクトのリスト。

```json
[
  {
    "rule_id": "cp1.forbidden_word.todo",
    "severity": "fail",
    "message": "TODO: が残存",
    "location": null,
    "auto_fixable": true,
    "fix_hint": "具体的な記述に置換してください"
  }
]
```
