"""
tests/tools/issuesmith/test_m2_gate.py

M2 checkbox gate のユニットテスト。
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from issuesmith.m2_gate import check_gate, get_unchecked_count, has_acceptance_criteria_section


BODY_NO_SECTION = """\
## 背景

無関係なコンテンツ。

- [ ] これはセクション外のチェックボックス
"""

BODY_ALL_CHECKED = """\
## 受け入れ条件

- [x] 完了した項目
- [x] これも完了
"""

BODY_ONE_UNCHECKED = """\
## 受け入れ条件

- [x] 完了した項目
- [ ] 未完了の項目
"""

BODY_ALL_UNCHECKED = """\
## 受け入れ条件

- [ ] 未完了 1
- [ ] 未完了 2
"""

BODY_UNCHECKED_ONLY_IN_SECTION = """\
## その他

- [ ] セクション外（カウント対象外）

## 受け入れ条件

- [ ] セクション内 1

## 次のセクション

- [ ] 別セクション（カウント対象外）
"""

BODY_WITH_YAML_PREAMBLE = """\
```yaml
base_branch: main
allow_paths: ["**"]
```

## 背景

説明文。

## 受け入れ条件

- [ ] AC1
- [x] AC2
"""


class TestHasAcceptanceCriteriaSection:
    def test_returns_false_when_no_section(self):
        assert not has_acceptance_criteria_section(BODY_NO_SECTION)

    def test_returns_true_when_section_exists(self):
        assert has_acceptance_criteria_section(BODY_ALL_CHECKED)

    def test_returns_true_with_unchecked(self):
        assert has_acceptance_criteria_section(BODY_ONE_UNCHECKED)

    def test_returns_false_for_empty_body(self):
        assert not has_acceptance_criteria_section("")


class TestGetUncheckedCount:
    def test_returns_zero_when_no_section(self):
        assert get_unchecked_count(BODY_NO_SECTION) == 0

    def test_returns_zero_when_all_checked(self):
        assert get_unchecked_count(BODY_ALL_CHECKED) == 0

    def test_returns_one_when_one_unchecked(self):
        assert get_unchecked_count(BODY_ONE_UNCHECKED) == 1

    def test_returns_two_when_all_unchecked(self):
        assert get_unchecked_count(BODY_ALL_UNCHECKED) == 2

    def test_counts_only_items_in_section(self):
        assert get_unchecked_count(BODY_UNCHECKED_ONLY_IN_SECTION) == 1

    def test_with_yaml_preamble(self):
        assert get_unchecked_count(BODY_WITH_YAML_PREAMBLE) == 1


class TestCheckGate:
    def test_no_section_returns_proceed(self):
        result = check_gate(BODY_NO_SECTION, [])
        assert result["action"] == "proceed"
        assert result["has_section"] is False
        assert result["unchecked_count"] == 0

    def test_all_checked_returns_proceed(self):
        result = check_gate(BODY_ALL_CHECKED, [])
        assert result["action"] == "proceed"
        assert result["unchecked_count"] == 0

    def test_unchecked_no_migration_label_returns_retry(self):
        result = check_gate(BODY_ONE_UNCHECKED, ["scope:enhancement"])
        assert result["action"] == "retry"
        assert result["unchecked_count"] == 1
        assert result["has_migration_label"] is False

    def test_unchecked_with_migration_label_returns_migrate(self):
        result = check_gate(BODY_ALL_UNCHECKED, ["scope:migration", "issuesmith:merge-running"])
        assert result["action"] == "migrate"
        assert result["unchecked_count"] == 2
        assert result["has_migration_label"] is True

    def test_unchecked_empty_labels_returns_retry(self):
        result = check_gate(BODY_ONE_UNCHECKED, [])
        assert result["action"] == "retry"

    def test_all_checked_with_migration_label_returns_proceed(self):
        result = check_gate(BODY_ALL_CHECKED, ["scope:migration"])
        assert result["action"] == "proceed"

    def test_no_section_with_migration_label_returns_proceed(self):
        result = check_gate(BODY_NO_SECTION, ["scope:migration"])
        assert result["action"] == "proceed"
        assert result["has_section"] is False

    def test_result_contains_all_keys(self):
        result = check_gate(BODY_ONE_UNCHECKED, ["scope:migration"])
        assert set(result.keys()) == {
            "action",
            "unchecked_count",
            "has_section",
            "has_migration_label",
            "contract_failures",
        }


BODY_CONTRACT_PASS = """\
## 受け入れ条件

