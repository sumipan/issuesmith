"""Re-publish from a clone whose fetch refspec only covers main (sumipan/nexus#3628).

``git fetch origin <branch>`` does not create ``origin/<branch>`` in such a clone, so the
rebase-aware push used to take the "new branch" path and was rejected non-fast-forward.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from issuesmith.ops import publish as publish_mod
from issuesmith.ops.publish import _push_branch

_BRANCH = "feat/issue-3628"


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], check=check, capture_output=True, text=True)


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(remote), str(seed)], check=True, capture_output=True)
    _git(seed, "config", "user.email", "t@t")
    _git(seed, "config", "user.name", "t")
    (seed / "a.txt").write_text("a\n", encoding="utf-8")
    _git(seed, "add", "a.txt")
    _git(seed, "commit", "-m", "seed")
    _git(seed, "push", "-u", "origin", "main")
    # the production clones are single-branch: only main is fetched
    wt = tmp_path / "wt"
    subprocess.run(["git", "clone", "--single-branch", "-b", "main", str(remote), str(wt)], check=True, capture_output=True)
    _git(wt, "config", "user.email", "t@t")
    _git(wt, "config", "user.name", "t")
    assert _git(wt, "config", "--get-all", "remote.origin.fetch").stdout.strip() == "+refs/heads/main:refs/remotes/origin/main"
    _git(wt, "checkout", "-b", _BRANCH)
    (wt / "b.txt").write_text("b\n", encoding="utf-8")
    _git(wt, "add", "b.txt")
    _git(wt, "commit", "-m", "feature")
    _git(wt, "push", "-u", "origin", _BRANCH)  # first publish
    return remote, wt


def test_second_publish_after_rewrite_uses_lease_in_single_branch_clone(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(publish_mod, "_PUSH_RETRY_DELAYS", (0.0, 0.0))
    remote, wt = _setup(tmp_path)
    _git(wt, "update-ref", "-d", f"refs/remotes/origin/{_BRANCH}")  # the tracking ref is gone, as after a fresh tick
    _git(wt, "commit", "--amend", "--no-edit", "-m", "feature (rebased)")  # rewritten tip, same content
    local = _git(wt, "rev-parse", "HEAD").stdout.strip()

    result = _push_branch(wt, _BRANCH)

    assert result is None, getattr(result, "stderr", result)
    remote_tip = _git(remote, "rev-parse", f"refs/heads/{_BRANCH}").stdout.strip()
    assert remote_tip == local


def test_first_publish_in_single_branch_clone_still_plain_push(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(publish_mod, "_PUSH_RETRY_DELAYS", (0.0, 0.0))
    remote, wt = _setup(tmp_path)
    _git(wt, "checkout", "-b", "feat/issue-9999")
    (wt / "c.txt").write_text("c\n", encoding="utf-8")
    _git(wt, "add", "c.txt")
    _git(wt, "commit", "-m", "another")

    assert _push_branch(wt, "feat/issue-9999") is None
    assert _git(remote, "rev-parse", "refs/heads/feat/issue-9999").returncode == 0
