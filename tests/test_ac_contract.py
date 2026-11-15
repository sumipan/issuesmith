"""test_ac_contract.py — unit tests for acceptance-criteria YAML contract extract/run."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from issuesmith.ac_contract import (
    GateMaterializationError,
    contract_failures,
    dual_gate_roots,
    extract_contract_from_body,
    normalize_reference_entry,
    pending_manual_checks,
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


def test_run_checks_references_invalid_entry_fails_without_raising(tmp_path):
    contract = {"references_must_resolve": [{"key_path": "a.b"}, 42]}

    records = run_checks(contract, tmp_path)

    assert [r["result"] for r in records] == ["FAIL", "FAIL"]
    assert all("invalid reference entry" in r["detail"] for r in records)


def test_manual_check_without_description_fails(tmp_path):
    for item in (
        {"kind": "manual_check"},
        {"kind": "manual_check", "description": ""},
        {"kind": "manual_check", "description": "   "},
        {"kind": "manual_check", "description": 1},
    ):
        records = run_checks({"post_merge": [item]}, tmp_path)
        schema = [r for r in records if r["check"] == "post_merge_schema"]
        assert len(schema) == 1, item
        assert schema[0]["result"] == "FAIL"
        assert "description" in schema[0]["detail"]
        assert pending_manual_checks(records) == []


def test_post_merge_unknown_kind_fails(tmp_path):
    records = run_checks({"post_merge": [{"kind": "manual"}]}, tmp_path)
    schema = [r for r in records if r["check"] == "post_merge_schema"]
    assert len(schema) == 1
    assert schema[0]["result"] == "FAIL"
    assert "unknown kind: manual" in schema[0]["detail"]


def test_contract_failures_ignores_manual_check(tmp_path):
    body = (
        "## Acceptance Criteria\n\n```yaml\npost_merge:\n"
        "  - kind: manual_check\n    description: check steps\n```\n"
    )
    assert contract_failures(body, repo_root=tmp_path) == []


# ---------------------------------------------------------------------------
# references_must_resolve — symbol form fails with unsupported form detail
# ---------------------------------------------------------------------------


def test_run_checks_references_symbol_fails_even_when_file_exists(tmp_path):
    """path::symbol must fail even if the file portion exists."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "x.py").write_text("", encoding="utf-8")
    contract = {"references_must_resolve": ["src/x.py::sym"]}
    records = run_checks(contract, tmp_path)
    assert len(records) == 1
    assert records[0]["result"] == "FAIL"
    assert "unsupported reference form" in records[0]["detail"]


# ---------------------------------------------------------------------------
# references_must_resolve — existing normal forms still pass (regression)
# ---------------------------------------------------------------------------


def test_run_checks_references_plain_string_pass(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text("# doc\n", encoding="utf-8")
    contract = {"references_must_resolve": ["docs/README.md"]}
    records = run_checks(contract, tmp_path)
    assert records[0]["result"] == "PASS"


# ---------------------------------------------------------------------------
# normalize_reference_entry — not_a_path (issue #5885)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ref",
    [
        "README.md -> TESTING.md",
        "a b.md",
        "../x.md",
        "/abs.md",
        {"file": "README.md -> TESTING.md"},
    ],
)
def test_normalize_reference_entry_not_a_path(ref):
    assert normalize_reference_entry(ref) == (None, None, "not_a_path")


def test_run_checks_records_not_a_path_without_existence_check(tmp_path):
    (tmp_path / "README.md").write_text("# doc\n", encoding="utf-8")
    contract = {"references_must_resolve": ["README.md -> TESTING.md"]}
    records = run_checks(contract, tmp_path)
    assert len(records) == 1
    r = records[0]
    assert r["result"] == "FAIL"
    assert r["detail"].startswith("not a file path")
    assert "source file not found" not in r["detail"]


def test_run_checks_references_dict_file_only_pass(tmp_path):
    (tmp_path / "config.yaml").write_text("a: 1\n", encoding="utf-8")
    contract = {"references_must_resolve": [{"file": "config.yaml"}]}
    records = run_checks(contract, tmp_path)
    assert records[0]["result"] == "PASS"


def test_run_checks_references_key_path_glob_value_fails_when_unmatched(tmp_path):
    (tmp_path / "tests" / "conventions").mkdir(parents=True)
    (tmp_path / "config.yaml").write_text(
        'ledger_globs:\n  - "tests/conventions/known_*.txt"\n',
        encoding="utf-8",
    )
    contract = {
        "references_must_resolve": [{"file": "config.yaml", "key_path": "ledger_globs"}]
    }

    records = run_checks(contract, tmp_path)

    ref_records = [r for r in records if r["check"] == "references_must_resolve"]
    assert len(ref_records) == 1
    r = ref_records[0]
    assert r["path"] == "config.yaml#ledger_globs=tests/conventions/known_*.txt"
    assert r["result"] == "FAIL"
    assert r["detail"] == "reference not found: tests/conventions/known_*.txt"


