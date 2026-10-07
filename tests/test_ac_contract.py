"""test_ac_contract.py — unit tests for acceptance-criteria YAML contract extract/run."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from issuesmith.ac_contract import (
    GateMaterializationError,
    contract_failures,
    dual_gate_roots,
    extract_contract_from_body,
    is_invalid_contract_path,
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


def test_manual_check_is_not_fail_and_is_listed_as_pending(tmp_path):
    records = run_checks(
        {"post_merge": [{"kind": "manual_check", "description": "check steps"}]},
        tmp_path,
    )
    assert [r for r in records if r["result"] == "FAIL"] == []
    assert pending_manual_checks(records) == ["check steps"]


def test_manual_check_mixed_with_other_kinds(tmp_path):
    records = run_checks(
        {
            "post_merge": [
                {"kind": "restart", "processes": ["release_watcher"]},
                {"kind": "manual_check", "description": "run preflight after MG1"},
            ]
        },
        tmp_path,
    )
    assert [r for r in records if r["result"] == "FAIL"] == []
    assert pending_manual_checks(records) == ["run preflight after MG1"]


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


def test_run_checks_references_symbol_string_fails_with_unsupported_form(tmp_path):
    """String path::symbol must fail with unsupported form detail regardless of file existence."""
    contract = {"references_must_resolve": ["src/x.py::sym"]}
    records = run_checks(contract, tmp_path)
    assert len(records) == 1
    r = records[0]
    assert r["result"] == "FAIL"
    assert "unsupported reference form" in r["detail"]
    assert "path::symbol" in r["detail"]
    assert "source file not found" not in r["detail"]


def test_run_checks_references_symbol_dict_fails_with_unsupported_form(tmp_path):
    """Dict {file: "path::symbol"} must fail with unsupported form detail."""
    contract = {"references_must_resolve": [{"file": "src/x.py::sym"}]}
    records = run_checks(contract, tmp_path)
    assert len(records) == 1
    r = records[0]
    assert r["result"] == "FAIL"
    assert "unsupported reference form" in r["detail"]
    assert "path::symbol" in r["detail"]
    assert "source file not found" not in r["detail"]


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


def test_run_checks_references_plain_string_pass_regression(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text("# doc\n", encoding="utf-8")
    contract = {"references_must_resolve": ["docs/README.md"]}
    records = run_checks(contract, tmp_path)
    assert records[0]["result"] == "PASS"


def test_run_checks_references_dict_file_only_pass_regression(tmp_path):
    (tmp_path / "config.yaml").write_text("a: 1\n", encoding="utf-8")
    contract = {"references_must_resolve": [{"file": "config.yaml"}]}
    records = run_checks(contract, tmp_path)
    assert records[0]["result"] == "PASS"


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


def test_extract_contract_reads_en_pack_heading(monkeypatch):
    """The shipped EN pack heading is enough to read paths_must_exist (#4472)."""
    import issuesmith.config as config_module
    from issuesmith.language import EN

    monkeypatch.setattr(config_module, "EN", EN)
    config_module.reset_config_cache()
    try:
        heading = config_module.get_config().sections["acceptance_criteria"]
        assert heading == EN.sections["acceptance_criteria"]
        body = (
            f"## {heading}\n\n```yaml\npaths_must_exist:\n  - src/app.py\n```\n\n"
            "- [ ] done\n"
        )
        assert extract_contract_from_body(body) == {"paths_must_exist": ["src/app.py"]}
    finally:
        config_module.reset_config_cache()


def test_extract_contract_follows_custom_pack_heading(tmp_path, monkeypatch):
    import dataclasses

    import issuesmith.config as config_module
    from issuesmith.language import EN

    sections = dict(EN.sections, acceptance_criteria="Done When")
    pack = dataclasses.replace(EN, sections=sections)
    cfg = dataclasses.replace(
        config_module.get_config(), language=pack, sections=dict(pack.sections)
    )
    monkeypatch.setattr(config_module, "_cached", cfg)
    body = "## Done When\n\n```yaml\npaths_must_exist:\n  - a.py\n```\n"
    assert extract_contract_from_body(body) == {"paths_must_exist": ["a.py"]}
    en_body = "## Acceptance Criteria\n\n```yaml\npaths_must_exist:\n  - a.py\n```\n"
    assert extract_contract_from_body(en_body) is None


# --- paths_must_exist glob expansion (#4803) ---


def test_paths_must_exist_glob_matches(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.txt").write_text("", encoding="utf-8")

    records = run_checks({"paths_must_exist": ["a/*.txt"]}, tmp_path)

    assert len(records) == 1
    assert records[0]["result"] == "PASS"
    assert records[0]["path"] == "a/*.txt"
    assert "glob matched 1" in records[0]["detail"]
    assert "a/x.txt" in records[0]["detail"]


def test_paths_must_exist_glob_matches_nothing(tmp_path):
    records = run_checks({"paths_must_exist": ["a/*.txt"]}, tmp_path)

    assert len(records) == 1
    assert records[0]["result"] == "FAIL"
    assert records[0]["path"] == "a/*.txt"
    assert records[0]["detail"] == "glob matched nothing"


def test_paths_must_exist_plain_path_keeps_detail(tmp_path):
    (tmp_path / "b.py").write_text("", encoding="utf-8")

    records = run_checks({"paths_must_exist": ["b.py", "missing.py"]}, tmp_path)

    by_path = {r["path"]: r for r in records}
    assert by_path["b.py"]["result"] == "PASS"
    assert by_path["b.py"]["detail"] == ""
    assert by_path["missing.py"]["result"] == "FAIL"
    assert by_path["missing.py"]["detail"] == "file not found"


def test_paths_must_exist_glob_generated_articles(tmp_path):
    # ASCII fixture of the generated-artifact contract that stopped M2.
    articles = tmp_path / "agents" / "person" / "sources" / "notion" / "articles"
    articles.mkdir(parents=True)
    (articles / "001.txt").write_text("", encoding="utf-8")
    (articles / "002.txt").write_text("", encoding="utf-8")
    pattern = "agents/person/sources/notion/articles/*.txt"

    records = run_checks({"paths_must_exist": [pattern]}, tmp_path)

    assert len(records) == 1
    assert records[0]["result"] == "PASS"
    assert records[0]["path"] == pattern
    assert "glob matched 2" in records[0]["detail"]


def test_paths_must_exist_glob_detail_lists_first_three(tmp_path):
    (tmp_path / "a").mkdir()
    for name in ("4.txt", "3.txt", "2.txt", "1.txt"):
        (tmp_path / "a" / name).write_text("", encoding="utf-8")

    records = run_checks({"paths_must_exist": ["a/*.txt"]}, tmp_path)

    assert records[0]["detail"] == "glob matched 4: a/1.txt, a/2.txt, a/3.txt"


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


def test_paths_must_not_exist_no_match_passes(tmp_path):
    records = run_checks({"paths_must_not_exist": ["legacy/*.jsonl"]}, tmp_path)

    assert records == [
        {
            "check": "paths_must_not_exist",
            "path": "legacy/*.jsonl",
            "result": "PASS",
            "detail": "",
            "git_log": "",
        }
    ]


def test_paths_must_not_exist_match_fails_per_file(tmp_path):
    (tmp_path / "legacy").mkdir()
    (tmp_path / "legacy" / "b.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "legacy" / "a.jsonl").write_text("", encoding="utf-8")

    records = run_checks({"paths_must_not_exist": ["legacy/*.jsonl"]}, tmp_path)

    detail = "glob matched: legacy/*.jsonl; matched: legacy/a.jsonl, legacy/b.jsonl"
    assert [(r["path"], r["result"], r["detail"]) for r in records] == [
        ("legacy/a.jsonl", "FAIL", detail),
        ("legacy/b.jsonl", "FAIL", detail),
    ]


def test_paths_must_not_exist_plain_path(tmp_path):
    (tmp_path / "old.py").write_text("", encoding="utf-8")

    records = run_checks({"paths_must_not_exist": ["old.py", "gone.py"]}, tmp_path)

    by_path = {r["path"]: r["result"] for r in records}
    assert by_path == {"old.py": "FAIL", "gone.py": "PASS"}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a/b.py", False),
        ("a/*.txt", False),
        ("./a.py", False),
        ("", True),
        ("  ", True),
        ("/abs/a.py", True),
        ("../a.py", True),
        ("a/../b.py", True),
        (1, True),
        (None, True),
    ],
)
def test_is_invalid_contract_path(value, expected):
    assert is_invalid_contract_path(value) is expected
