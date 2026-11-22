"""Configurable publish version policies (#7789)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from issuesmith.bump_commits import BUMP_SUBJECT_PREFIX
from issuesmith.config import reset_config_cache
from issuesmith.ops.publish import _maybe_bump_version, _run_version_bump
from issuesmith.version_policy import resolve_policy

_CHANGELOG = "# Changelog\n\n## [Unreleased]\n\n"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _init_repo(tmp_path: Path, *, extra: dict[str, str] | None = None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "commit.gpgsign", "false")
    files = {
        "pyproject.toml": '[project]\nname = "x"\nversion = "0.1.0"\n',
        "CHANGELOG.md": _CHANGELOG,
        "src/mod.py": "x = 1\n",
    }
    if extra:
        files.update(extra)
    for rel, content in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", "init")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    return repo


def _load_version_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, version: dict) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/host", "version": version}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_auto_resolves_by_pyproject_presence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7789 AC4 — auto picks pyproject or none from worktree root."""
    _load_version_config(tmp_path, monkeypatch, {"policy": "auto"})
    with_py = tmp_path / "with"
    with_py.mkdir()
    (with_py / "pyproject.toml").write_text("x\n", encoding="utf-8")
    without = tmp_path / "without"
    without.mkdir()
    assert resolve_policy(with_py) == "pyproject"
    assert resolve_policy(without) == "none"


def test_none_policy_adds_no_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refs #7789 AC5 — none policy never bumps cross-repo."""
    repo = _init_repo(tmp_path)
    _load_version_config(tmp_path, monkeypatch, {"policy": "none"})
    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert (
        _maybe_bump_version(repo, "main", "o/target", "o/host") is None
    )
    assert _git(repo, "rev-parse", "HEAD").stdout.strip() == head_before


def test_pyproject_bump_without_host_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7789 AC9 — pyproject bump uses the package entrypoint, not host scripts."""
    repo = _init_repo(tmp_path)
    _load_version_config(tmp_path, monkeypatch, {"policy": "pyproject"})
    (repo / "src" / "mod.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "add", "src/mod.py")
    _git(repo, "commit", "-q", "-m", "fix: tweak")
    result = _run_version_bump(repo, "origin/main")
    assert result.returncode == 0, result.stderr
    subjects = _git(
        repo, "log", "--format=%s", "origin/main..HEAD",
    ).stdout.splitlines()
    assert any(s.startswith(BUMP_SUBJECT_PREFIX) for s in subjects)


def test_pr_scope_accepts_command_bump_only_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7789 AC8 — command bump-only files outside allow_paths stay in scope."""
    from issuesmith.gates.pr_scope import PrScopeGate

    repo = _init_repo(tmp_path, extra={"VERSION": "0.1.0\n"})
    (repo / "pyproject.toml").unlink()
    _git(repo, "add", "-u")
    _git(repo, "commit", "-q", "-m", "drop pyproject")
    _load_version_config(
        tmp_path,
        monkeypatch,
        {
            "policy": "command",
            "bump_command": ["true"],
        },
    )
    (repo / "src" / "allowed.py").write_text("ok\n", encoding="utf-8")
    _git(repo, "add", "src/allowed.py")
    _git(repo, "commit", "-q", "-m", "feat: allowed")
    (repo / "VERSION").write_text("0.2.0\n", encoding="utf-8")
    _git(repo, "add", "VERSION")
    _git(repo, "commit", "-q", "-m", f"{BUMP_SUBJECT_PREFIX}0.2.0 (command)")
    gate = PrScopeGate(repo, ["src/**"], "main")
    assert gate.check("", []) == []

    (repo / "VERSION").write_text("0.3.0\n", encoding="utf-8")
    _git(repo, "commit", "-q", "-am", "feat: hand edit version")
    violations = gate.check("", [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pr_scope.out_of_allow"
    assert violations[0].location == "VERSION"
