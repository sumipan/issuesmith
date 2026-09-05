"""test_ac_contract.py — 受け入れ条件 YAML 契約の抽出・実行のユニットテスト。"""
from __future__ import annotations

from issuesmith.ac_contract import (
    contract_failures,
    extract_contract_from_body,
    run_checks,
)

BODY_WITH_CONTRACT = """\
## 設計

説明。

## 受け入れ条件

```yaml
paths_must_exist:
  - tests/test_migration.py
paths_must_not_exist:
  - legacy/*.jsonl
```

- [x] AC1
"""


def test_extract_contract_from_body():
    contract = extract_contract_from_body(BODY_WITH_CONTRACT)
    assert contract == {
        "paths_must_exist": ["tests/test_migration.py"],
        "paths_must_not_exist": ["legacy/*.jsonl"],
    }


def test_extract_returns_none_without_ac_section():
    assert extract_contract_from_body("## 概要\n本文のみ\n") is None


def test_extract_returns_none_without_yaml_block():
    assert extract_contract_from_body("## 受け入れ条件\n\n- [x] AC1\n") is None


def test_extract_returns_none_for_invalid_yaml():
    body = "## 受け入れ条件\n\n```yaml\n: : broken [\n```\n"
    assert extract_contract_from_body(body) is None


def test_extract_supports_header_variants():
    body = "## 7. 受け入れ条件（Acceptance Criteria）\n\n```yaml\npaths_must_exist:\n  - a.py\n```\n"
    assert extract_contract_from_body(body) == {"paths_must_exist": ["a.py"]}


def test_run_checks_pass_and_fail(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_migration.py").write_text("", encoding="utf-8")
    contract = {
        "paths_must_exist": ["tests/test_migration.py", "tests/missing.py"],
        "paths_must_not_exist": ["legacy/*.jsonl"],
    }

    records = run_checks(contract, tmp_path)

    by_path = {r["path"]: r["result"] for r in records}
    assert by_path["tests/test_migration.py"] == "PASS"
    assert by_path["tests/missing.py"] == "FAIL"
    assert by_path["legacy/*.jsonl"] == "PASS"


def test_run_checks_paths_must_not_exist_fails_on_match(tmp_path):
    (tmp_path / "legacy").mkdir()
    (tmp_path / "legacy" / "old.jsonl").write_text("", encoding="utf-8")

    records = run_checks({"paths_must_not_exist": ["legacy/*.jsonl"]}, tmp_path)

    assert [r["result"] for r in records] == ["FAIL"]


def test_contract_failures_returns_human_readable_fail_lines(tmp_path):
    failures = contract_failures(BODY_WITH_CONTRACT, repo_root=tmp_path)
    assert len(failures) == 1
    assert "tests/test_migration.py" in failures[0]


def test_contract_failures_empty_without_contract(tmp_path):
    assert contract_failures("## 概要\n本文\n", repo_root=tmp_path) == []
