"""test_ac_contract.py — unit tests for acceptance-criteria YAML contract extract/run."""
from __future__ import annotations

from issuesmith.ac_contract import (
    contract_failures,
    extract_contract_from_body,
    run_checks,
)

# ASCII fixture data.
BODY_WITH_CONTRACT = """\
## Design

Description.

## Acceptance Criteria

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
    assert extract_contract_from_body("## Overview\nbody only\n") is None


def test_extract_returns_none_without_yaml_block():
    # ASCII fixture data.
    assert extract_contract_from_body("## Acceptance Criteria\n\n- [x] AC1\n") is None


def test_extract_returns_none_for_invalid_yaml():
    # ASCII fixture data.
    body = "## Acceptance Criteria\n\n```yaml\n: : broken [\n```\n"
    assert extract_contract_from_body(body) is None


def test_extract_supports_header_variants():
    # ASCII fixture data.
    body = "## 7. Acceptance Criteria_cFF08Acceptance CriteriacFF09\n\n```yaml\npaths_must_exist:\n  - a.py\n```\n"
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
    assert contract_failures("## Overview\nbody\n", repo_root=tmp_path) == []


def test_run_checks_references_plain_string_checks_file_existence(tmp_path):
    """Plain-string form ("docs/FOO.md") checks file existence only; no TypeError (#3290)."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "MLTGNT.md").write_text("# not yaml\n", encoding="utf-8")
    contract = {"references_must_resolve": ["docs/MLTGNT.md", "docs/MISSING.md"]}

    records = run_checks(contract, tmp_path)

    by_path = {r["path"]: r["result"] for r in records if r["check"] == "references_must_resolve"}
    assert by_path == {"docs/MLTGNT.md": "PASS", "docs/MISSING.md": "FAIL"}


def test_run_checks_references_dict_without_key_path_checks_file_existence(tmp_path):
    (tmp_path / "config.yaml").write_text("a: 1\n", encoding="utf-8")
    contract = {"references_must_resolve": [{"file": "config.yaml"}]}

    records = run_checks(contract, tmp_path)

    assert [(r["path"], r["result"]) for r in records] == [("config.yaml", "PASS")]


def test_run_checks_references_invalid_entry_fails_without_raising(tmp_path):
    contract = {"references_must_resolve": [{"key_path": "a.b"}, 42]}

    records = run_checks(contract, tmp_path)

    assert [r["result"] for r in records] == ["FAIL", "FAIL"]
    assert all("invalid reference entry" in r["detail"] for r in records)
