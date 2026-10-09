"""test_cp1.py — Cp1Rules unit tests"""

from __future__ import annotations

import subprocess
from pathlib import Path

from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.cp1 import Cp1Rules, _pytest_module_name
from tests import legacy_text


def test_tbd_returns_violation():
    """AC: TBD yields a cp1.forbidden_word.tbd Violation."""
    # ASCII fixture data.
    violations = Cp1Rules().check("c672C_c6587_c306B TBD c304C_c6B8B_c3063_c3066_c3044_c308B", [])
    assert any(v.rule_id == "cp1.forbidden_word.tbd" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.tbd")
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert v.location is None


def test_todo_returns_violation():
    # ASCII fixture data.
    violations = Cp1Rules().check("TODO: c5F8C_c3067_c5BFE_c5FDC", [])
    assert any(v.rule_id == "cp1.forbidden_word.todo" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.todo")
    assert v.severity == "fail"
    assert v.auto_fixable is True


def test_youkakunin_returns_violation():
    rule_ids = {item[1] for item in Cp1Rules.FAIL_PATTERNS}
    assert "cp1.forbidden_word.youkakunin" in rule_ids


def test_mitei_returns_violation():
    rule_ids = {item[1] for item in Cp1Rules.FAIL_PATTERNS}
    assert "cp1.forbidden_word.mitei" in rule_ids


def test_kentouchuu_returns_violation():
    rule_ids = {item[1] for item in Cp1Rules.FAIL_PATTERNS}
    assert "cp1.forbidden_word.kentouchuu" in rule_ids


def test_user_confirm_returns_violation():
    rule_ids = {item[1] for item in Cp1Rules.FAIL_PATTERNS}
    assert "cp1.forbidden_word.user_confirm" in rule_ids


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
    violations = Cp1Rules().check(_VALID_YAML_HEAD + "## c6982_c8981\nc3053_c308C_c306F_c666E_c901A_c306E_Design_c66F8_c3067_c3059_c3002\n", [])
    assert violations == []


def test_code_block_excluded():
    # ASCII fixture data.
    body = "c901A_c5E38_c30C6_c30AD_c30B9_c30C8\n\n```python\n# TODO: remove\nFAIL_PATTERNS = []\n```\n"
    violations = Cp1Rules().check(body, [])
    assert not any(v.rule_id == "cp1.forbidden_word.todo" for v in violations)


def test_inline_code_excluded():
    # ASCII fixture data.
    body = _VALID_YAML_HEAD + "Acceptance Criteria: `TODO:` c3092_c542B_c3080 body c306F FAIL"
    violations = Cp1Rules().check(body, [])
    assert violations == []


def test_cp1_must_fail_true_returns_intentional_hold():
    # ASCII fixture data.
    body = "```yaml\ncp1_must_fail: true\n```\n\n## c6982_c8981\nc901A_c5E38_c306E_c5185_c5BB9\n"
    violations = Cp1Rules().check(body, [])
    assert any(v.rule_id == "cp1.intentional_hold" for v in violations)
    v = next(v for v in violations if v.rule_id == "cp1.intentional_hold")
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert v.fix_hint is None
    assert v.location is None


def test_cp1_must_fail_false_passes():
    # ASCII fixture data.
    body = "```yaml\ncp1_must_fail: false\n```\n\n## c6982_c8981\nc5185_c5BB9\n"
    violations = Cp1Rules().check(body, [])
    assert not any(v.rule_id == "cp1.intentional_hold" for v in violations)


def test_miteigi_not_flagged():
    """The Japanese word for 'undefined' must not be false-positive-flagged as 'undecided'."""
    # ASCII fixture data.
    violations = Cp1Rules().check("c672A_c5B9A_c7FA9_c5909_c6570_c3092_c53C2_c7167_c3057_c3066_c3044_c307E_c3059", [])
    assert not any(v.rule_id == "cp1.forbidden_word.mitei" for v in violations)


def test_labels_param_ignored():
    """labels parameter is accepted but does not affect behavior."""
    violations = Cp1Rules().check("TBD", ["some-label", "other-label"])
    assert any(v.rule_id == "cp1.forbidden_word.tbd" for v in violations)


def test_gate_registry_registered():
    import issuesmith.gate_rules.cp1  # noqa: F401 — ensure module loaded
    assert GATE_REGISTRY.get("cp1") is Cp1Rules


def test_fix_hint_present_for_forbidden_words():
    violations = Cp1Rules().check("TBD", [])
    v = next(v for v in violations if v.rule_id == "cp1.forbidden_word.tbd")
    assert v.fix_hint is not None


# --- Issue #1774: yaml_contract violations are auto_fixable=True ---

def test_yaml_missing_target_repo_is_auto_fixable():
    """Missing target_repo in YAML block → auto_fixable=True so B1 can fix."""
    # ASCII fixture data.
    body = "```yaml\nbase_branch: main\nallow_paths:\n  - src/**\n```\n\n## c6982_c8981\nc5185_c5BB9\n"
    violations = Cp1Rules().check(body, [])
    v = next((v for v in violations if v.rule_id == "cp1.yaml_contract.missing_required"), None)
    assert v is not None, "cp1.yaml_contract.missing_required should be detected"
    assert v.auto_fixable is True
    assert v.fix_hint is not None
    assert "target_repo" in v.fix_hint


def test_yaml_annotation_in_path_is_auto_fixable():
    """Parenthetical annotation in allow_paths → auto_fixable=True."""
    # ASCII fixture data.
    body = "```yaml\ntarget_repo: sumipan/ghdag\nallow_paths:\n  - (ghdag c30EA_c30DD) src/**\n```\n"
    violations = Cp1Rules().check(body, [])
    v = next((v for v in violations if v.rule_id == "cp1.yaml_contract.annotation_in_path"), None)
    assert v is not None
    assert v.auto_fixable is True
    assert v.fix_hint is not None


def test_yaml_invalid_path_format_is_auto_fixable():
    """allow_paths containing /var/tmp/ → auto_fixable=True."""
    body = "```yaml\ntarget_repo: sumipan/ghdag\nallow_paths:\n  - /var/tmp/ghdag/\n```\n"
    violations = Cp1Rules().check(body, [])
    v = next((v for v in violations if v.rule_id == "cp1.yaml_contract.invalid_path_format"), None)
    assert v is not None
    assert v.auto_fixable is True
    assert v.fix_hint is not None


def test_yaml_unsupported_repo_is_not_auto_fixable():
    """Unsupported repository → auto_fixable=False (needs human judgment)."""
    # ASCII fixture data.
    body = "```yaml\ntarget_repo: sumipan/unknown-repo\n```\n\n## c6982_c8981\nc5185_c5BB9\n"
    violations = Cp1Rules().check(body, [])
    v = next((v for v in violations if v.rule_id == "cp1.yaml_contract.unsupported_repo"), None)
    assert v is not None
    assert v.auto_fixable is False


def test_missing_yaml_block_is_fail_with_fix_hint():
    """Missing leading yaml is missing_block (no skip — #2539/#2541 regression)."""
    # ASCII fixture data.
    violations = Cp1Rules().check("## c6982_c8981\nyaml None\n", [])
    by_id = {v.rule_id: v for v in violations}
    v = by_id["cp1.yaml_contract.missing_block"]
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert "allow_paths" in (v.fix_hint or "")


def test_scope_gate_over_hard_max_returns_violation():
    """AC-3: scope_gate.max_files above hard_max_files fails CP1 yaml contract."""
    # ASCII fixture data.
    body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        "  - tests/**\n"
        "scope_gate:\n"
        "  max_files: 250\n"
        "```\n\n## c6982_c8981\nc901A_c5E38_c306E_c5185_c5BB9\n"
    )
    violations = Cp1Rules().check(body, [])
    assert any(
        v.rule_id == "cp1.yaml_contract.scope_gate_over_hard_max" for v in violations
    )
    v = next(
        v for v in violations if v.rule_id == "cp1.yaml_contract.scope_gate_over_hard_max"
    )
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert "200" in (v.fix_hint or "") or "hard_max" in (v.fix_hint or "").lower()


def test_scope_gate_within_hard_max_is_ok():
    # ASCII fixture data.
    body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: main\n"
        "allow_paths:\n"
        "  - tests/**\n"
        "scope_gate:\n"
        "  max_files: 150\n"
        "```\n\n## c6982_c8981\nc901A_c5E38_c306E_c5185_c5BB9\n"
    )
    violations = Cp1Rules().check(body, [])
    assert not any(
        v.rule_id == "cp1.yaml_contract.scope_gate_over_hard_max" for v in violations
    )


