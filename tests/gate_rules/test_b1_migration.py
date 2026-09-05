"""tests/gate_rules/test_b1_migration.py — b1_migration ゲートのユニットテスト。"""
from __future__ import annotations

import issuesmith.gate_rules.b1_migration  # noqa: F401 — registers on import
from issuesmith.gate_rules import GATE_REGISTRY

MIGRATION_LABELS = ["scope:migration"]
NON_MIGRATION_LABELS = ["scope:feature"]

MIGRATION_PROCEDURE_SKELETON = """\
## マイグレーション手順

（MG1 が実行するコマンドをここに記述する）

```bash
# 例: /var/tmp/mltgnt を main 最新に更新
cd /var/tmp/mltgnt && git fetch origin && git checkout main && git pull origin main
pip install -e "/var/tmp/mltgnt/[dev]" --no-deps

# マージ済みファイルの存在確認
test -f <対象ファイル> && echo "OK: file exists"
```
"""


def _check(body: str, labels: list[str]):
    gate = GATE_REGISTRY["b1_migration"]()
    return gate.check(body, labels)


def _rule_ids(body: str, labels: list[str]) -> set[str]:
    return {v.rule_id for v in _check(body, labels)}


# 3 要件（手順・実行時状態調査・移行検証テスト契約）をすべて満たす body
BODY_COMPLETE = """\
## 影響範囲調査

some impact analysis

### 実行時状態の調査

- **永続 state ファイル**: logs/.diary-observer-state.json
- **untracked 実データ**: logs/.diary-observer-snapshot-*.md
- **データ間の不変条件**: hash(snapshot) == state.handled_hash
- **途中停止時の復旧**: atomic write（tempfile + os.replace）で自己復旧可能

## マイグレーション手順

```bash
test -f tools/foo.py && echo "OK"
```

## 受け入れ条件

```yaml
paths_must_exist:
  - tests/tools/secretary/test_observer_migration.py
```

- [ ] 移行テストが通ること
"""

BODY_WITHOUT_MIGRATION_PROCEDURE = """\
## 影響範囲調査

some impact analysis
"""

BODY_MISSING_STATE_SURVEY = """\
## 影響範囲調査

some impact analysis

## マイグレーション手順

```bash
test -f tools/foo.py && echo "OK"
```

## 受け入れ条件

```yaml
paths_must_exist:
  - tests/test_migration.py
```
"""

BODY_MISSING_TEST_CONTRACT = """\
## 影響範囲調査

### 実行時状態の調査

- **永続 state ファイル**: （該当なし）

## マイグレーション手順

```bash
test -f tools/foo.py && echo "OK"
```

## 受け入れ条件

```yaml
paths_must_exist:
  - tools/foo.py
```
"""


def test_no_migration_label_returns_empty():
    assert _check(BODY_WITHOUT_MIGRATION_PROCEDURE, NON_MIGRATION_LABELS) == []


def test_empty_labels_returns_empty():
    assert _check(BODY_WITHOUT_MIGRATION_PROCEDURE, []) == []


def test_migration_label_with_complete_body_returns_empty():
    assert _check(BODY_COMPLETE, MIGRATION_LABELS) == []


def test_migration_label_without_section_returns_procedure_violation():
    violations = _check(BODY_WITHOUT_MIGRATION_PROCEDURE, MIGRATION_LABELS)
    by_id = {v.rule_id: v for v in violations}
    v = by_id["b1_migration.migration_procedure_missing"]
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert v.fix_hint == MIGRATION_PROCEDURE_SKELETON


def test_missing_everything_returns_all_three_violations():
    assert _rule_ids(BODY_WITHOUT_MIGRATION_PROCEDURE, MIGRATION_LABELS) == {
        "b1_migration.migration_procedure_missing",
        "b1_migration.state_survey_missing",
        "b1_migration.verification_test_missing",
    }


def test_missing_state_survey_returns_violation_with_skeleton():
    violations = _check(BODY_MISSING_STATE_SURVEY, MIGRATION_LABELS)
    by_id = {v.rule_id: v for v in violations}
    assert set(by_id) == {"b1_migration.state_survey_missing"}
    v = by_id["b1_migration.state_survey_missing"]
    assert v.auto_fixable is True
    assert "実行時状態の調査" in v.fix_hint
    assert "永続 state ファイル" in v.fix_hint


def test_empty_state_survey_section_is_violation():
    body = BODY_MISSING_STATE_SURVEY.replace(
        "some impact analysis",
        "some impact analysis\n\n### 実行時状態の調査\n",
    )
    assert "b1_migration.state_survey_missing" in _rule_ids(body, MIGRATION_LABELS)


def test_missing_test_contract_returns_violation():
    violations = _check(BODY_MISSING_TEST_CONTRACT, MIGRATION_LABELS)
    by_id = {v.rule_id: v for v in violations}
    assert set(by_id) == {"b1_migration.verification_test_missing"}
    assert "paths_must_exist" in by_id["b1_migration.verification_test_missing"].fix_hint


def test_tools_scoped_tests_path_satisfies_contract():
    body = BODY_MISSING_TEST_CONTRACT.replace(
        "  - tools/foo.py",
        "  - tools/issuesmith/tests/test_foo_migration.py",
    )
    assert _check(body, MIGRATION_LABELS) == []


def test_registered_in_gate_registry():
    assert "b1_migration" in GATE_REGISTRY
