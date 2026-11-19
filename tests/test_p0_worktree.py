"""Tests for issuesmith.worktree public helpers (#4274 / nexus #4111).

Covers local/cross-repo preparation, fetch retry, existing worktree reuse, and
branch mismatch rejection. Step orchestration (milestone, scope gate, notify)
is intentionally out of scope here.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from issuesmith import worktree as wt

_STALE_BASE_COMMENT_TEMPLATE = """\
## P0 stop: base_branch sync failed (rebase conflict)

`origin/{base_branch}` rebase conflict. Resolve manually and re-dispatch.

**Conflict files:**
```
{conflict_files}
```

PIPELINE_STATUS: STALE_BASE"""

# --- Real git strings (verbatim captures; values unchanged) ---

CLONE_FAIL_STDERR = (
    "Cloning into 'clone_fail'...\n"
    "remote: Repository not found.\n"
    "fatal: repository 'https://github.com/sumipan/does-not-exist-zzzz-3168.git/' not found\n"
)

CANNOT_LOCK_REF_STDERR = (
    "fatal: update_ref failed for ref 'refs/heads/main': cannot lock ref "
    "'refs/heads/main': Unable to create "
    "'/var/folders/bh/wdg0sj9x21xd0z1c_89fmjvr0000gn/T/tmp.97SSFTMDiW/r/"
    ".git/refs/heads/main.lock': File exists.\n"
    "\n"
    "Another git process seems to be running in this repository, e.g.\n"
    "an editor opened by 'git commit'. Please make sure all processes\n"
    "are terminated then try again. If it still fails, a git process\n"
    "may have crashed in this repository earlier:\n"
    "remove the file manually to continue.\n"
)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("base\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _clone(src: Path, dst: Path) -> Path:
    subprocess.run(
        ["git", "clone", "-q", str(src), str(dst)],
        capture_output=True,
        check=True,
    )
    _git(dst, "config", "user.email", "t@example.com")
    _git(dst, "config", "user.name", "t")
    return dst


def _git_init_with_main(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "t@t"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "t"],
        check=True,
        capture_output=True,
    )
    (repo / "README").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )


def test_prefers_origin_when_both_exist(tmp_path: Path) -> None:
    upstream = _init_repo(tmp_path / "upstream")
    clone = _clone(upstream, tmp_path / "clone")
    assert wt.resolve_base_ref(clone, "main") == "origin/main"


def test_falls_back_to_local_when_origin_missing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "local-only")
    assert wt.resolve_base_ref(repo, "main") == "refs/heads/main"


def test_raises_when_neither_exists(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "local-only")
    with pytest.raises(wt.WorktreeError):
        wt.resolve_base_ref(repo, "develop")


def test_local_only_ref_is_usable_as_start_point(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "local-only")
    worktree_dir = tmp_path / "wt"
    wt.prepare_worktree(repo, worktree_dir, "feat/y", wt.resolve_base_ref(repo, "main"))
    assert _git(worktree_dir, "rev-parse", "HEAD") == _git(repo, "rev-parse", "main")
    assert _git(worktree_dir, "branch", "--show-current") == "feat/y"


def test_validate_branch_rejects_invalid_name() -> None:
    with pytest.raises(wt.WorktreeError, match="invalid branch name"):
        wt.validate_branch("bad branch")


def test_prepare_worktree_rejects_branch_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
    worktree_dir = tmp_path / "worktrees" / "issue-3168"
    wt.prepare_worktree(repo, worktree_dir, "feat/one", "main")
    with pytest.raises(wt.WorktreeError, match="branch mismatch"):
        wt.prepare_worktree(repo, worktree_dir, "feat/other", "main")


def test_git_common_dir_resolves_linked_worktree(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
    worktree_dir = tmp_path / "linked-wt"
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            "-b",
            "linked-main",
            str(worktree_dir),
            "main",
        ],
        check=True,
        capture_output=True,
    )
    assert (worktree_dir / ".git").is_file()
    common = wt._git_common_dir(worktree_dir)
    assert common.is_dir()
    assert common == wt._git_common_dir(repo).resolve()


def test_prepare_local_worktree_returns_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "local")
    worktree_dir = tmp_path / "wt-local"
    result = wt.prepare_local_worktree(repo, worktree_dir, "feat/local", "main")
    assert result == worktree_dir
    assert worktree_dir.is_dir()
    assert _git(worktree_dir, "branch", "--show-current") == "feat/local"


def test_clone_if_missing_clones_into_empty_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone_path = tmp_path / "external" / "issuesmith"
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        if cmd[0:2] == ["git", "clone"]:
            clone_path.mkdir(parents=True)
            _git_init_with_main(clone_path)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(wt.subprocess, "run", fake_run)
    wt.clone_if_missing(clone_path, "sumipan/issuesmith", "main")
    clone_calls = [c for c in calls if c[0:2] == ["git", "clone"]]
    assert len(clone_calls) == 1
    assert "https://github.com/sumipan/issuesmith.git" in clone_calls[0]
    assert str(clone_path) in clone_calls[0]


def test_clone_if_missing_raises_on_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    clone_path = tmp_path / "missing" / "repo"

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr=CLONE_FAIL_STDERR)

    monkeypatch.setattr(wt.subprocess, "run", fake_run)
    with pytest.raises(wt.WorktreeError, match="failed to clone sumipan/does-not-exist"):
        wt.clone_if_missing(clone_path, "sumipan/does-not-exist-zzzz-3168", "main")


def test_prepare_cross_repo_worktree_reuses_existing_clone(tmp_path: Path) -> None:
    clone = _init_repo(tmp_path / "clone")
    subprocess.run(
        ["git", "-C", str(clone), "remote", "add", "origin", str(clone)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(clone), "update-ref", "refs/remotes/origin/main", "HEAD"],
        check=True,
        capture_output=True,
    )
    worktree_dir = tmp_path / "worktrees" / "issue-cross"
    result = wt.prepare_cross_repo_worktree(
        clone,
        worktree_dir,
        target_repo="sumipan/issuesmith",
        base_branch="main",
        branch="feat/issue-cross",
    )
    assert result == worktree_dir
    assert worktree_dir.is_dir()
    assert _git(worktree_dir, "branch", "--show-current") == "feat/issue-cross"


def test_fetch_lock_wait_uses_issuesmith_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ISSUESMITH_FETCH_LOCK_WAIT", "5")
    monkeypatch.delenv("P0_FETCH_LOCK_WAIT", raising=False)
    assert wt._lock_wait_seconds() == 5


def test_transient_fetch_markers_detected() -> None:
    assert wt._is_transient_fetch_error(CANNOT_LOCK_REF_STDERR) is True
    assert wt._is_transient_fetch_error("network down") is False


def test_require_yaml_metadata_rejects_missing_block() -> None:
    with pytest.raises(wt.WorktreeError, match="yaml metadata block"):
        wt.require_yaml_metadata("## Background\nno yaml\n")


def _make_repo_with_diverged_branch(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "-b", "feat/stale"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )
    (repo / "file2").write_text("y\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "file2"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "advance main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", str(repo)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "fetch", "origin", "main"],
        check=True,
        capture_output=True,
    )
    return repo, "feat/stale"


def test_ensure_base_included_rebase_success_returns_none(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo, stale_branch = _make_repo_with_diverged_branch(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "checkout", stale_branch],
        check=True,
        capture_output=True,
    )
    client = MagicMock()
    result = wt.ensure_base_included(
        repo,
        "main",
        client,
        3408,
        stale_base_comment_template=_STALE_BASE_COMMENT_TEMPLATE,
    )
    assert result is None
    err = capsys.readouterr().err
    assert "P0_REBASE: auto-synced" in err
    client.issue_comment.assert_not_called()


def test_ensure_base_included_resets_local_only_branch_on_conflict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refs sumipan/nexus#6687: unpushed branch resets to origin/base on rebase conflict."""
    origin = _init_repo(tmp_path / "origin")
    clone = _clone(origin, tmp_path / "clone")
    subprocess.run(
        ["git", "-C", str(clone), "checkout", "-b", "feat/local-only"],
        check=True,
        capture_output=True,
    )
    (clone / "README.md").write_text("stale side\n", encoding="utf-8")
    _git(clone, "add", "README.md")
    _git(clone, "commit", "-q", "-m", "local only work")
    discarded_sha = _git(clone, "rev-parse", "HEAD")

    (origin / "README.md").write_text("main side\n", encoding="utf-8")
    _git(origin, "add", "README.md")
    _git(origin, "commit", "-q", "-m", "advance main")

    _git(clone, "fetch", "origin", "main")
    origin_main_sha = _git(clone, "rev-parse", "origin/main")

    client = MagicMock()
    result = wt.ensure_base_included(
        clone,
        "main",
        client,
        6687,
        stale_base_comment_template=_STALE_BASE_COMMENT_TEMPLATE,
    )
    assert result is None
    assert _git(clone, "rev-parse", "HEAD") == origin_main_sha
    err = capsys.readouterr().err
    assert "P0_REBASE: discarded local-only branch" in err
    assert discarded_sha in err
    client.issue_comment.assert_not_called()