```yaml
paths_must_exist:
  - pyproject.toml
```

- [x] AC1
"""

BODY_CONTRACT_FAIL = """\
## 受け入れ条件

```yaml
paths_must_exist:
  - tests/does/not/exist_migration_test.py
```

- [x] AC1
"""


class TestCheckGateContract:
    def test_checked_ac_with_passing_contract_returns_proceed(self):
        result = check_gate(BODY_CONTRACT_PASS, [])
        assert result["action"] == "proceed"
        assert result["contract_failures"] == []

    def test_checked_ac_with_failing_contract_returns_retry(self):
        result = check_gate(BODY_CONTRACT_FAIL, [])
        assert result["action"] == "retry"
        assert result["unchecked_count"] == 0
        assert any(
            "exist_migration_test.py" in failure
            for failure in result["contract_failures"]
        )

    def test_unchecked_ac_skips_contract_check(self):
        # checkbox が未完了なら契約実行前に retry/migrate へ落ちる
        result = check_gate(BODY_ONE_UNCHECKED, [])
        assert result["action"] == "retry"
        assert result["contract_failures"] == []

    def test_contract_execution_error_is_fail_open(self, monkeypatch):
        import issuesmith.ac_contract as ac_contract

        def boom(body, repo_root=None):
            raise RuntimeError("simulated contract failure")

        monkeypatch.setattr(ac_contract, "contract_failures", boom)
        result = check_gate(BODY_CONTRACT_FAIL, [])
        assert result["action"] == "proceed"
        assert result["contract_failures"] == []


BODY_CROSS_REPO_CONTRACT = """\
## 受け入れ条件

```yaml
paths_must_exist:
  - src/mltgnt/loops
```

- [x] AC1
"""


class TestCheckGateRepoRoot:
    """クロスレポ: paths_must_exist は --repo-root / repo_root で解決する。"""

    def test_external_repo_root_with_path_returns_proceed(self, tmp_path: Path):
        external = tmp_path / "mltgnt"
        (external / "src" / "mltgnt" / "loops").mkdir(parents=True)
        # nexus 側には同パスが無い前提を明示（tmp_path 自体を誤ルートに見立てる）
        assert not (tmp_path / "src" / "mltgnt" / "loops").exists()

        result = check_gate(BODY_CROSS_REPO_CONTRACT, [], repo_root=external)
        assert result["action"] == "proceed"
        assert result["contract_failures"] == []

    def test_wrong_repo_root_returns_retry_with_failure(self, tmp_path: Path):
        wrong_root = tmp_path / "nexus-like"
        wrong_root.mkdir()
        assert not (wrong_root / "src" / "mltgnt" / "loops").exists()

        result = check_gate(BODY_CROSS_REPO_CONTRACT, [], repo_root=wrong_root)
        assert result["action"] == "retry"
        assert any(
            "paths_must_exist: src/mltgnt/loops" in failure
            for failure in result["contract_failures"]
        )

    def test_unchecked_skips_contract_even_with_repo_root(self, tmp_path: Path):
        result = check_gate(BODY_ONE_UNCHECKED, [], repo_root=tmp_path)
        assert result["action"] == "retry"
        assert result["contract_failures"] == []

    def test_unchecked_migration_skips_contract_even_with_repo_root(self, tmp_path: Path):
        result = check_gate(BODY_ONE_UNCHECKED, ["scope:migration"], repo_root=tmp_path)
        assert result["action"] == "migrate"
        assert result["contract_failures"] == []


class TestMainRepoRootCli:
    def test_main_passes_repo_root_to_check_gate(self, tmp_path: Path, monkeypatch, capsys):
        external = tmp_path / "mltgnt"
        (external / "src" / "mltgnt" / "loops").mkdir(parents=True)

        mock_client = MagicMock()
        mock_client.issue_get.return_value = {
            "body": BODY_CROSS_REPO_CONTRACT,
            "labels": [],
        }
        monkeypatch.setattr(
            "issuesmith.m2_gate.GitHubClient",
            lambda: mock_client,
        )
        monkeypatch.setattr(
            "sys.argv",
            ["m2-gate", "2515", "--repo-root", str(external)],
        )

        from issuesmith.m2_gate import main

        main()
        out = json.loads(capsys.readouterr().out)
        assert set(out.keys()) == {
            "action",
            "unchecked_count",
            "has_section",
            "has_migration_label",
            "contract_failures",
        }
        assert out["action"] == "proceed"
        assert out["contract_failures"] == []
