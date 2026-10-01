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

_MILESTONE_COMMENT = """\
## P0 blocked: scope:milestone issue
Design-only issue; develop-ready auto-implementation is forbidden.
PIPELINE_STATUS: MILESTONE_BLOCKED"""

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


def test_unpushed_local_commit_not_in_branch(tmp_path: Path) -> None:
    upstream = _init_repo(tmp_path / "upstream")
    clone = _clone(upstream, tmp_path / "clone")
    memory = clone / "chat" / "memory"
    memory.mkdir(parents=True)
    (memory / "log.jsonl").write_text("{}\n", encoding="utf-8")
    _git(clone, "add", "chat")
    _git(clone, "commit", "-q", "-m", "memory append")
    assert _git(clone, "rev-parse", "main") != _git(clone, "rev-parse", "origin/main")

    worktree_dir = tmp_path / "wt"
    wt.prepare_worktree(clone, worktree_dir, "feat/x", wt.resolve_base_ref(clone, "main"))

    assert _git(worktree_dir, "rev-parse", "HEAD") == _git(clone, "rev-parse", "origin/main")
    changed = _git(worktree_dir, "diff", "--name-only", "origin/main...HEAD")
    assert changed == ""
    assert not (worktree_dir / "chat" / "memory" / "log.jsonl").exists()


def test_local_only_ref_is_usable_as_start_point(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "local-only")
    worktree_dir = tmp_path / "wt"
    wt.prepare_worktree(repo, worktree_dir, "feat/y", wt.resolve_base_ref(repo, "main"))
    assert _git(worktree_dir, "rev-parse", "HEAD") == _git(repo, "rev-parse", "main")
    assert _git(worktree_dir, "branch", "--show-current") == "feat/y"


def test_validate_branch_rejects_invalid_name() -> None:
    with pytest.raises(wt.WorktreeError, match="invalid branch name"):
        wt.validate_branch("bad branch")


def test_prepare_worktree_creates_new_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
    worktree_dir = tmp_path / "worktrees" / "issue-3168"
    wt.prepare_worktree(repo, worktree_dir, "feat/issue-3168-aabb", "main")
    assert (worktree_dir / ".git").exists() or (worktree_dir / ".git").is_file()
    branch = subprocess.check_output(
        ["git", "-C", str(worktree_dir), "branch", "--show-current"], text=True
    ).strip()
    assert branch == "feat/issue-3168-aabb"


def test_prepare_worktree_reuses_existing_matching_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
    worktree_dir = tmp_path / "worktrees" / "issue-3168"
    wt.prepare_worktree(repo, worktree_dir, "feat/reuse", "main")
    wt.prepare_worktree(repo, worktree_dir, "feat/reuse", "main")
    branch = subprocess.check_output(
        ["git", "-C", str(worktree_dir), "branch", "--show-current"], text=True
    ).strip()
    assert branch == "feat/reuse"


def test_prepare_worktree_rejects_branch_mismatch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
    worktree_dir = tmp_path / "worktrees" / "issue-3168"
    wt.prepare_worktree(repo, worktree_dir, "feat/one", "main")
    with pytest.raises(wt.WorktreeError, match="branch mismatch"):
        wt.prepare_worktree(repo, worktree_dir, "feat/other", "main")


def test_fetch_base_retries_transient_lock_at_most_three_times(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "clone"
    _git_init_with_main(repo)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", str(repo)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "update-ref", "refs/remotes/origin/main", "HEAD"],
        check=True,
        capture_output=True,
    )

    monkeypatch.setenv("P0_FETCH_LOCK_WAIT", "0")
    sleeps: list[float] = []
    monkeypatch.setattr(wt, "_sleep", lambda s: sleeps.append(s))

    calls: list[str] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:3] == ["git", "-C", str(repo)] and "fetch" in cmd:
            calls.append("fetch")
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr=CANNOT_LOCK_REF_STDERR)
        if "update-ref" in cmd and "-d" in cmd:
            calls.append("delete-ref")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(wt.subprocess, "run", fake_run)

    ok = wt.fetch_base_with_retry(repo, "main")
    assert ok is False
    assert calls.count("fetch") == 3
    assert sleeps == [1, 2, 3]


def test_fetch_base_or_raise_includes_remote_and_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "clone"
    _git_init_with_main(repo)
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", str(repo)],
        check=True,
        capture_output=True,
    )

    def always_fail(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if "fetch" in cmd:
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="network down")
        return subprocess.run(cmd, **kwargs)

    monkeypatch.setenv("P0_FETCH_LOCK_WAIT", "0")
    monkeypatch.setattr(wt, "_sleep", lambda _s: None)
    monkeypatch.setattr(wt.subprocess, "run", always_fail)
    with pytest.raises(wt.WorktreeError, match="failed to fetch sumipan/issuesmith:main"):
        wt.fetch_base_or_raise(repo, "main", remote="sumipan/issuesmith")


def test_prepare_local_worktree_returns_path(tmp_path: Path) -> None:
    repo = _init_repo(tmp_path / "local")
    worktree_dir = tmp_path / "wt-local"
    result = wt.prepare_local_worktree(repo, worktree_dir, "feat/local", "main")
    assert result == worktree_dir
    assert worktree_dir.is_dir()
    assert _git(worktree_dir, "branch", "--show-current") == "feat/local"