def test_ensure_base_included_rebase_conflict_returns_andon_decision(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from dataclasses import replace

    from issuesmith.config import PhaseConfig, get_config

    cfg = replace(
        get_config(),
        phases=[
            PhaseConfig(
                name="develop",
                role="implementation",
                entry_step="prepare",
                handler="impl",
                steps=("prepare", "implement"),
            )
        ],
    )
    monkeypatch.setattr("issuesmith.config.get_config", lambda: cfg)
    repo, stale_branch = _make_repo_with_diverged_branch(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "checkout", stale_branch],
        check=True,
        capture_output=True,
    )
    (repo / "README").write_text("conflict\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "stale conflict"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )
    (repo / "README").write_text("main conflict\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "main conflict"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "fetch", "origin", "main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", stale_branch],
        check=True,
        capture_output=True,
    )
    client = MagicMock()
    result = wt.ensure_base_included(
        repo,
        "main",
        client,
        3408,
        stale_base_comment_template=_STALE_BASE_COMMENT_TEMPLATE,
        caller_step="prepare",
    )
    assert result is not None
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "decision"
    assert result.andon.rule_id == "p0.base_rebase_conflict"
    assert result.andon.options == [
        "manual_rebase_then_resume_implement",
        "discard_branch_then_restart_prepare",
    ]
    assert "README" in result.andon.summary
    assert "git rebase origin/main" in result.andon.summary
    assert "issuesmith resume 3408" in result.andon.summary
    assert "--from implement" in result.andon.summary
    assert "p1" not in result.andon.summary
    client.issue_comment.assert_not_called()
    err = capsys.readouterr().err
    assert "STALE_BASE" in err


def test_ensure_base_included_reads_caller_step_env_on_conflict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from dataclasses import replace

    from issuesmith.config import PhaseConfig, get_config

    cfg = replace(
        get_config(),
        phases=[
            PhaseConfig(
                name="develop",
                role="implementation",
                entry_step="prepare",
                handler="impl",
                steps=("prepare", "implement"),
            )
        ],
    )
    monkeypatch.setattr("issuesmith.config.get_config", lambda: cfg)
    monkeypatch.setenv(wt.CALLER_STEP_ENV_VAR, "prepare")
    repo, stale_branch = _make_repo_with_diverged_branch(tmp_path)
    subprocess.run(
        ["git", "-C", str(repo), "checkout", stale_branch],
        check=True,
        capture_output=True,
    )
    (repo / "README").write_text("conflict\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "stale conflict"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "main"],
        check=True,
        capture_output=True,
    )
    (repo / "README").write_text("main conflict\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "main conflict"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "fetch", "origin", "main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", stale_branch],
        check=True,
        capture_output=True,
    )
    client = MagicMock()
    result = wt.ensure_base_included(
        repo,
        "main",
        client,
        3408,
        stale_base_comment_template=_STALE_BASE_COMMENT_TEMPLATE,
    )
    assert result is not None
    assert "--from implement" in (result.andon.summary if result.andon else "")
    capsys.readouterr()


def test_ensure_base_included_rev_parse_fails_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
    client = MagicMock()
    result = wt.ensure_base_included(
        repo,
        "main",
        client,
        3408,
        stale_base_comment_template=_STALE_BASE_COMMENT_TEMPLATE,
    )
    assert result is None
    client.issue_comment.assert_not_called()


def test_assert_jobs_clean_passes_when_clean(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
    wt.assert_jobs_clean(repo, "jobs/")


_BASE_CL = "# Changelog\n\n## 0.1.0 - 2026-01-01\n- seed\n"


def test_ensure_base_included_resolves_changelog_only_conflict(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """refs #7398: P0 rebase keeps both CHANGELOG entries when only that file conflicts."""
    origin = _init_repo(tmp_path / "origin")
    clone = _clone(origin, tmp_path / "clone")
    subprocess.run(
        ["git", "-C", str(clone), "checkout", "-b", "feat/changelog"],
        check=True,
        capture_output=True,
    )
    cl_feat = _BASE_CL.replace("- seed\n", "- seed\n- feat line\n")
    (clone / "CHANGELOG.md").write_text(cl_feat, encoding="utf-8")
    _git(clone, "add", "CHANGELOG.md")
    _git(clone, "commit", "-q", "-m", "feat changelog")

    cl_main = _BASE_CL.replace("- seed\n", "- seed\n- main line\n")
    (origin / "CHANGELOG.md").write_text(cl_main, encoding="utf-8")
    _git(origin, "add", "CHANGELOG.md")
    _git(origin, "commit", "-q", "-m", "main changelog")
    _git(clone, "fetch", "origin", "main")

    client = MagicMock()
    result = wt.ensure_base_included(
        clone,
        "main",
        client,
        7398,
        stale_base_comment_template=_STALE_BASE_COMMENT_TEMPLATE,
    )
    assert result is None
    changelog = (clone / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "<<<<<<<" not in changelog
    assert "- feat line" in changelog
    assert "- main line" in changelog
    ancestor = subprocess.run(
        ["git", "-C", str(clone), "merge-base", "--is-ancestor", "origin/main", "HEAD"],
        capture_output=True,
        check=False,
    )
    assert ancestor.returncode == 0
    assert "P0_REBASE: auto-synced" in capsys.readouterr().err


def test_ensure_base_included_changelog_plus_source_conflict_still_andons(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refs #7398: real conflicts still andon without leaving rebase state."""
    origin = _init_repo(tmp_path / "origin")
    clone = _clone(origin, tmp_path / "clone")
    subprocess.run(
        ["git", "-C", str(clone), "checkout", "-b", "feat/mixed"],
        check=True,
        capture_output=True,
    )
    (clone / "CHANGELOG.md").write_text(
        _BASE_CL.replace("- seed\n", "- seed\n- ours cl\n"),
        encoding="utf-8",
    )
    (clone / "src" / "x.py").parent.mkdir(parents=True, exist_ok=True)
    (clone / "src" / "x.py").write_text("ours\n", encoding="utf-8")
    _git(clone, "add", "CHANGELOG.md", "src/x.py")
    _git(clone, "commit", "-q", "-m", "feat")
    monkeypatch.setattr("issuesmith.worktree._branch_on_remote", lambda *_a, **_k: True)

    (origin / "CHANGELOG.md").write_text(
        _BASE_CL.replace("- seed\n", "- seed\n- theirs cl\n"),
        encoding="utf-8",
    )
    (origin / "src" / "x.py").parent.mkdir(parents=True, exist_ok=True)
    (origin / "src" / "x.py").write_text("theirs\n", encoding="utf-8")
    _git(origin, "add", "CHANGELOG.md", "src/x.py")
    _git(origin, "commit", "-q", "-m", "main")
    _git(clone, "fetch", "origin", "main")

    client = MagicMock()
    result = wt.ensure_base_included(
        clone,
        "main",
        client,
        7398,
        stale_base_comment_template=_STALE_BASE_COMMENT_TEMPLATE,
    )
    assert result is not None
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.kind == "decision"
    assert not (clone / ".git" / "rebase-merge").exists()
    assert not (clone / ".git" / "rebase-apply").exists()
    capsys.readouterr()
