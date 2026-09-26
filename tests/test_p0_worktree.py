"""Tests for resolve_base_ref origin-first resolution (nexus #4111).

P0 must branch from ``origin/<base>`` when it exists so the branch start point
matches the ``origin/<base>...HEAD`` range that pr_scope uses. A local base that
is ahead of origin (unpushed auto-commits) must not leak into the branch.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from issuesmith.steps.p0_worktree import (
    WorktreeError,
    prepare_worktree,
    resolve_base_ref,
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


def test_prefers_origin_when_both_exist(tmp_path: Path) -> None:
    upstream = _init_repo(tmp_path / "upstream")
    clone = _clone(upstream, tmp_path / "clone")
    assert resolve_base_ref(clone, "main") == "origin/main"


def test_falls_back_to_local_when_origin_missing(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "local-only")
    assert resolve_base_ref(repo, "main") == "refs/heads/main"


def test_raises_when_neither_exists(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "local-only")
    with pytest.raises(WorktreeError):
        resolve_base_ref(repo, "develop")


def test_unpushed_local_commit_not_in_branch(tmp_path: Path) -> None:
    upstream = _init_repo(tmp_path / "upstream")
    clone = _clone(upstream, tmp_path / "clone")
    # Simulate a daemon auto-commit on local main that is not pushed yet.
    memory = clone / "chat" / "memory"
    memory.mkdir(parents=True)
    (memory / "log.jsonl").write_text("{}\n", encoding="utf-8")
    _git(clone, "add", "chat")
    _git(clone, "commit", "-q", "-m", "memory append")
    assert _git(clone, "rev-parse", "main") != _git(clone, "rev-parse", "origin/main")

    wt = tmp_path / "wt"
    prepare_worktree(clone, wt, "feat/x", resolve_base_ref(clone, "main"))

    assert _git(wt, "rev-parse", "HEAD") == _git(clone, "rev-parse", "origin/main")
    changed = _git(wt, "diff", "--name-only", "origin/main...HEAD")
    assert changed == ""
    assert not (wt / "chat" / "memory" / "log.jsonl").exists()


def test_local_only_ref_is_usable_as_start_point(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "local-only")
    wt = tmp_path / "wt"
    prepare_worktree(repo, wt, "feat/y", resolve_base_ref(repo, "main"))
    assert _git(wt, "rev-parse", "HEAD") == _git(repo, "rev-parse", "main")
    assert _git(wt, "branch", "--show-current") == "feat/y"
