"""test_design_lint.py — DesignLintRules unit tests"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.design_lint_rules import DesignLintRules, _pytest_module_name
from tests import legacy_text


def test_youkakunin_returns_violation():
    rule_ids = {item[1] for item in DesignLintRules.FAIL_PATTERNS}
    assert "design_lint.forbidden_word.youkakunin" in rule_ids


def test_mitei_returns_violation():
    rule_ids = {item[1] for item in DesignLintRules.FAIL_PATTERNS}
    assert "design_lint.forbidden_word.mitei" in rule_ids


def test_kentouchuu_returns_violation():
    rule_ids = {item[1] for item in DesignLintRules.FAIL_PATTERNS}
    assert "design_lint.forbidden_word.kentouchuu" in rule_ids


def test_user_confirm_returns_violation():
    rule_ids = {item[1] for item in DesignLintRules.FAIL_PATTERNS}
    assert "design_lint.forbidden_word.user_confirm" in rule_ids


_VALID_YAML_HEAD = (
    '```yaml\n'
    'target_repo: sumipan/nexus\n'
    'base_branch: main\n'
    'allow_paths:\n'
    '  - "**"\n'
    '```\n\n'
)


def test_clean_body_returns_empty():
    # ASCII fixture data.
    violations = DesignLintRules().check(_VALID_YAML_HEAD + "## c6982_c8981\nc3053_c308C_c306F_c666E_c901A_c306E_Design_c66F8_c3067_c3059_c3002\n", [])
    assert violations == []


def test_code_block_excluded():
    # ASCII fixture data.
    body = "c901A_c5E38_c30C6_c30AD_c30B9_c30C8\n\n```python\n# TODO: remove\nFAIL_PATTERNS = []\n```\n"
    violations = DesignLintRules().check(body, [])
    assert not any(v.rule_id == "design_lint.forbidden_word.todo" for v in violations)


def test_inline_code_excluded():
    # ASCII fixture data.
    body = _VALID_YAML_HEAD + "Acceptance Criteria: `TODO:` c3092_c542B_c3080 body c306F FAIL"
    violations = DesignLintRules().check(body, [])
    assert violations == []


def test_cp1_must_fail_false_passes():
    # ASCII fixture data.
    body = "```yaml\ncp1_must_fail: false\n```\n\n## c6982_c8981\nc5185_c5BB9\n"
    violations = DesignLintRules().check(body, [])
    assert not any(v.rule_id == "design_lint.intentional_hold" for v in violations)


def test_miteigi_not_flagged():
    """The Japanese word for 'undefined' must not be false-positive-flagged as 'undecided'."""
    # ASCII fixture data.
    violations = DesignLintRules().check("c672A_c5B9A_c7FA9_c5909_c6570_c3092_c53C2_c7167_c3057_c3066_c3044_c307E_c3059", [])
    assert not any(v.rule_id == "design_lint.forbidden_word.mitei" for v in violations)


def test_labels_param_ignored():
    """labels parameter is accepted but does not affect behavior."""
    violations = DesignLintRules().check("TBD", ["some-label", "other-label"])
    assert any(v.rule_id == "design_lint.forbidden_word.tbd" for v in violations)


def test_gate_registry_registered():
    import issuesmith.gate_rules.design_lint_rules  # noqa: F401 — ensure module loaded
    assert GATE_REGISTRY.get("design_lint") is DesignLintRules


def test_fix_hint_present_for_forbidden_words():
    violations = DesignLintRules().check("TBD", [])
    v = next(v for v in violations if v.rule_id == "design_lint.forbidden_word.tbd")
    assert v.fix_hint is not None


# --- Issue #1774: yaml_contract violations are auto_fixable=True ---


def test_broken_yaml_block_is_missing_block():
    # ASCII fixture data.
    body = "```yaml\n: : broken [\n```\n\n## c6982_c8981\nc672C_c6587\n"
    violations = DesignLintRules().check(body, [])
    assert any(v.rule_id == "design_lint.yaml_contract.missing_block" for v in violations)


# --- Issue #5105: pytest test basename collision (design_lint.test_basename_collision) ---

_CHANGE_TABLE_HEADER = (
    f"| {legacy_text.REPOSITORY} | {legacy_text.FILE_PATH} | "
    f"{legacy_text.CHANGE_TYPE} | {legacy_text.DESCRIPTION} |\n"
    "|---|---|---|---|\n"
)


def _collision_body(
    rows: list[tuple[str, str]],
    yaml_head: str = "",
    ac_yaml: str = "",
) -> str:
    table = "".join(
        f"| `sumipan/issuesmith` | `{path}` | {kind} | x |\n" for path, kind in rows
    )
    ac_block = ""
    if ac_yaml:
        ac_block = f"\n## Acceptance Criteria\n\n```yaml\n{ac_yaml}\n```\n"
    head = yaml_head or (
        "target_repo: sumipan/issuesmith\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "tests/**"\n'
    )
    return f"```yaml\n{head}```\n\n## Changed Files\n\n{_CHANGE_TABLE_HEADER}{table}{ac_block}"


def _git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@test.com"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        check=True,
        capture_output=True,
    )
    return repo


def _track(repo: Path, rel_path: str, content: str = "#\n") -> None:
    path = repo / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", rel_path], check=True, capture_output=True)


def _commit(repo: Path, message: str = "init") -> None:
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", message],
        check=True,
        capture_output=True,
    )


def _collision_violations(body: str, repo: Path, monkeypatch) -> list:
    monkeypatch.setattr(
        "issuesmith.gate_rules.design_lint_rules.resolve_scope_root",
        lambda metadata, cfg: repo,
    )
    all_v = DesignLintRules().check(body, [])
    return [v for v in all_v if v.rule_id == "design_lint.test_basename_collision"]


def test_pytest_module_name_unit():
    assert _pytest_module_name("tests/a/b/test_x.py", {"tests/a/b"}) == "b.test_x"
    assert _pytest_module_name("tests/a/test_x.py", {"tests", "tests/a"}) == "tests.a.test_x"
    assert _pytest_module_name("tests/a/test_x.py", {"tests"}) == "test_x"


def test_new_collides_with_tracked(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    _track(repo, "tests/b/test_x.py")
    _commit(repo)
    body = _collision_body([("tests/a/test_x.py", "new")])
    violations = _collision_violations(body, repo, monkeypatch)
    assert len(violations) == 1
    assert violations[0].location == "tests/a/test_x.py"
    assert "tests/b/test_x.py" in violations[0].message


def test_nested_vs_flat_no_collision(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    _track(repo, "tests/a/b/__init__.py")
    _commit(repo)
    body = _collision_body([
        ("tests/a/b/test_x.py", "new"),
        ("tests/c/test_x.py", "new"),
    ])
    violations = _collision_violations(body, repo, monkeypatch)
    assert violations == []


def test_importlib_addopts_skips_check(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    _track(
        repo,
        "pyproject.toml",
        '[tool.pytest.ini_options]\naddopts = "-q --import-mode=importlib"\n',
    )
    _commit(repo)
    body = _collision_body([
        ("tests/a/test_x.py", "new"),
        ("tests/b/test_x.py", "new"),
    ])
    assert _collision_violations(body, repo, monkeypatch) == []


def test_importlib_addopts_list_form(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    _track(
        repo,
        "pyproject.toml",
        '[tool.pytest.ini_options]\naddopts = ["--import-mode=importlib"]\n',
    )
    _commit(repo)
    body = _collision_body([
        ("tests/a/test_x.py", "new"),
        ("tests/b/test_x.py", "new"),
    ])
    assert _collision_violations(body, repo, monkeypatch) == []


def test_importlib_addopts_split_tokens(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    _track(
        repo,
        "pyproject.toml",
        '[tool.pytest.ini_options]\naddopts = "--import-mode importlib"\n',
    )
    _commit(repo)
    body = _collision_body([
        ("tests/a/test_x.py", "new"),
        ("tests/b/test_x.py", "new"),
    ])
    assert _collision_violations(body, repo, monkeypatch) == []


def test_append_import_mode_still_collides(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    _track(
        repo,
        "pyproject.toml",
        '[tool.pytest.ini_options]\naddopts = "--import-mode=append"\n',
    )
    _commit(repo)
    body = _collision_body([
        ("tests/a/test_x.py", "new"),
        ("tests/b/test_x.py", "new"),
    ])
    assert len(_collision_violations(body, repo, monkeypatch)) == 2


def test_invalid_toml_still_collides(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    _track(repo, "pyproject.toml", "not valid [[[\n")
    _commit(repo)
    body = _collision_body([
        ("tests/a/test_x.py", "new"),
        ("tests/b/test_x.py", "new"),
    ])
    assert len(_collision_violations(body, repo, monkeypatch)) == 2


def test_resolve_scope_root_none_skips(monkeypatch):
    body = _collision_body([
        ("tests/a/test_x.py", "new"),
        ("tests/b/test_x.py", "new"),
    ])
    monkeypatch.setattr(
        "issuesmith.gate_rules.design_lint_rules.resolve_scope_root",
        lambda metadata, cfg: None,
    )
    all_v = DesignLintRules().check(body, [])
    assert [v for v in all_v if v.rule_id == "design_lint.test_basename_collision"] == []


@pytest.fixture
def _config_owner_a(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "owner-a/repo-a", "scope_gate": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    yield
    reset_config_cache()


def test_missing_target_repo_defaults_to_config_repo(
    tmp_path, monkeypatch, _config_owner_a,
):
    """Refs #7156."""
    repo = _git_repo(tmp_path)
    _track(repo, "tests/b/test_x.py")
    _commit(repo)
    monkeypatch.setattr(
        "issuesmith.gate_rules.design_lint_rules.resolve_scope_root",
        lambda metadata, cfg: repo,
    )
    head = (
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "tests/**"\n'
    )
    table = (
        "| `owner-a/repo-a` | `tests/a/test_x.py` | new | x |\n"
    )
    body = (
        f"```yaml\n{head}```\n\n## Changed Files\n\n"
        f"{_CHANGE_TABLE_HEADER}{table}"
    )
    all_v = DesignLintRules().check(body, [])
    collisions = [
        v for v in all_v if v.rule_id == "design_lint.test_basename_collision"
    ]
    assert len(collisions) == 1
    missing = [
        v for v in all_v if v.rule_id == "design_lint.yaml_contract.missing_required"
    ]
    assert len(missing) == 1
    assert "<owner>/<repo>" in (missing[0].fix_hint or "")