def test_broken_yaml_block_is_missing_block():
    # ASCII fixture data.
    body = "```yaml\n: : broken [\n```\n\n## c6982_c8981\nc672C_c6587\n"
    violations = Cp1Rules().check(body, [])
    assert any(v.rule_id == "cp1.yaml_contract.missing_block" for v in violations)


# --- Issue #5105: pytest test basename collision (cp1.test_basename_collision) ---

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
        "issuesmith.gate_rules.cp1.resolve_scope_root",
        lambda metadata, cfg: repo,
    )
    all_v = Cp1Rules().check(body, [])
    return [v for v in all_v if v.rule_id == "cp1.test_basename_collision"]


def test_pytest_module_name_unit():
    assert _pytest_module_name("tests/a/b/test_x.py", {"tests/a/b"}) == "b.test_x"
    assert _pytest_module_name("tests/a/test_x.py", {"tests", "tests/a"}) == "tests.a.test_x"
    assert _pytest_module_name("tests/a/test_x.py", {"tests"}) == "test_x"


def test_two_new_same_basename_no_init(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    body = _collision_body([
        ("tests/a/test_x.py", "new"),
        ("tests/b/test_x.py", "new"),
    ])
    violations = _collision_violations(body, repo, monkeypatch)
    assert len(violations) == 2
    locations = {v.location for v in violations}
    assert locations == {"tests/a/test_x.py", "tests/b/test_x.py"}
    for v in violations:
        assert v.severity == "fail"
        assert v.auto_fixable is True
        assert "pytest module" in v.message


def test_new_collides_with_tracked(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    _track(repo, "tests/b/test_x.py")
    _commit(repo)
    body = _collision_body([("tests/a/test_x.py", "new")])
    violations = _collision_violations(body, repo, monkeypatch)
    assert len(violations) == 1
    assert violations[0].location == "tests/a/test_x.py"
    assert "tests/b/test_x.py" in violations[0].message


def test_paths_must_exist_and_new_table_row(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    _track(repo, "tests/b/test_x.py")
    _commit(repo)
    body = _collision_body(
        [("tests/a/test_x.py", "new")],
        ac_yaml="paths_must_exist:\n  - tests/b/test_x.py\n",
    )
    violations = _collision_violations(body, repo, monkeypatch)
    assert len(violations) == 2


def test_package_init_avoids_false_positive(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    _track(repo, "tests/a/__init__.py")
    _track(repo, "tests/b/__init__.py")
    _track(repo, "tests/a/test_x.py")
    _track(repo, "tests/b/test_x.py")
    _commit(repo)
    body = _collision_body([
        ("tests/a/test_x.py", "new"),
        ("tests/b/test_x.py", "new"),
    ])
    violations = _collision_violations(body, repo, monkeypatch)
    assert violations == []


def test_nested_init_same_module_name_collides(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    _track(repo, "tests/a/b/__init__.py")
    _track(repo, "tests/c/b/__init__.py")
    _commit(repo)
    body = _collision_body([
        ("tests/a/b/test_x.py", "new"),
        ("tests/c/b/test_x.py", "new"),
    ])
    violations = _collision_violations(body, repo, monkeypatch)
    assert len(violations) == 2


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


def test_no_pyproject_still_collides(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
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


def test_non_new_modify_and_outside_tests_no_violation(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    _track(repo, "tests/b/test_x.py")
    _commit(repo)
    body = _collision_body([
        ("tests/a/test_x.py", "modify"),
        ("src/test_x.py", "new"),
        ("tests/a/helper.py", "new"),
        ("tests/a/test_y.py", "new"),
    ])
    assert _collision_violations(body, repo, monkeypatch) == []


def test_multiple_tracked_opponents_sorted_in_message(tmp_path, monkeypatch):
    repo = _git_repo(tmp_path)
    _track(repo, "tests/c/test_x.py")
    _track(repo, "tests/b/test_x.py")
    _commit(repo)
    body = _collision_body([("tests/a/test_x.py", "new")])
    violations = _collision_violations(body, repo, monkeypatch)
    assert len(violations) == 1
    msg = violations[0].message
    assert msg.index("tests/b/test_x.py") < msg.index("tests/c/test_x.py")


def test_resolve_scope_root_none_skips(monkeypatch):
    body = _collision_body([
        ("tests/a/test_x.py", "new"),
        ("tests/b/test_x.py", "new"),
    ])
    monkeypatch.setattr(
        "issuesmith.gate_rules.cp1.resolve_scope_root",
        lambda metadata, cfg: None,
    )
    all_v = Cp1Rules().check(body, [])
    assert [v for v in all_v if v.rule_id == "cp1.test_basename_collision"] == []


def test_no_test_candidates_skips_resolve(monkeypatch):
    calls = 0

    def _boom(metadata, cfg):
        nonlocal calls
        calls += 1
        raise AssertionError("resolve_scope_root should not run")

    monkeypatch.setattr("issuesmith.gate_rules.cp1.resolve_scope_root", _boom)
    body = _collision_body([("src/foo.py", "new")])
    assert Cp1Rules().check(body, []) == []
    assert calls == 0


def test_no_git_dir_body_only_collision(tmp_path, monkeypatch):
    repo = tmp_path / "not_a_git_repo"
    repo.mkdir()
    body = _collision_body([
        ("tests/a/test_x.py", "new"),
        ("tests/b/test_x.py", "new"),
    ])
    violations = _collision_violations(body, repo, monkeypatch)
    assert len(violations) == 2
