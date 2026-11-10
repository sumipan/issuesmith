"""BaseFreshnessGate.fix falls back to rebase and reports failure; _run_pytest has a timeout."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from issuesmith import worktree as wt_mod
from issuesmith.gates import NON_REPAIRABLE_RULE_IDS, is_non_repairable_rule_id
from issuesmith.gates import worktree as wt


def _init_git_repo(repo: Path) -> None:
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


def _proc(rc: int, stderr: str = "", stdout: str = "") -> MagicMock:
    return MagicMock(returncode=rc, stdout=stdout, stderr=stderr)


def _calls(mock) -> list[list[str]]:
    return [list(c.args[0]) for c in mock.call_args_list]


_TRANSIENT_FETCH_STDERR = (
    "error: cannot lock ref 'refs/remotes/origin/main': is at aaa but expected bbb\n"
    " ! aaa..bbb  main       -> origin/main  (unable to update local ref)\n"
)

_REAL_SUBPROCESS_RUN = subprocess.run


def _worktree_subprocess_side_effect(
    repo: Path,
    *,
    fetch_results: list[tuple[int, str]],
):
    fetch_idx = {"n": 0}
    repo_resolved = repo.resolve()

    def _is_repo_cmd(cmd: list[str]) -> bool:
        return (
            len(cmd) >= 3
            and cmd[0] == "git"
            and cmd[1] == "-C"
            and Path(cmd[2]).resolve() == repo_resolved
        )

    def fake_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if _is_repo_cmd(cmd) and "fetch" in cmd:
            i = fetch_idx["n"]
            fetch_idx["n"] += 1
            rc, err = fetch_results[min(i, len(fetch_results) - 1)]
            return subprocess.CompletedProcess(cmd, rc, stdout="", stderr=err)
        if _is_repo_cmd(cmd) and "update-ref" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return _REAL_SUBPROCESS_RUN(cmd, **kwargs)

    return fake_run


def test_check_succeeds_after_transient_fetch_then_success(tmp_path: Path, monkeypatch):
    repo = tmp_path / "wt"
    _init_git_repo(repo)
    gate = wt.BaseFreshnessGate(repo, "main")
    monkeypatch.setenv("ISSUESMITH_FETCH_LOCK_WAIT", "0")
    monkeypatch.setattr(wt_mod, "_sleep", lambda _s: None)
    fetch_side = _worktree_subprocess_side_effect(
        repo,
        fetch_results=[(1, _TRANSIENT_FETCH_STDERR), (0, "")],
    )

    def gate_run(cmd, **kwargs):  # type: ignore[no-untyped-def]
        if cmd[:2] == ["git", "rev-list"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="0\n", stderr="")
        return fetch_side(cmd, **kwargs)

    with patch.object(wt_mod.subprocess, "run", side_effect=fetch_side):
        with patch.object(wt.subprocess, "run", side_effect=gate_run):
            assert gate.check("", []) == []


def test_check_returns_transient_exhausted_violation(tmp_path: Path, monkeypatch):
    repo = tmp_path / "wt"
    _init_git_repo(repo)
    gate = wt.BaseFreshnessGate(repo, "main")
    monkeypatch.setenv("ISSUESMITH_FETCH_LOCK_WAIT", "0")
    monkeypatch.setattr(wt_mod, "_sleep", lambda _s: None)
    fetch_side = _worktree_subprocess_side_effect(
        repo,
        fetch_results=[(1, _TRANSIENT_FETCH_STDERR)] * 3,
    )
    with patch.object(wt_mod.subprocess, "run", side_effect=fetch_side):
        violations = gate.check("", [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "base_freshness.fetch_transient_exhausted"
    assert v.auto_fixable is False
    assert v.fix_hint == "Re-run the step"


def test_check_returns_fetch_failed_for_non_transient(tmp_path: Path, monkeypatch):
    repo = tmp_path / "wt"
    _init_git_repo(repo)
    gate = wt.BaseFreshnessGate(repo, "main")
    monkeypatch.setenv("ISSUESMITH_FETCH_LOCK_WAIT", "0")
    fetch_side = _worktree_subprocess_side_effect(
        repo,
        fetch_results=[(1, "fatal: could not read Username")],
    )
    with patch.object(wt_mod.subprocess, "run", side_effect=fetch_side):
        violations = gate.check("", [])
    assert len(violations) == 1
    assert violations[0].rule_id == "base_freshness.fetch_failed"
    assert violations[0].fix_hint == "Manually fetch origin/main"


def test_fetch_transient_exhausted_is_non_repairable():
    assert "base_freshness.fetch_transient_exhausted" in NON_REPAIRABLE_RULE_IDS
    assert is_non_repairable_rule_id("base_freshness.fetch_transient_exhausted")


def test_fix_fast_forward_success_does_not_rebase(tmp_path: Path):
    gate = wt.BaseFreshnessGate(tmp_path, "main")
    with patch.object(wt.subprocess, "run", side_effect=[_proc(0)]) as run:
        gate.fix(MagicMock())
    assert _calls(run) == [["git", "merge", "--ff-only", "origin/main"]]


def test_fix_diverged_branch_rebases_onto_base(tmp_path: Path):
    gate = wt.BaseFreshnessGate(tmp_path, "main")
    with patch("issuesmith.bump_commits.drop_bump_commits_before_rebase", return_value=()):
        with patch.object(
            wt.subprocess,
            "run",
            side_effect=[_proc(128, "fatal: Not possible to fast-forward"), _proc(0)],
        ) as run:
            gate.fix(MagicMock())
    assert _calls(run) == [
        ["git", "merge", "--ff-only", "origin/main"],
        ["git", "rebase", "origin/main"],
    ]


def test_fix_rebase_conflict_aborts_and_raises(tmp_path: Path):
    gate = wt.BaseFreshnessGate(tmp_path, "main")
    seq = [_proc(128, "fatal: Not possible to fast-forward"), _proc(1, "CONFLICT (content): CHANGELOG.md"), _proc(0)]
    with patch("issuesmith.bump_commits.drop_bump_commits_before_rebase", return_value=()):
        with patch.object(wt.subprocess, "run", side_effect=seq) as run:
            with pytest.raises(RuntimeError, match="base_freshness auto-fix failed"):
                gate.fix(MagicMock())
    assert _calls(run)[-1] == ["git", "rebase", "--abort"]


def test_run_pytest_timeout_returns_124(tmp_path: Path):
    def _timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=kwargs.get("timeout"), output=b"collected 10 items\n")

    with patch.object(wt.subprocess, "run", side_effect=_timeout):
        rc, out = wt._run_pytest(tmp_path, ["tests"], timeout=1.0)
    assert rc == 124
    assert out.startswith("pytest timed out after 1 s")
    assert "collected 10 items" in out


def test_pytest_timeout_env_override(monkeypatch):
    monkeypatch.setenv(wt._PYTEST_TIMEOUT_ENV, "42")
    assert wt._pytest_timeout_sec() == 42.0
    monkeypatch.setenv(wt._PYTEST_TIMEOUT_ENV, "not-a-number")
    assert wt._pytest_timeout_sec() == wt._PYTEST_TIMEOUT_DEFAULT_SEC


def _make_bump_behind_base_repo(tmp_path: Path) -> Path:
    from tests.test_p0_worktree import _make_bump_stale_base_repo

    return _make_bump_stale_base_repo(tmp_path)


def test_fix_drops_bump_before_rebase(tmp_path: Path) -> None:
    """#6177: BaseFreshnessGate drops publish bumps before rebasing onto origin/main."""
    from issuesmith.gates.base import ContractInput
    from tests.test_p0_worktree import _git

    repo = _make_bump_behind_base_repo(tmp_path)
    gate = wt.BaseFreshnessGate(repo, "main")
    behind = gate.check("", [])
    assert behind and behind[0].rule_id == "base_freshness.behind_base"
    gate.fix(ContractInput(body=""))
    subjects = _git(repo, "log", "--format=%s", "origin/main..HEAD")
    assert "chore: bump version to" not in subjects
    assert gate.check("", []) == []


def test_fix_bump_drop_failure_aborts_and_raises(tmp_path: Path) -> None:
    from issuesmith.bump_commits import BumpCommitsError
    from issuesmith.gates.base import ContractInput

    repo = _make_bump_behind_base_repo(tmp_path)
    head_before = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    gate = wt.BaseFreshnessGate(repo, "main")
    with patch(
        "issuesmith.bump_commits.drop_bump_commits_before_rebase",
        side_effect=BumpCommitsError("simulated drop failure"),
    ):
        with pytest.raises(RuntimeError, match="bump commit removal failed"):
            gate.fix(ContractInput(body=""))
    head_after = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_after == head_before
