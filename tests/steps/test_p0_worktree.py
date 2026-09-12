"""Fixture tests for issuesmith.steps.p0_worktree (#3168).

Git clone/fetch stderr fixtures were captured 2026-09-13 (CLAUDE.md §10):

  git clone --depth 1 --branch main file://…/bare.git clone_ok
  → ``Cloning into 'clone_ok'...`` (exit 0; fetch of matching ref is silent)

  git clone --depth 1 --branch main \\
      https://github.com/sumipan/does-not-exist-zzzz-3168.git
  → remote: Repository not found. / fatal: repository '…' not found

  git -C badrepo fetch origin refs/heads/main:refs/remotes/origin/main
    (remote = file:///nonexistent/nowhere.git)
  → fatal: '…' does not appear to be a git repository
    fatal: Could not read from remote repository.

  git update-ref with stale ``.git/refs/heads/main.lock``
  → fatal: … cannot lock ref 'refs/heads/main': Unable to create '….lock': File exists.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from issuesmith.steps import p0_worktree as p0
from issuesmith.steps.base import StepContext

# --- Real git strings (verbatim captures; values unchanged) ---

CLONE_SUCCESS_STDERR = "Cloning into 'clone_ok'...\n"

CLONE_FAIL_STDERR = (
    "Cloning into 'clone_fail'...\n"
    "remote: Repository not found.\n"
    "fatal: repository 'https://github.com/sumipan/does-not-exist-zzzz-3168.git/' not found\n"
)

FETCH_FAIL_STDERR = (
    "fatal: '/nonexistent/nowhere.git' does not appear to be a git repository\n"
    "fatal: Could not read from remote repository.\n"
    "\n"
    "Please make sure you have the correct access rights\n"
    "and the repository exists.\n"
)

# Real ``cannot lock ref`` from git update-ref with a stale .lock file (2026-09-13).
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

UNABLE_TO_UPDATE_LOCAL_REF_STDERR = (
    "error: cannot lock ref 'refs/remotes/origin/main': "
    "is at 1111111111111111111111111111111111111111 but expected "
    "2222222222222222222222222222222222222222\n"
    "From https://github.com/example/repo\n"
    " ! [new branch]      main       -> origin/main  (unable to update local ref)\n"
)


def _ctx(**overrides: str) -> StepContext:
    base = {
        "issue_number": "3168",
        "base_branch": "main",
        "handler_name": "develop",
        "is_cross_repo": "false",
        "target_clone_path": "",
        "source": "",
        "workflow_name": "issuesmith",
        "m1_result_filename": "",
        "m1r_result_filename": "",
        "worktree_path": "/tmp/wt-3168",
        "target_worktree_path": "",
        "branch": "feat/issue-3168-7b4d70e2",
        "target_repo": "",
        "allow_paths": "- src/issuesmith/steps/p0_worktree.py",
        "issue_repo": "sumipan/nexus",
        "has_diary_changes": "false",
        "diary_worktree_path": "",
    }
    base.update(overrides)
    return StepContext(**base)


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


def test_clone_success_fixture_contains_real_cloning_into_string() -> None:
    assert "Cloning into 'clone_ok'..." in CLONE_SUCCESS_STDERR
    assert p0._is_transient_fetch_error(CLONE_SUCCESS_STDERR) is False


def test_clone_fail_fixture_is_real_repository_not_found() -> None:
    assert "Repository not found" in CLONE_FAIL_STDERR
    assert "does-not-exist-zzzz-3168" in CLONE_FAIL_STDERR
    completed = MagicMock(
        returncode=128,
        stdout="",
        stderr=CLONE_FAIL_STDERR,
    )
    assert p0._classify_clone_result(completed) == "fail"


def test_fetch_fail_fixture_is_real_nonexistent_remote() -> None:
    assert "does not appear to be a git repository" in FETCH_FAIL_STDERR
    assert p0._is_transient_fetch_error(FETCH_FAIL_STDERR) is False


def test_cannot_lock_ref_fixture_is_detected_as_transient() -> None:
    assert "cannot lock ref" in CANNOT_LOCK_REF_STDERR
    assert p0._is_transient_fetch_error(CANNOT_LOCK_REF_STDERR) is True


def test_unable_to_update_local_ref_is_transient() -> None:
    assert "unable to update local ref" in UNABLE_TO_UPDATE_LOCAL_REF_STDERR
    assert p0._is_transient_fetch_error(UNABLE_TO_UPDATE_LOCAL_REF_STDERR) is True


def test_fetch_base_retries_transient_lock_at_most_three_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "clone"
    _git_init_with_main(repo)
    # remote-tracking ref so update-ref -d has a target
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", str(repo)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "update-ref",
            "refs/remotes/origin/main",
            "HEAD",
        ],
        check=True,
        capture_output=True,
    )

    monkeypatch.setenv("P0_FETCH_LOCK_WAIT", "0")
    sleeps: list[float] = []
    monkeypatch.setattr(p0, "_sleep", lambda s: sleeps.append(s))

    calls: list[str] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:3] == ["git", "-C", str(repo)] and "fetch" in cmd:
            calls.append("fetch")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=CANNOT_LOCK_REF_STDERR)
        if "update-ref" in cmd and "-d" in cmd:
            calls.append("delete-ref")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(p0.subprocess, "run", fake_run)

    ok = p0.fetch_base_with_retry(repo, "main")
    assert ok is False
    assert calls.count("fetch") == 3
    assert sleeps == [1, 2, 3]


def test_scope_milestone_transitions_to_draft_done_and_fails() -> None:
    client = MagicMock()
    client.issue_get.return_value = {
        "labels": [{"name": "scope:milestone"}, {"name": "issuesmith:develop-ready"}],
        "body": "```yaml\ntarget_repo: sumipan/issuesmith\n```\n",
    }
    with (
        patch.object(p0, "_github_client", return_value=client),
        patch.object(p0, "_transition") as transition,
        patch.object(p0, "_repo_root", return_value=Path("/tmp/nexus")),
        patch.object(p0, "validate_branch"),
    ):
        result = p0.run(_ctx())
    assert result.exit_code == 1
    assert result.pipeline_status == "WORKTREE_FAILED"
    transition.assert_called_once_with(3168, "issuesmith:draft-done")
    client.issue_comment.assert_called_once()
    body = client.issue_comment.call_args.args[1]
    assert "scope:milestone" in body
    assert "MILESTONE_BLOCKED" in body


def test_validate_branch_rejects_invalid_name() -> None:
    with pytest.raises(p0.WorktreeError, match="invalid branch name"):
        p0.validate_branch("bad branch")


def test_prepare_worktree_creates_new_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
    wt = tmp_path / "worktrees" / "issue-3168"
    p0.prepare_worktree(repo, wt, "feat/issue-3168-aabb", "main")
    assert (wt / ".git").exists() or (wt / ".git").is_file()
    branch = subprocess.check_output(
        ["git", "-C", str(wt), "branch", "--show-current"], text=True
    ).strip()
    assert branch == "feat/issue-3168-aabb"


def test_prepare_worktree_reuses_existing_matching_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
    wt = tmp_path / "worktrees" / "issue-3168"
    p0.prepare_worktree(repo, wt, "feat/reuse", "main")
    # second call must be idempotent
    p0.prepare_worktree(repo, wt, "feat/reuse", "main")
    branch = subprocess.check_output(
        ["git", "-C", str(wt), "branch", "--show-current"], text=True
    ).strip()
    assert branch == "feat/reuse"


def test_prepare_worktree_rejects_branch_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
    wt = tmp_path / "worktrees" / "issue-3168"
    p0.prepare_worktree(repo, wt, "feat/one", "main")
    with pytest.raises(p0.WorktreeError, match="branch mismatch"):
        p0.prepare_worktree(repo, wt, "feat/other", "main")


def test_target_worktree_path_rejects_absolute_and_traversal() -> None:
    with pytest.raises(p0.WorktreeError, match="must be relative"):
        p0._validate_target_worktree_path("/abs/path")
    with pytest.raises(p0.WorktreeError, match="parent traversal"):
        p0._validate_target_worktree_path("../escape")


def test_yaml_metadata_required() -> None:
    with pytest.raises(p0.WorktreeError, match="yaml metadata block"):
        p0._require_yaml_metadata("## 背景\nno yaml\n")


def test_local_run_success(tmp_path: Path) -> None:
    repo = tmp_path / "nexus"
    _git_init_with_main(repo)
    wt = tmp_path / "nexus" / ".claude" / "worktrees" / "issue-3168-abcd"
    client = MagicMock()
    client.issue_get.return_value = {
        "labels": [{"name": "issuesmith:develop-ready"}],
        "body": "```yaml\nbase_branch: main\nallow_paths:\n  - src/**\n```\n\n## 設計\n",
    }
    with (
        patch.object(p0, "_github_client", return_value=client),
        patch.object(p0, "_repo_root", return_value=repo),
    ):
        result = p0.run(
            _ctx(
                worktree_path=str(wt),
                branch="feat/issue-3168-local",
                is_cross_repo="false",
            )
        )
    assert result.exit_code == 0
    assert result.pipeline_status == "WORKTREE_READY"
    assert wt.is_dir()


def test_cross_repo_clone_failure_uses_real_fail_string(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "nexus"
    _git_init_with_main(repo)
    client = MagicMock()
    client.issue_get.return_value = {
        "labels": [],
        "body": "```yaml\ntarget_repo: sumipan/issuesmith\n```\n",
    }

    real_run = subprocess.run

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[0] == "git" and cmd[1] == "clone":
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr=CLONE_FAIL_STDERR)
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(p0.subprocess, "run", fake_run)
    with (
        patch.object(p0, "_github_client", return_value=client),
        patch.object(p0, "_repo_root", return_value=repo),
    ):
        result = p0.run(
            _ctx(
                is_cross_repo="true",
                target_repo="sumipan/does-not-exist-zzzz-3168",
                target_clone_path=".claude/external/does-not-exist-zzzz-3168",
                target_worktree_path=(
                    ".claude/external/does-not-exist-zzzz-3168/worktrees/issue-3168-x"
                ),
                branch="feat/issue-3168-x",
            )
        )
    assert result.exit_code == 1
    assert result.pipeline_status == "WORKTREE_FAILED"
