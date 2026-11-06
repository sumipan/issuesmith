"""Derived allow_paths: ledger files and forbidden paths."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.gates.pr_scope import PrScopeGate
from issuesmith.gates.worktree import (
    TestsGate,
    check_derived_test_guard,
    derive_ledger_allow_paths,
)
from tests import test_derived_allow_paths as _base
from tests.test_derived_allow_paths import _LEDGER, _commit_all, _make_repo, _write

_derived_config = _base._derived_config
ledger_repo = _base.ledger_repo


# ---------------------------------------------------------------------------
# #4791: ratchet ledgers next to failing tests are derived and may only shrink
# ---------------------------------------------------------------------------

_LEDGER_GLOBS = ["tests/conventions/known_*.txt"]


def test_ledger_derived_from_failing_test_dir(ledger_repo: Path) -> None:
    derived = derive_ledger_allow_paths(
        ledger_repo, "main", ["tests/conventions/test_x.py::test_a"],
        ["src/pkg/gamma.py"], _LEDGER_GLOBS,
    )
    assert derived == [_LEDGER]


def test_ledger_not_derived_for_other_dir(ledger_repo: Path) -> None:
    derived = derive_ledger_allow_paths(
        ledger_repo, "main", ["tests/test_other.py::test_unrelated"],
        ["src/pkg/gamma.py"], _LEDGER_GLOBS,
    )
    assert derived == []


def test_ledger_not_on_base_not_derived(ledger_repo: Path) -> None:
    _write(ledger_repo, {"tests/conventions/known_new.txt": "x\n"})
    _commit_all(ledger_repo, "add ledger")
    derived = derive_ledger_allow_paths(
        ledger_repo, "main", ["tests/conventions/test_x.py::test_a"],
        ["src/pkg/gamma.py"], ["tests/conventions/known_*.txt"],
    )
    assert derived == [_LEDGER]


def test_ledger_already_allowed_not_derived(ledger_repo: Path) -> None:
    derived = derive_ledger_allow_paths(
        ledger_repo, "main", ["tests/conventions/test_x.py::test_a"],
        ["src/pkg/gamma.py", "tests/conventions/*.txt"], _LEDGER_GLOBS,
    )
    assert derived == []


def test_ledger_tests_gate_derives_ledger(ledger_repo: Path, _derived_config) -> None:
    _derived_config(None)
    gate = TestsGate(ledger_repo, allow_paths=["src/pkg/gamma.py"])
    violations = gate.check("", [])
    assert [v.rule_id for v in violations] == ["tests.pytest_failure"]
    assert gate.derived_allow_paths == [_LEDGER]


def test_ledger_tests_gate_disabled(ledger_repo: Path, _derived_config) -> None:
    _derived_config(False)
    gate = TestsGate(ledger_repo, allow_paths=["src/pkg/gamma.py"])
    assert gate.check("", [])
    assert gate.derived_allow_paths == []


def _ledger_guard(root: Path, content: str) -> list:
    _write(root, {_LEDGER: content})
    return check_derived_test_guard(root, "main", [_LEDGER], ledger_globs=_LEDGER_GLOBS)


def test_ledger_guard_delete_line_ok(ledger_repo: Path) -> None:
    assert _ledger_guard(ledger_repo, "alpha\nbeta\n") == []


def test_ledger_guard_reorder_ok(ledger_repo: Path) -> None:
    assert _ledger_guard(ledger_repo, "gamma\n  alpha\nbeta\n\n") == []


def test_ledger_guard_missing_file_ok(ledger_repo: Path) -> None:
    (ledger_repo / _LEDGER).unlink()
    assert check_derived_test_guard(
        ledger_repo, "main", [_LEDGER], ledger_globs=_LEDGER_GLOBS
    ) == []


def test_ledger_guard_added_line_grew(ledger_repo: Path) -> None:
    violations = _ledger_guard(ledger_repo, "alpha\nbeta\ndelta\n")
    assert [v.rule_id for v in violations] == ["derived_allow.ledger_grew"]
    assert violations[0].severity == "fail"
    assert violations[0].auto_fixable is False
    assert violations[0].location == _LEDGER
    assert "delta" in violations[0].message


def test_ledger_guard_without_globs_is_not_parsed_as_ledger(ledger_repo: Path) -> None:
    # The indented line is not valid Python: without ledger_globs the AST path reports
    # test_weakened; with them it is a reordered ledger and passes.
    content = "beta\n  alpha\n"
    _write(ledger_repo, {_LEDGER: content})
    violations = check_derived_test_guard(ledger_repo, "main", [_LEDGER])
    assert [v.rule_id for v in violations] == ["derived_allow.test_weakened"]
    assert "cannot parse working tree file" in violations[0].message
    assert _ledger_guard(ledger_repo, content) == []


def test_ledger_pr_scope_delete_only_passes(ledger_repo: Path, _derived_config) -> None:
    _derived_config(None)
    _write(ledger_repo, {_LEDGER: "alpha\nbeta\n"})
    gate = PrScopeGate(
        ledger_repo, ["src/pkg/gamma.py"], "main", derived_allow_paths=[_LEDGER],
    )
    assert gate.check("", []) == []


def test_ledger_pr_scope_added_line_grew(ledger_repo: Path, _derived_config) -> None:
    _derived_config(None)
    _write(ledger_repo, {_LEDGER: "alpha\nbeta\ngamma\ndelta\n"})
    gate = PrScopeGate(
        ledger_repo, ["src/pkg/gamma.py"], "main", derived_allow_paths=[_LEDGER],
    )
    assert [v.rule_id for v in gate.check("", [])] == ["derived_allow.ledger_grew"]


# ---------------------------------------------------------------------------
# #5421: PrScopeGate keeps forbidden_path; out-of-scope → pr_scope.out_of_allow
# ---------------------------------------------------------------------------


@pytest.fixture()
def forbidden_jsonl_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    clone = _make_repo(tmp_path, {
        "docs/lessons/issuesmith.jsonl": '{"a": 1}\n',
    })
    _write(clone, {"docs/lessons/issuesmith.jsonl": '{"a": 2}\n'})
    _commit_all(clone, "change ledger")
    cfg = tmp_path / "issuesmith.yaml"
    cfg.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "forbidden_pr_paths": ["[!s]*.jsonl"],
            "derived_allow": {"enabled": True},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg))
    reset_config_cache()
    return clone


def test_pr_scope_gate_preserves_forbidden_path(forbidden_jsonl_repo: Path) -> None:
    gate = PrScopeGate(
        forbidden_jsonl_repo,
        ["docs/lessons/issuesmith.jsonl"],
        "main",
    )
    violations = gate.check("", [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "pr_diff_scope.forbidden_path"
    assert v.location == "docs/lessons/issuesmith.jsonl"
    assert "forbidden path:" in v.message
    assert "[!s]*.jsonl" in v.message
    assert v.fix_hint == "git checkout main -- docs/lessons/issuesmith.jsonl"


def test_pr_scope_gate_out_of_allow_compat(tmp_path: Path) -> None:
    clone = _make_repo(tmp_path, {"scripts/x.py": "x = 1\n"})
    _write(clone, {"scripts/x.py": "x = 2\n"})
    _commit_all(clone, "change script")
    gate = PrScopeGate(clone, ["src/**"], "main")
    violations = gate.check("", [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "pr_scope.out_of_allow"
    assert v.location == "scripts/x.py"
    assert v.fix_hint == "widen:scripts/x.py"


def test_pr_scope_forbidden_skips_derived_guard(forbidden_jsonl_repo: Path) -> None:
    gate = PrScopeGate(
        forbidden_jsonl_repo,
        ["docs/lessons/issuesmith.jsonl"],
        "main",
        derived_allow_paths=["docs/lessons/issuesmith.jsonl"],
    )
    violations = gate.check("", [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pr_diff_scope.forbidden_path"
