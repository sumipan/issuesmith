"""scope_coupling: code-constant pinning and repository root config references."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from issuesmith.gate_rules import scope_coupling
from issuesmith.gate_rules.scope_coupling import (
    CODE_CONSTS_PIN_RULE_ID,
    ScopeCouplingRules,
    _deletion_search_pathspecs,
    check_code_consts_pinning,
    check_deletion_references,
)
from tests.gate_rules import test_scope_coupling as _base
from tests.gate_rules.test_scope_coupling import _MODIFY_ROW, _body, _init_repo, _write_config

_clear_config_cache = _base._clear_config_cache


# ---------------------------------------------------------------------------
# check_code_consts_pinning (nexus #5095)
# ---------------------------------------------------------------------------


def _code_consts_body(
    allow_paths: list[str],
    *,
    rows: str = "",
    scope_mode_internal: bool = False,
) -> str:
    body = _body(allow_paths, rows=rows or _MODIFY_ROW)
    if scope_mode_internal:
        body = body.replace("base_branch: main\n", "base_branch: main\nscope_mode: internal\n")
    return body


@pytest.fixture()
def code_consts_repo(tmp_path: Path) -> Path:
    root = tmp_path / "code_consts_repo"
    files = {
        "src/pkg/mod.py": (
            "def extract(x):\n"
            "    return x\n\n"
            "def other():\n"
            "    pass\n\n"
            "class C:\n"
            "    def run(self):\n"
            "        return 1\n"
        ),
        "tests/test_x.py": (
            "import pkg.mod as mod\n"
            "def test_pin():\n"
            "    mod.extract.__code__.co_consts\n"
        ),
        "tests/test_covered.py": "def test_ok(): pass\n",
    }
    return _init_repo(root, files)


def test_code_consts_pinning_returns_uncovered_test(code_consts_repo):
    allow = ["src/pkg/mod.py"]
    body = _code_consts_body(allow)
    assert check_code_consts_pinning(body, allow, code_consts_repo) == ["tests/test_x.py"]


def test_code_consts_pinning_without_extract_in_body(code_consts_repo):
    allow = ["src/pkg/mod.py"]
    body = _code_consts_body(allow)
    assert "extract" not in body
    assert check_code_consts_pinning(body, allow, code_consts_repo) == ["tests/test_x.py"]


def test_code_consts_pinning_covered_when_test_in_allow_paths(code_consts_repo):
    allow = ["src/pkg/mod.py", "tests/test_x.py"]
    body = _code_consts_body(allow)
    assert check_code_consts_pinning(body, allow, code_consts_repo) == []



def test_code_consts_pinning_class_method_getsource(tmp_path):
    root = tmp_path / "cls_repo"
    files = {
        "src/pkg/mod.py": "class C:\n    def run(self):\n        return 1\n",
        "tests/test_cls.py": (
            "import inspect\n"
            "from pkg.mod import C\n"
            "def test_pin():\n"
            "    inspect.getsource(C.run)\n"
        ),
    }
    _init_repo(root, files)
    allow = ["src/pkg/mod.py"]
    body = _code_consts_body(allow)
    assert check_code_consts_pinning(body, allow, root) == ["tests/test_cls.py"]


def test_code_consts_pinning_scope_mode_internal(code_consts_repo, tmp_path, monkeypatch):
    allow = ["src/pkg/mod.py"]
    body = _code_consts_body(allow, scope_mode_internal=True)
    rule = ScopeCouplingRules()
    with mock.patch.object(scope_coupling, "resolve_scope_root", return_value=code_consts_repo):
        violations = rule.check(body, [])
    cc = [v for v in violations if v.rule_id == CODE_CONSTS_PIN_RULE_ID]
    assert len(cc) == 1
    assert cc[0].severity == "fail"
    assert "tests/test_x.py" in (cc[0].fix_hint or "")


def test_code_consts_pinning_undefined_def_not_a_hit(tmp_path):
    root = tmp_path / "missing_def"
    files = {
        "src/pkg/mod.py": "def extract(x):\n    return x\n",
        "tests/test_missing.py": "missing.__code__.co_consts\n",
    }
    _init_repo(root, files)
    allow = ["src/pkg/mod.py"]
    body = _code_consts_body(allow)
    assert check_code_consts_pinning(body, allow, root) == []





def test_code_consts_autofix_blocked_over_max_files(code_consts_repo, tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"scope_gate": {"max_files": 1}})
    allow = ["src/pkg/mod.py"]
    body = _code_consts_body(allow)
    rule = ScopeCouplingRules()
    with mock.patch.object(scope_coupling, "resolve_scope_root", return_value=code_consts_repo):
        violations = rule.check(body, [])
    assert rule.autofix_new_allow_paths is None
    cc = [v for v in violations if v.rule_id == CODE_CONSTS_PIN_RULE_ID]
    assert len(cc) == 1
    assert cc[0].severity == "fail"
    assert cc[0].auto_fixable is False




# ---------------------------------------------------------------------------
# Root config files in deletion reference search (#5886)
# ---------------------------------------------------------------------------

_ROOT_PYPROJECT_REF_FILES = {
    "pyproject.toml": (
        "[tool.ruff.lint.per-file-ignores]\n"
        '"tools/skill/report/analyzer.py" = ["TID251"]\n'
    ),
    "tools/skill/report/analyzer.py": "x = 1\n",
}

_DELETE_ANALYZER_ROW = (
    "| sumipan/issuesmith | tools/skill/report/analyzer.py | delete | retire |"
)



def test_deletion_reference_in_root_pyproject_covered_by_allow_paths(tmp_path):
    """Ref: #5886 AC2."""
    root = _init_repo(tmp_path / "pyproj_cov", _ROOT_PYPROJECT_REF_FILES)
    allow = ["tools/skill/report/analyzer.py", "pyproject.toml"]
    body = _body(allow, rows=_DELETE_ANALYZER_ROW)
    assert check_deletion_references(body, allow, root) == []




def test_deletion_search_pathspecs_without_repo_path_unchanged():
    """Ref: #5886 AC5."""
    deleted = "tools/skill/report/analyzer.py"
    assert _deletion_search_pathspecs(deleted) == (
        "src",
        "tests",
        "scripts",
        "tools",
        "tools/skill/report",
    )