def test_clone_if_missing_skips_existing_checkout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    existing = tmp_path / "external" / "issuesmith"
    _git_init_with_main(existing)
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(wt.subprocess, "run", fake_run)
    wt.clone_if_missing(existing, "sumipan/issuesmith", "main")
    assert calls == []


def test_clone_if_missing_clones_when_absent(
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


def test_transient_fetch_markers_detected() -> None:
    assert wt._is_transient_fetch_error(CANNOT_LOCK_REF_STDERR) is True
    assert wt._is_transient_fetch_error("network down") is False


def test_worktree_module_has_no_pipeline_imports() -> None:
    """Public worktree helpers must not import step / workflow modules."""
    import importlib

    mod = importlib.import_module("issuesmith.worktree")
    source_path = Path(mod.__file__).read_text(encoding="utf-8")
    import_block = source_path.split("class WorktreeError", 1)[0]
    forbidden = (
        "issuesmith.steps",
        "scope_gate",
        "state_machine",
        "issue_comment",
    )
    for token in forbidden:
        assert token not in import_block


def test_require_yaml_metadata_rejects_missing_block() -> None:
    with pytest.raises(wt.WorktreeError, match="yaml metadata block"):
        wt.require_yaml_metadata("## Background\nno yaml\n")


def test_handle_milestone_posts_comment_and_returns_failed() -> None:
    client = MagicMock()
    transition = MagicMock()
    result = wt.handle_milestone(
        client,
        3168,
        comment=_MILESTONE_COMMENT,
        on_transition=lambda issue_number: transition(issue_number, "issuesmith:draft-done"),
    )
    assert result.exit_code == 1
    assert result.pipeline_status == "WORKTREE_FAILED"
    transition.assert_called_once_with(3168, "issuesmith:draft-done")
    client.issue_comment.assert_called_once_with(3168, _MILESTONE_COMMENT)
    body = client.issue_comment.call_args.args[1]
    assert "scope:milestone" in body
    assert "MILESTONE_BLOCKED" in body


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


def test_ensure_base_included_already_included_returns_none(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
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


def test_ensure_base_included_rebase_conflict_returns_stale_base(tmp_path: Path) -> None:
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
    assert result.exit_code == 1
    assert result.pipeline_status == "STALE_BASE"
    client.issue_comment.assert_called_once()
    comment = client.issue_comment.call_args.args[1]
    assert "STALE_BASE" in comment


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


def test_assert_jobs_clean_raises_when_dirty(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
    jobs = repo / "jobs"
    jobs.mkdir()
    (jobs / "exec.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(wt.WorktreeError, match="dirty files under jobs/"):
        wt.assert_jobs_clean(repo, "jobs/")
    err = capsys.readouterr().err
    assert "jobs/" in err
    assert "dirty" in err.lower()


def test_assert_jobs_clean_passes_when_clean(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
    wt.assert_jobs_clean(repo, "jobs/")


# --- Deprecated compat re-export (steps.p0_worktree) ---

_COMPAT_NAMES = (
    "WorktreeError",
    "_assert_jobs_clean",
    "_ensure_base_included",
    "_github_client",
    "_handle_milestone",
    "_require_yaml_metadata",
    "fetch_base_with_retry",
    "validate_branch",
)


def _import_compat_module() -> object:
    import importlib
    import sys

    sys.modules.pop("issuesmith.steps.p0_worktree", None)
    with pytest.warns(DeprecationWarning, match="issuesmith.worktree"):
        return importlib.import_module("issuesmith.steps.p0_worktree")


def test_compat_module_reexports_old_names_with_deprecation_warning() -> None:
    compat = _import_compat_module()
    for name in _COMPAT_NAMES:
        assert hasattr(compat, name), name
    assert compat.WorktreeError is wt.WorktreeError
    assert compat.fetch_base_with_retry is wt.fetch_base_with_retry
    assert compat.validate_branch is wt.validate_branch


def test_compat_assert_jobs_clean_delegates_with_jobs_default(tmp_path: Path) -> None:
    compat = _import_compat_module()
    repo = tmp_path / "repo"
    _git_init_with_main(repo)
    compat._assert_jobs_clean(repo)
    (repo / "jobs").mkdir()
    (repo / "jobs" / "exec.jsonl").write_text("{}\n", encoding="utf-8")
    with pytest.raises(wt.WorktreeError):
        compat._assert_jobs_clean(repo)


def test_compat_require_yaml_metadata_delegates() -> None:
    compat = _import_compat_module()
    compat._require_yaml_metadata("```yaml\nbase_branch: main\n```\n")
    with pytest.raises(wt.WorktreeError):
        compat._require_yaml_metadata("no metadata")


def test_compat_handle_milestone_comments_and_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    compat = _import_compat_module()
    calls: list[int] = []
    monkeypatch.setattr(compat, "_transition_to_draft_done", calls.append)
    client = MagicMock()
    result = compat._handle_milestone(client, 42)
    assert result.exit_code == 1
    assert result.pipeline_status == "WORKTREE_FAILED"
    assert calls == [42]
    body = client.issue_comment.call_args.args[1]
    assert "MILESTONE_BLOCKED" in body