def test_run_checks_references_key_path_glob_value_matches_under_scripts(tmp_path):
    tools = tmp_path / "scripts" / "tools"
    tools.mkdir(parents=True)
    (tools / "x.py").write_text("", encoding="utf-8")
    (tmp_path / "config.yaml").write_text('ledger_globs:\n  - "tools/*.py"\n', encoding="utf-8")
    contract = {
        "references_must_resolve": [{"file": "config.yaml", "key_path": "ledger_globs"}]
    }

    records = run_checks(contract, tmp_path)

    ref_records = [r for r in records if r["check"] == "references_must_resolve"]
    assert len(ref_records) == 1
    assert ref_records[0]["path"] == "config.yaml#ledger_globs=tools/*.py"
    assert ref_records[0]["result"] == "PASS"


def test_dual_gate_roots_runs_checks_on_both_materialized_roots(tmp_path, monkeypatch):
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    for root in (primary, secondary):
        (root / "src").mkdir(parents=True)
        (root / "src" / "helper.py").write_text("", encoding="utf-8")

    contract = {"paths_must_exist": ["src/helper.py"]}

    with (
        patch("issuesmith.ac_contract.materialize_gate_root", side_effect=[primary, secondary]),
        patch("issuesmith.ac_contract.cleanup_gate_root") as mock_cleanup,
    ):
        with dual_gate_roots(primary, secondary, "main") as (left, right):
            left_records = run_checks(contract, left)
            right_records = run_checks(contract, right)

    assert all(r["result"] == "PASS" for r in left_records)
    assert all(r["result"] == "PASS" for r in right_records)
    assert mock_cleanup.call_count == 2


def test_dual_gate_roots_cleanup_on_materialize_failure(tmp_path):
    primary = tmp_path / "primary"
    primary.mkdir()
    with (
        patch("issuesmith.ac_contract.materialize_gate_root", side_effect=[primary, GateMaterializationError("fail")]),
        patch("issuesmith.ac_contract.cleanup_gate_root") as mock_cleanup,
    ):
        with pytest.raises(GateMaterializationError):
            with dual_gate_roots(primary, primary, "main"):
                pass
    mock_cleanup.assert_called_once_with(primary, primary)


def test_run_checks_references_none_source_fails_without_raising(tmp_path, monkeypatch):
    """Defense branch when normalize_reference_entry returns (None, None, None)."""
    import issuesmith.ac_contract as ac_contract

    monkeypatch.setattr(
        ac_contract,
        "normalize_reference_entry",
        lambda _ref: (None, None, None),
    )
    contract = {"references_must_resolve": ["docs/MLTGNT.md"]}

    records = run_checks(contract, tmp_path)

    assert len(records) == 1
    r = records[0]
    assert r["check"] == "references_must_resolve"
    assert r["result"] == "FAIL"
    assert "invalid reference entry" in r["detail"]
    assert r["git_log"] == ""


# --- paths_must_exist glob expansion (#4803) ---


@pytest.mark.parametrize("entry", ["/abs/*.txt", "../x.txt", "", "   ", 1, None])
def test_paths_must_exist_invalid_entry_fails_without_raising(tmp_path, entry):
    records = run_checks({"paths_must_exist": [entry]}, tmp_path)

    assert len(records) == 1
    assert records[0]["check"] == "paths_must_exist"
    assert records[0]["result"] == "FAIL"
    assert records[0]["path"] == str(entry)
    assert "invalid path" in records[0]["detail"]


@pytest.mark.parametrize("entry", ["/abs/*.txt", "../x.txt", ""])
def test_paths_must_not_exist_invalid_entry_fails_without_raising(tmp_path, entry):
    records = run_checks({"paths_must_not_exist": [entry]}, tmp_path)

    assert len(records) == 1
    assert records[0]["check"] == "paths_must_not_exist"
    assert records[0]["result"] == "FAIL"
    assert "invalid path" in records[0]["detail"]


def _git_repo(tmp_path: Path) -> Path:
    """Refs sumipan/nexus#6431 — minimal git repo for greps_must_be_empty checks."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@example.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    return tmp_path


def test_run_checks_greps_must_be_empty(tmp_path):
    """Refs sumipan/nexus#6431 — run_checks records PASS/FAIL per grep contract."""
    repo = _git_repo(tmp_path)
    pkg = repo / "pkg"
    pkg.mkdir()
    (pkg / "a.py").write_text("MARKER = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "pkg/a.py"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-q", "-m", "add"],
        check=True,
        capture_output=True,
    )
    contract = {
        "greps_must_be_empty": [
            {
                "pattern": "MARKER",
                "paths": ["pkg"],
            },
            {
                "pattern": "ABSENT",
                "paths": ["pkg"],
            },
        ]
    }
    records = run_checks(contract, repo)
    by_pattern = {r["path"]: r for r in records if r["check"] == "greps_must_be_empty"}
    assert by_pattern["MARKER"]["result"] == "FAIL"
    assert by_pattern["MARKER"]["detail"] == "pkg/a.py"
    assert by_pattern["ABSENT"]["result"] == "PASS"
    assert by_pattern["ABSENT"]["detail"] == ""


