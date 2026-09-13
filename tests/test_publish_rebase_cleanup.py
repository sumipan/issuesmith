"""Real-git fixtures for _ensure_rebased runtime-dir cleanup (#3227).

Reproduces the 2026-09-13 nexus #3225 P3 failure mode:
unstaged RUNTIME_DIR_EXCLUDES dirt blocks ``git rebase origin/<base>``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from issuesmith.ops.publish import _ensure_rebased

_ALLOW = ["src/issuesmith/ops/publish.py"]


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _config_identity(repo: Path) -> None:
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")


def _write(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _setup_behind_worktree(tmp_path: Path) -> Path:
    """bare origin + feature worktree whose HEAD is behind origin/main.

    Initial tree includes ``jobs/audit.jsonl`` (RUNTIME_DIR_EXCLUDES) and
    ``src/issuesmith/ops/publish.py`` (allow_paths).
    """
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )

    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "clone", str(remote), str(seed)],
        check=True,
        capture_output=True,
    )
    _config_identity(seed)
    _write(seed, "README", "init\n")
    _write(seed, "jobs/audit.jsonl", "audit-v1\n")
    _write(seed, "src/issuesmith/ops/publish.py", "publish-v1\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "init")
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    wt = tmp_path / "wt"
    subprocess.run(
        ["git", "clone", str(remote), str(wt)],
        check=True,
        capture_output=True,
    )
    _config_identity(wt)
    _git(wt, "checkout", "-b", "feat/issue-3227")
    _write(wt, "src/issuesmith/ops/publish.py", "publish-v1-feat\n")
    _git(wt, "add", "--", "src/issuesmith/ops/publish.py")
    _git(wt, "commit", "-m", "feat change")

    # Advance origin/main so feature HEAD is behind.
    _write(seed, "README", "init\norigin-ahead\n")
    _git(seed, "add", "--", "README")
    _git(seed, "commit", "-m", "origin advances")
    _git(seed, "push", "origin", "main")

    return wt


def test_runtime_dirty_discarded_and_rebase_succeeds(tmp_path: Path) -> None:
    wt = _setup_behind_worktree(tmp_path)
    _write(wt, "jobs/audit.jsonl", "audit-dirty-runtime\n")

    status_before = _git(wt, "status", "--porcelain").stdout
    assert "jobs/audit.jsonl" in status_before

    result = _ensure_rebased(wt, "main", _ALLOW)
    assert result is None

    ancestor = _git(wt, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False)
    assert ancestor.returncode == 0
    assert (wt / "jobs" / "audit.jsonl").read_text(encoding="utf-8") == "audit-v1\n"
    assert "jobs/audit.jsonl" not in _git(wt, "status", "--porcelain").stdout


def test_allow_paths_dirty_returns_dirty_worktree(tmp_path: Path) -> None:
    wt = _setup_behind_worktree(tmp_path)
    _write(wt, "jobs/audit.jsonl", "audit-dirty-runtime\n")
    _write(wt, "src/issuesmith/ops/publish.py", "publish-local-dirty\n")

    result = _ensure_rebased(wt, "main", _ALLOW)
    assert result is not None
    assert result.status == "DIRTY_WORKTREE"
    assert result.exit_code == 1
    assert "src/issuesmith/ops/publish.py" in result.stderr

    # Runtime dirt discarded; allow_paths dirt remains; no rebase performed.
    assert (wt / "jobs" / "audit.jsonl").read_text(encoding="utf-8") == "audit-v1\n"
    assert "publish-local-dirty" in (wt / "src/issuesmith/ops/publish.py").read_text(
        encoding="utf-8"
    )
    ancestor = _git(wt, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False)
    assert ancestor.returncode != 0


def test_real_conflict_returns_rebase_conflict(tmp_path: Path) -> None:
    wt = _setup_behind_worktree(tmp_path)
    seed = tmp_path / "seed"
    # Conflicting change on origin/main at the same path/lines as the feat commit.
    _write(seed, "src/issuesmith/ops/publish.py", "publish-origin-conflict\n")
    _git(seed, "add", "--", "src/issuesmith/ops/publish.py")
    _git(seed, "commit", "-m", "origin conflict")
    _git(seed, "push", "origin", "main")

    pre_head = _git(wt, "rev-parse", "HEAD").stdout.strip()
    result = _ensure_rebased(wt, "main", _ALLOW)

    assert result is not None
    assert result.status == "REBASE_CONFLICT"
    assert result.exit_code == 1
    assert "src/issuesmith/ops/publish.py" in result.stderr
    assert _git(wt, "rev-parse", "HEAD").stdout.strip() == pre_head
    # rebase --abort leaves a clean worktree (no conflict markers / in-progress rebase).
    assert not (wt / ".git" / "rebase-merge").exists()
    assert not (wt / ".git" / "rebase-apply").exists()
