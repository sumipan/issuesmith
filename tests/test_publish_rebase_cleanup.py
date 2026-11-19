"""Real-git fixtures for _ensure_rebased runtime-dir cleanup (#3227).

Reproduces the 2026-09-13 nexus #3225 P3 failure mode:
unstaged RUNTIME_DIR_EXCLUDES dirt blocks ``git rebase origin/<base>``.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from issuesmith.bump_commits import BUMP_SUBJECT_PREFIX
from issuesmith.ops.publish import _ensure_rebased, publish

_ALLOW = ["src/issuesmith/ops/publish.py"]


def _read(root: Path, rel: str) -> str:
    return (root / rel).read_text(encoding="utf-8")


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
    assert "publish-local-dirty" in _read(wt, "src/issuesmith/ops/publish.py")
    ancestor = _git(wt, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False)
    assert ancestor.returncode != 0


def _bump_pyproject(repo: Path, version: str, message: str | None = None) -> None:
    _write(repo, "pyproject.toml", f'version = "{version}"\n')
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-m", message or f"{BUMP_SUBJECT_PREFIX}{version}")


def _fake_run_version_bump(worktree: Path, base_branch: str) -> subprocess.CompletedProcess[str]:
    text = (worktree / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        return subprocess.CompletedProcess([], 1, "", "no version line")
    parts = match.group(1).split(".")
    parts[-1] = str(int(parts[-1]) + 1)
    new_v = ".".join(parts)
    (worktree / "pyproject.toml").write_text(
        re.sub(
            r'^version\s*=\s*"[^"]+"',
            f'version = "{new_v}"',
            text,
            count=1,
            flags=re.MULTILINE,
        ),
        encoding="utf-8",
    )
    _git(worktree, "add", "pyproject.toml")
    _git(worktree, "commit", "-m", f"{BUMP_SUBJECT_PREFIX}{new_v}")
    return subprocess.CompletedProcess([], 0, "", "")


def test_rerun_publish_rebumps_from_new_base_version(tmp_path: Path) -> None:
    """refs #7398: parallel publish bumps on base do not block re-publish rebase."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(remote), str(seed)], check=True, capture_output=True)
    _config_identity(seed)
    _write(seed, "src/issuesmith/ops/publish.py", "impl\n")
    _bump_pyproject(seed, "0.1.0", "init")
    _git(seed, "push", "-u", "origin", "main")

    wt = tmp_path / "wt"
    subprocess.run(["git", "clone", str(remote), str(wt)], check=True, capture_output=True)
    _config_identity(wt)
    _git(wt, "checkout", "-b", "feat/issue-7398")
    _write(wt, "src/issuesmith/ops/publish.py", "impl-feat\n")
    _git(wt, "add", "src/issuesmith/ops/publish.py")
    _git(wt, "commit", "-m", "feat impl")
    _bump_pyproject(wt, "0.1.1")

    _bump_pyproject(seed, "0.1.1")
    _git(seed, "push", "origin", "main")
    _git(wt, "fetch", "origin", "main")

    client = MagicMock()
    client.pr_list.return_value = [{"url": "https://github.com/example/pull/1"}]
    with patch("issuesmith.ops.publish.get_forge", return_value=client), patch(
        "issuesmith.ops.publish._run_version_bump", side_effect=_fake_run_version_bump
    ):
        result = publish(
            issue_number=7398,
            branch="feat/issue-7398",
            base_branch="main",
            worktree=wt,
            repo="sumipan/issuesmith",
            issue_repo="sumipan/nexus",
        )
    assert result.status == "OK", result.stderr
    version_line = _read(wt, "pyproject.toml")
    assert 'version = "0.1.2"' in version_line
    subjects = _git(
        wt, "log", "--format=%s", "origin/main..HEAD"
    ).stdout.splitlines()
    bump_subjects = [s for s in subjects if s.startswith(BUMP_SUBJECT_PREFIX)]
    assert len(bump_subjects) == 1
    ancestor = _git(wt, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False)
    assert ancestor.returncode == 0


