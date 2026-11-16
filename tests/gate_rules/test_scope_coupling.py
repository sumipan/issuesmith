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
    DELETION_RULE_ID,
    DELETION_SEARCH_DIRS,
    ScopeCouplingRules,
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
        "tools/runner.sh": "python scripts/git-sync.py --dry-run\n",
        "src/app.py": "# unrelated runtime module\n",
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





def test_referrers_covered_by_allow_paths_glob_pass(repo):
    allow = ["scripts/git-sync.py", "tests/scripts/**", "tools/*.sh"]
    body = _body(allow, rows=_DELETE_ROW)
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





def _write_language_pack(tmp_path: Path, monkeypatch, **overrides) -> None:
    """Point the config at a language pack YAML: the EN pack with ``overrides``."""
    import dataclasses

    import yaml

    from issuesmith.language import EN

    data = {f.name: getattr(EN, f.name) for f in dataclasses.fields(EN)}
    data.update(overrides)
    plain = {
        k: list(v) if isinstance(v, tuple) else dict(v) if isinstance(v, dict) else v
        for k, v in data.items()
    }
    plain["sections"] = dict(EN.sections)
    plain["messages"] = dict(EN.messages)
    pack_path = tmp_path / "language_pack.yaml"
    pack_path.write_text(yaml.safe_dump(plain, allow_unicode=False), encoding="utf-8")
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/issuesmith", "language_pack": str(pack_path)}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


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



# --- Common file names are not deletion search keys (nexus #4076) ---



def test_readme_keys_are_invalid():
    assert deletion_search_keys("README.md") == []
    for key in ("README.md", "README", "readme", "SKILL.md", "skill"):
        assert not scope_coupling._is_valid_key(key)




def _init_repo(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return root


# ---------------------------------------------------------------------------
# src-layout deletion reference detection (#4321)
# ---------------------------------------------------------------------------


def test_deletion_search_dirs_includes_src():
    assert "src" in DELETION_SEARCH_DIRS


def test_deleted_parent_module_strips_src_prefix():
    assert scope_coupling._deleted_parent_module(
        "src/issuesmith/ops/repair_step.py"
    ) == "issuesmith.ops"


def test_deletion_search_keys_strips_src_prefix():
    keys = deletion_search_keys("src/issuesmith/ops/repair_step.py")
    assert "issuesmith.ops.repair_step" in keys
    assert "src.issuesmith.ops.repair_step" not in keys


@pytest.fixture()
def src_layout_repo(tmp_path: Path) -> Path:
    root = tmp_path / "src_layout_repo"
    files = {
        "src/pkg/steps/x.py": "def f(): pass\n",
        "src/pkg/verbs/y.py": "from pkg.steps.x import f\n",
        "tests/t.py": "from pkg.steps import x\n",
    }
    return _init_repo(root, files)






def _subpackage_repo(tmp_path: Path) -> Path:
    root = tmp_path / "subpkg"
    files = {
        "src/pkg/sub/__init__.py": "",
        "src/pkg/sub/a.py": "VALUE = 1\n",
        "src/pkg/sub/b.py": "class X:\n    pass\n",
        "tests/test_sibling_from.py": "from pkg.sub.b import X\n",
        "tests/test_sibling_parent.py": "from pkg.sub import Verdict\n",
        "tests/test_from_parent.py": "from pkg.sub import a\n",
        "tests/test_import_dotted.py": "import pkg.sub.a\n",
    }
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init")
    return root


def test_parent_module_key_ignores_sibling_importers(tmp_path):
    """#6233: deleting src/pkg/sub/a.py must not flag importers of sibling modules."""
    root = _subpackage_repo(tmp_path)
    row = "| sumipan/issuesmith | src/pkg/sub/a.py | delete | retire a |"
    body = _body(["src/pkg/sub/a.py"], rows=row)
    violations = check_deletion_references(body, ["src/pkg/sub/a.py"], root)
    assert len(violations) == 1
    message = violations[0].message
    assert "tests/test_from_parent.py" in message
    assert "tests/test_import_dotted.py" in message
    assert "tests/test_sibling_from.py" not in message
    assert "tests/test_sibling_parent.py" not in message
