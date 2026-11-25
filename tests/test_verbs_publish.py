"""Verb-level publish helpers (refs sumipan/nexus#8011)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from ghdag.forge import LocalForge

from issuesmith.ops.publish import _UNRESTRICTED_ALLOW_PATHS, PublishResult
from issuesmith.verbs.publish import pr_title_and_body, publish_pr


def test_pr_title_and_body_same_repo() -> None:
    title, body = pr_title_and_body(7, "o/r", "o/r")
    assert title == "Implement Issue #7"
    assert body.endswith("Refs #7")


def test_pr_title_and_body_cross_repo() -> None:
    title, body = pr_title_and_body(7, "o/x", "o/r")
    assert title == "Implement o/r#7"
    assert body.endswith("Refs o/r#7")


def test_publish_pr_converts_context_allow_paths() -> None:
    captured: dict[str, object] = {}

    def fake_publish(**kwargs: object) -> PublishResult:
        captured.update(kwargs)
        return PublishResult(status="OK", pr_url="https://example/pr/1")

    raw = "- src/a.py\n- tests/test_a.py"
    with patch("issuesmith.verbs.publish.publish", fake_publish):
        publish_pr(
            Path("/tmp/wt"),
            issue_number=1,
            branch="feat/x",
            base_branch="main",
            repo="o/r",
            issue_repo="o/r",
            allow_paths=raw,
        )
    assert captured["allow_paths"] == ["src/a.py", "tests/test_a.py"]

    with patch("issuesmith.verbs.publish.publish", fake_publish):
        publish_pr(
            Path("/tmp/wt"),
            issue_number=1,
            branch="feat/x",
            base_branch="main",
            repo="o/r",
            issue_repo="o/r",
            allow_paths="",
        )
    assert captured["allow_paths"] is None

    with patch("issuesmith.verbs.publish.publish", fake_publish):
        publish_pr(
            Path("/tmp/wt"),
            issue_number=1,
            branch="feat/x",
            base_branch="main",
            repo="o/r",
            issue_repo="o/r",
            allow_paths=_UNRESTRICTED_ALLOW_PATHS,
        )
    assert captured["allow_paths"] is None


def test_publish_pr_normalizes_called_process_error() -> None:
    exc = subprocess.CalledProcessError(128, ["git"], stderr="fatal")
    with patch("issuesmith.verbs.publish.publish", side_effect=exc):
        result = publish_pr(
            Path("/tmp/wt"),
            issue_number=1,
            branch="feat/x",
            base_branch="main",
            repo="o/r",
            issue_repo="o/r",
        )
    assert result.status == "GIT_FAILED"
    assert result.stderr == "fatal"
    assert result.exit_code == 128


def _stub_publish_deps(monkeypatch: pytest.MonkeyPatch, forge: LocalForge) -> None:
    monkeypatch.setattr("issuesmith.ops.publish.get_forge", lambda **_k: forge)
    monkeypatch.setattr(
        "issuesmith.ops.publish._commit_if_needed",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "issuesmith.ops.publish._ensure_rebased",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "issuesmith.ops.publish._check_commit_diff_gates",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "issuesmith.ops.publish._maybe_bump_version",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "issuesmith.ops.publish._push_branch",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "issuesmith.ops.publish._ahead_commit_count",
        lambda *args, **kwargs: 1,
    )


def _git(worktree: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(worktree), *args], check=True, capture_output=True)


def _repo_with_commit(tmp_path: Path) -> tuple[Path, str]:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "T")
    (seed / "README").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "README")
    _git(seed, "commit", "-m", "base")
    subprocess.run(["git", "clone", "--bare", str(seed), str(origin)], check=True, capture_output=True)
    worktree = tmp_path / "wt"
    subprocess.run(["git", "clone", str(origin), str(worktree)], check=True, capture_output=True)
    _git(worktree, "config", "user.email", "t@example.com")
    _git(worktree, "config", "user.name", "T")
    branch = "feat/publish-verb"
    _git(worktree, "checkout", "-b", branch)
    (worktree / "README").write_text("feat\n", encoding="utf-8")
    _git(worktree, "add", "README")
    _git(worktree, "commit", "-m", "feat")
    return worktree, branch


def test_publish_pr_reuses_open_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    forge = LocalForge(tmp_path / "forge")
    worktree, branch = _repo_with_commit(tmp_path)
    open_url = forge.pr_create(
        base="main",
        head=branch,
        title="open pr",
        body="body",
        repo="o/r",
    )
    before = len(forge.pr_list(state="all", repo="o/r"))
    _stub_publish_deps(monkeypatch, forge)
    result = publish_pr(
        worktree,
        issue_number=9,
        branch=branch,
        base_branch="main",
        repo="o/r",
        issue_repo="o/r",
    )
    assert result.status == "OK"
    assert result.pr_url == open_url
    assert len(forge.pr_list(state="all", repo="o/r")) == before


def test_publish_pr_does_not_reuse_closed_pr(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    forge = LocalForge(tmp_path / "forge")
    worktree, branch = _repo_with_commit(tmp_path)
    closed_url = forge.pr_create(
        base="main",
        head=branch,
        title="closed pr",
        body="body",
        repo="o/r",
    )
    closed_number = int(str(closed_url).rstrip("/").split("/")[-1])
    closed = forge._load_pull(closed_number)
    closed["state"] = "closed"
    forge._write_json(forge._pull_path(closed_number), closed)
    _stub_publish_deps(monkeypatch, forge)
    result = publish_pr(
        worktree,
        issue_number=9,
        branch=branch,
        base_branch="main",
        repo="o/r",
        issue_repo="o/r",
    )
    assert result.status == "OK"
    open_prs = forge.pr_list(state="open", head=branch, repo="o/r")
    assert len(open_prs) == 1
    assert result.pr_url == open_prs[0].get("url")
