"""Public ``issuesmith.verbs.worktree`` orchestration (#6581)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from issuesmith.verbs import worktree as vwt

_STALE_BASE_COMMENT_TEMPLATE = """\
## P0 stop: base_branch sync failed (rebase conflict)

`origin/{base_branch}` rebase conflict. Resolve manually and re-dispatch.

**Conflict files:**
```
{conflict_files}
```

PIPELINE_STATUS: STALE_BASE"""

_MILESTONE_COMMENT = """\
## P0 blocked: scope:milestone issue
Design-only issue; develop-ready auto-implementation is forbidden.
PIPELINE_STATUS: MILESTONE_BLOCKED"""


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(path: Path, *, origin: bool = True) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    (path / "README.md").write_text("base\n", encoding="utf-8")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "init")
    if origin:
        subprocess.run(
            ["git", "-C", str(path), "remote", "add", "origin", str(path)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(path), "update-ref", "refs/remotes/origin/main", "HEAD"],
            check=True,
            capture_output=True,
        )
    return path


def test_prepare_worktree_rejects_invalid_branch_before_mutation(tmp_path: Path) -> None:
    """Refs sumipan/nexus#6581 — invalid branch names fail before filesystem changes."""
    repo = _init_repo(tmp_path / "host")
    wt_path = tmp_path / "wt-bad"
    with pytest.raises(vwt.WorktreeError, match="invalid branch name"):
        vwt.prepare_worktree(repo, "main", "a..b", wt_path)
    assert not wt_path.exists()


def test_prepare_worktree_clones_fetches_and_returns_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#6581 — cross-repo path runs clone, fetch, then worktree once."""
    clone_path = tmp_path / "external" / "issuesmith"
    wt_path = tmp_path / "worktrees" / "issue-x"
    order: list[str] = []

    def track_clone(clone_dir: Path, target_repo: str, base: str) -> None:
        order.append("clone")
        _init_repo(clone_dir)

    real_fetch = vwt.fetch_base_or_raise

    def track_fetch(repo_dir: Path, base: str, *, remote: str = "origin") -> None:
        order.append("fetch")
        real_fetch(repo_dir, base, remote=remote)

    real_prepare = vwt._prepare_worktree

    def track_prepare(repo_dir: Path, worktree_dir: Path, branch: str, base_ref: str) -> None:
        order.append("worktree")
        real_prepare(repo_dir, worktree_dir, branch, base_ref)

    monkeypatch.setattr(vwt, "clone_if_missing", track_clone)
    monkeypatch.setattr(vwt, "fetch_base_or_raise", track_fetch)
    monkeypatch.setattr(vwt, "_prepare_worktree", track_prepare)

    result = vwt.prepare_worktree(
        tmp_path / "host",
        "main",
        "feat/cross",
        wt_path,
        external_dir=clone_path,
        target_repo="sumipan/issuesmith",
    )
    assert order == ["clone", "fetch", "worktree"]
    assert result == wt_path.resolve()
    assert wt_path.is_dir()
    assert _git(wt_path, "branch", "--show-current") == "feat/cross"


def test_prepare_worktree_reuses_matching_clean_worktree(tmp_path: Path) -> None:
    """Refs sumipan/nexus#6581 — a second call with the same inputs returns the same path."""
    repo = _init_repo(tmp_path / "host")
    worktree_dir = tmp_path / "wt-reuse"
    first = vwt.prepare_worktree(repo, "main", "feat/reuse", worktree_dir)
    second = vwt.prepare_worktree(repo, "main", "feat/reuse", worktree_dir)
    assert first == second == worktree_dir.resolve()


def test_prepare_worktree_rejects_dirty_clean_path(tmp_path: Path) -> None:
    """Refs sumipan/nexus#6581 — dirty ``clean_path`` raises before returning."""
    repo = _init_repo(tmp_path / "host")
    worktree_dir = tmp_path / "wt-dirty"
    vwt.prepare_worktree(repo, "main", "feat/dirty", worktree_dir)
    jobs = worktree_dir / "jobs"
    jobs.mkdir()
    (jobs / "exec.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(vwt.WorktreeError, match="is dirty|dirty files"):
        vwt.prepare_worktree(
            repo,
            "main",
            "feat/dirty",
            worktree_dir,
            clean_path="jobs/",
        )


def test_prepare_worktree_rejects_partial_cross_repo_arguments(tmp_path: Path) -> None:
    """Refs sumipan/nexus#6581 — cross-repo clone args must include ``external_dir``."""
    repo = _init_repo(tmp_path / "host")
    wt_path = tmp_path / "wt-partial"
    with pytest.raises(vwt.WorktreeError, match="external_dir and target_repo"):
        vwt.prepare_worktree(
            repo, "main", "feat/y", wt_path, target_repo="sumipan/issuesmith"
        )
    ext_only = _init_repo(tmp_path / "ext-only")
    with pytest.raises(vwt.WorktreeError, match="external_dir and target_repo"):
        vwt.prepare_worktree(
            repo,
            "main",
            "feat/z",
            wt_path,
            external_dir=ext_only,
        )


def test_worktree_public_helpers_preserve_outcomes(tmp_path: Path) -> None:
    """Refs sumipan/nexus#6581 — forge helpers match legacy ``issuesmith.worktree`` outcomes."""
    from issuesmith import worktree as internal

    repo = _init_repo(tmp_path / "repo")
    subprocess.run(
        ["git", "-C", str(repo), "fetch", "origin", "main"],
        check=True,
        capture_output=True,
    )
    client = MagicMock()
    included_internal = internal.ensure_base_included(
        repo,
        "main",
        client,
        6581,
        stale_base_comment_template=_STALE_BASE_COMMENT_TEMPLATE,
    )
    included_public = vwt.ensure_base_included(
        repo,
        "main",
        client,
        6581,
        stale_base_comment_template=_STALE_BASE_COMMENT_TEMPLATE,
    )
    assert included_internal == included_public is None
    client.issue_comment.assert_not_called()

    transition = MagicMock()
    milestone_internal = internal.handle_milestone(
        client,
        6581,
        comment=_MILESTONE_COMMENT,
        on_transition=lambda issue_number: transition(issue_number, "issuesmith:draft-done"),
    )
    client.reset_mock()
    transition.reset_mock()
    milestone_public = vwt.block_design_only_issue(
        client,
        6581,
        comment=_MILESTONE_COMMENT,
        on_transition=lambda issue_number: transition(issue_number, "issuesmith:draft-done"),
    )
    assert milestone_public.exit_code == milestone_internal.exit_code == 1
    assert milestone_public.pipeline_status == milestone_internal.pipeline_status == "WORKTREE_FAILED"
    transition.assert_called_once_with(6581, "issuesmith:draft-done")
    client.issue_comment.assert_called_once_with(6581, _MILESTONE_COMMENT)
