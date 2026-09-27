"""scope_coupling.deletion_reference_uncovered (nexus #3953).

Deleted files listed in the change table are searched (file name / stem / module name)
under tests/ scripts/ tools/ of the base checkout; referrers outside allow_paths and
paths_must_not_exist are reported. Uses a real git repo in tmp_path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from issuesmith.config import reset_config_cache
from issuesmith.gate_rules import scope_coupling
from issuesmith.gate_rules.scope_coupling import (
    BEHAVIOR_PIN_RULE_ID,
    DELETION_RULE_ID,
    PATH_STRING_RULE_ID,
    ScopeCouplingRules,
    check_allow_paths_string_references,
    check_behavior_pinning,
    check_deletion_references,
    deletion_search_keys,
)


@pytest.fixture(autouse=True)
def _clear_config_cache(monkeypatch):
    monkeypatch.delenv("ISSUESMITH_CONFIG", raising=False)
    reset_config_cache()
    yield
    reset_config_cache()


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    files = {
        "scripts/git-sync.py": "RUNTIME_STATE_RESET_PATHS = []\n",
        "tests/scripts/test_vcs_untrack_migration.py": (
            'SCRIPT = ROOT / "scripts" / "git-sync.py"\n'
        ),
        "tests/scripts/test_git_sync_retired.py": "import git_sync\n",
        "tools/runner.sh": "python scripts/git-sync --dry-run\n",
        "src/app.py": "# git-sync.py is not searched under src/\n",
        "docs/notes.md": "git-sync.py\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return root


def _body(allow_paths: list[str], *, rows: str, must_not_exist: list[str] | None = None) -> str:
    allow = "\n".join(f'  - "{p}"' for p in allow_paths)
    mne = must_not_exist or []
    mne_yaml = "\n".join(f'  - "{p}"' for p in mne) if mne else ""
    contract = (
        "paths_must_not_exist:\n" + mne_yaml if mne else "paths_must_not_exist: []"
    )
    return (
        "```yaml\n"
        "target_repo: sumipan/issuesmith\n"
        "base_branch: main\n"
        "allow_paths:\n"
        f"{allow}\n"
        "```\n\n"
        "## Changed Files\n\n"
        "| Repo | File | Change | Note |\n"
        "|---|---|---|---|\n"
        f"{rows}\n\n"
        "## Acceptance Criteria\n\n"
        "```yaml\n"
        "paths_must_exist: []\n"
        f"{contract}\n"
        "references_must_resolve: []\n"
        "```\n"
    )


_DELETE_ROW = "| sumipan/issuesmith | scripts/git-sync.py | delete | retire git_sync |"
_MODIFY_ROW = "| sumipan/issuesmith | src/app.py | update | something |"


def test_search_keys_are_file_name_stem_and_module_name():
    assert deletion_search_keys("scripts/git-sync.py") == ["git-sync.py", "git-sync", "git_sync"]


def test_uncovered_referrers_yield_violation(repo):
    body = _body(["scripts/git-sync.py"], rows=_DELETE_ROW)
    violations = check_deletion_references(body, ["scripts/git-sync.py"], repo)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == DELETION_RULE_ID == "scope_coupling.deletion_reference_uncovered"
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert v.location == "scripts/git-sync.py"
    for ref in (
        "tests/scripts/test_vcs_untrack_migration.py",
        "tests/scripts/test_git_sync_retired.py",
        "tools/runner.sh",
    ):
        assert ref in v.message
        assert ref in (v.fix_hint or "")
    # src/ and docs/ are outside the search dirs; the deleted file itself is never a referrer.
    assert "src/app.py" not in v.message
    assert "docs/notes.md" not in v.message


def test_git_grep_runs_all_three_patterns(repo):
    body = _body(["scripts/git-sync.py"], rows=_DELETE_ROW)
    calls: list[str] = []
    real = scope_coupling._git_grep

    def spy(root, pattern, pathspec):
        calls.append(pattern)
        return real(root, pattern, pathspec)

    with mock.patch.object(scope_coupling, "_git_grep", side_effect=spy):
        check_deletion_references(body, ["scripts/git-sync.py"], repo)
    assert {"git-sync.py", "git-sync", "git_sync"} <= set(calls)


def test_referrers_covered_by_allow_paths_glob_pass(repo):
    allow = ["scripts/git-sync.py", "tests/scripts/**", "tools/*.sh"]
    body = _body(allow, rows=_DELETE_ROW)
    assert check_deletion_references(body, allow, repo) == []


def test_referrers_in_paths_must_not_exist_pass(repo):
    allow = ["scripts/git-sync.py", "tools/runner.sh"]
    body = _body(
        allow,
        rows=_DELETE_ROW,
        must_not_exist=[
            "tests/scripts/test_vcs_untrack_migration.py",
            "tests/scripts/test_git_sync_retired.py",
        ],
    )
    assert check_deletion_references(body, allow, repo) == []


def test_no_delete_row_returns_empty(repo):
    body = _body(["src/app.py"], rows=_MODIFY_ROW)
    assert check_deletion_references(body, ["src/app.py"], repo) == []


def test_delete_row_for_other_repo_is_ignored(repo):
    body = _body(["src/app.py"], rows=_MODIFY_ROW)
    rows = [
        ("sumipan/nexus", "scripts/git-sync.py", "delete"),
        ("sumipan/issuesmith", "src/app.py", "update"),
    ]
    with mock.patch.object(scope_coupling, "extract_change_table_rows", return_value=rows):
        assert check_deletion_references(body, ["src/app.py"], repo) == []
    rows[0] = ("sumipan/issuesmith", "scripts/git-sync.py", "delete")
    with mock.patch.object(scope_coupling, "extract_change_table_rows", return_value=rows):
        [v] = check_deletion_references(body, ["src/app.py"], repo)
    assert v.location == "scripts/git-sync.py"


def test_rules_check_reports_deletion_violation(repo):
    body = _body(["scripts/git-sync.py"], rows=_DELETE_ROW)
    with mock.patch.object(scope_coupling, "resolve_scope_root", return_value=repo):
        violations = ScopeCouplingRules().check(body, [])
    assert DELETION_RULE_ID in {v.rule_id for v in violations}


def test_rules_check_skips_referrers_added_by_autofix(repo):
    """Test referrers auto-widened by the coupling autofix are not double-reported."""
    allow = ["scripts/git-sync.py", "tools/runner.sh", "tests/scripts/test_git_sync_retired.py"]
    body = _body(allow, rows=_DELETE_ROW)
    rule = ScopeCouplingRules()
    with mock.patch.object(scope_coupling, "resolve_scope_root", return_value=repo):
        violations = rule.check(body, [])
    assert rule.autofix_new_allow_paths is not None
    assert "tests/scripts/test_vcs_untrack_migration.py" in rule.autofix_new_allow_paths
    assert DELETION_RULE_ID not in {v.rule_id for v in violations}


def test_rules_check_scope_mode_internal_still_checks_deletions(repo):
    body = _body(["scripts/git-sync.py"], rows=_DELETE_ROW).replace(
        "base_branch: main\n", "base_branch: main\nscope_mode: internal\n"
    )
    with mock.patch.object(scope_coupling, "resolve_scope_root", return_value=repo):
        violations = ScopeCouplingRules().check(body, [])
    assert [v.rule_id for v in violations] == [DELETION_RULE_ID]


def _write_config(tmp_path: Path, monkeypatch, data: dict) -> None:
    import yaml

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "sumipan/issuesmith", **data}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_disabled_coupling_still_checks_deletions(repo, tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"scope_coupling": {"enabled": False}})
    body = _body(["scripts/git-sync.py"], rows=_DELETE_ROW)
    rule = ScopeCouplingRules()
    with mock.patch.object(scope_coupling, "resolve_scope_root", return_value=repo):
        violations = rule.check(body, [])
    assert [v.rule_id for v in violations] == [DELETION_RULE_ID]
    assert rule.autofix_new_allow_paths is None


def test_disabled_coupling_without_clone_passes(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"scope_coupling": {"enabled": False}})
    body = _body(["scripts/git-sync.py"], rows=_DELETE_ROW)
    with mock.patch.object(scope_coupling, "resolve_scope_root", return_value=None):
        assert ScopeCouplingRules().check(body, []) == []


def test_delete_words_vocabulary_from_scope_size(repo, tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"scope_size": {"delete_words": ["remove"]}})
    row = "| sumipan/issuesmith | scripts/git-sync.py | remove | retire |"
    body = _body(["scripts/git-sync.py"], rows=row)
    [v] = check_deletion_references(body, ["scripts/git-sync.py"], repo)
    assert v.location == "scripts/git-sync.py"
    # "delete" is no longer a delete word under this config.
    assert check_deletion_references(
        _body(["scripts/git-sync.py"], rows=_DELETE_ROW), ["scripts/git-sync.py"], repo
    ) == []


# --- Common file names are not deletion search keys (nexus #4076) ---


@pytest.mark.parametrize(
    "path",
    [
        "skills/project-summary/README.md",
        "skills/project-summary/SKILL.md",
        "src/pkg/__init__.py",
        "CHANGELOG.md",
        "pyproject.toml",
    ],
)
def test_common_file_names_yield_no_valid_keys(path):
    assert all(not scope_coupling._is_valid_key(k) for k in deletion_search_keys(path))
    assert deletion_search_keys(path) == []


def test_readme_keys_are_invalid():
    assert deletion_search_keys("README.md") == []
    for key in ("README.md", "README", "readme", "SKILL.md", "skill"):
        assert not scope_coupling._is_valid_key(key)


def test_short_stems_are_dropped_from_deletion_keys():
    # "fetch" is a common word and "cli" is too short; only the full name remains.
    assert deletion_search_keys("scripts/fetch.py") == ["fetch.py"]
    assert deletion_search_keys("scripts/cli.sh") == ["cli.sh"]


def test_common_file_name_deletion_has_no_referrers(repo):
    (repo / "scripts" / "budget-brake.py").write_text("# see README.md and SKILL.md\n")
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "refs")
    body = _body(
        ["src/app.py"],
        rows="| sumipan/issuesmith | skills/project-summary/SKILL.md | delete | retire |",
    )
    assert check_deletion_references(body, ["src/app.py"], repo) == []


# ---------------------------------------------------------------------------
# check_behavior_pinning and check_allow_paths_string_references (nexus #4134)
# ---------------------------------------------------------------------------


@pytest.fixture()
def pin_repo(tmp_path: Path) -> Path:
    """Git repo with test files referencing specific symbols and paths."""
    root = tmp_path / "pin_repo"
    files = {
        "src/memory.py": "def read_memory_iterative(): pass\n",
        "tests/test_memory.py": (
            "import memory\n"
            "def test_read_memory_iterative():\n"
            "    memory.read_memory_iterative()\n"
        ),
        "tests/test_mode.py": (
            'def test_mode():\n'
            '    assert config["mode"] == "iterative"\n'
        ),
        "tests/test_unrelated.py": "def test_pass(): pass\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return root


def test_behavior_pinning_deletion_symbol_outside_allow_paths(pin_repo):
    """Issue body deletes `read_memory_iterative`; uncovered test triggers violation."""
    body_text = "## 変更内容\n\n`read_memory_iterative` を削除する。\n"
    allow = ["src/memory.py"]
    violations = check_behavior_pinning(body_text, allow, pin_repo)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == BEHAVIOR_PIN_RULE_ID
    assert v.severity == "fail"
    assert "tests/test_memory.py" in v.message
    assert "tests/test_memory.py" in (v.fix_hint or "")


def test_behavior_pinning_key_value_outside_allow_paths(pin_repo):
    """`mode: iterative` removal; test with `iterative` outside allow_paths triggers violation."""
    body_text = "## 変更内容\n\n`mode: iterative` を外す。\n"
    allow = ["src/memory.py"]
    violations = check_behavior_pinning(body_text, allow, pin_repo)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == BEHAVIOR_PIN_RULE_ID
    assert "tests/test_mode.py" in v.message


def test_behavior_pinning_covered_test_no_violation(pin_repo):
    """Test already in allow_paths; no violation."""
    body_text = "## 変更内容\n\n`read_memory_iterative` を削除する。\n"
    allow = ["src/memory.py", "tests/test_memory.py"]
    assert check_behavior_pinning(body_text, allow, pin_repo) == []


def test_behavior_pinning_no_replacement_keywords_returns_empty(pin_repo):
    """No deletion/replacement keywords in body; no violation."""
    body_text = "## 変更内容\n\nThis is a new feature. Add `read_memory_iterative` here.\n"
    assert check_behavior_pinning(body_text, [], pin_repo) == []


def test_behavior_pinning_no_symbols_in_body_returns_empty(pin_repo):
    """Deletion keyword present but no backtick-quoted symbols; no violation."""
    body_text = "## 変更内容\n\n古い機能を削除する。\n"
    assert check_behavior_pinning(body_text, [], pin_repo) == []


def test_allow_paths_string_reference_outside_allow_paths(tmp_path):
    """Test outside allow_paths references an allow_paths path string → violation."""
    root = tmp_path / "path_repo"
    skill_path = "skills/project-todo/SKILL.md"
    files = {
        "skills/project-todo/SKILL.md": "# SKILL\n",
        "tests/test_skill_path.py": f'PATH = "{skill_path}"\n',
        "tests/test_unrelated.py": "def test_pass(): pass\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    allow = [skill_path, "src/other.py"]
    violations = check_allow_paths_string_references(allow, root)
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == PATH_STRING_RULE_ID
    assert v.severity == "fail"
    assert v.location == skill_path
    assert "tests/test_skill_path.py" in v.message
    assert "tests/test_skill_path.py" in (v.fix_hint or "")


def test_allow_paths_string_reference_covered_no_violation(tmp_path):
    """Test already in allow_paths; no violation for path string reference."""
    root = tmp_path / "path_repo2"
    skill_path = "skills/project-todo/SKILL.md"
    files = {
        "skills/project-todo/SKILL.md": "# SKILL\n",
        "tests/test_skill_path.py": f'PATH = "{skill_path}"\n',
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    allow = [skill_path, "tests/test_skill_path.py"]
    assert check_allow_paths_string_references(allow, root) == []


def test_no_stem_search_from_allow_paths_path(tmp_path):
    """`foo.py` in allow_paths; test has only `foo` (not the full path) → no violation."""
    root = tmp_path / "stem_repo"
    files = {
        "src/foo.py": "x = 1\n",
        "tests/test_foo.py": 'x = "foo"\n',
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")

    allow = ["src/foo.py"]
    # Searches for exact string "src/foo.py" in tests; "foo" alone does not match.
    assert check_allow_paths_string_references(allow, root) == []
