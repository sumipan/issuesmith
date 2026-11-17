"""BaseFreshnessGate.fix falls back to rebase and reports failure; _run_pytest has a timeout."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


def test_fetch_transient_exhausted_is_non_repairable():
    assert "base_freshness.fetch_transient_exhausted" in NON_REPAIRABLE_RULE_IDS
    assert is_non_repairable_rule_id("base_freshness.fetch_transient_exhausted")


def test_fix_fast_forward_success_does_not_rebase(tmp_path: Path):
    gate = wt.BaseFreshnessGate(tmp_path, "main")
    with patch.object(wt.subprocess, "run", side_effect=[_proc(0)]) as run:
        gate.fix(MagicMock())
    assert _calls(run) == [["git", "merge", "--ff-only", "origin/main"]]


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
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(seed)],
        check=True,
        capture_output=True,
    )
    for key, value in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(
            ["git", "-C", str(seed), "config", key, value],
            check=True,
            capture_output=True,
        )
    (seed / "README").write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(seed), "add", "README"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "commit", "-m", "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True, capture_output=True)

    repo = tmp_path / "repo"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True, capture_output=True)
    for key, value in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(
            ["git", "-C", str(repo), "config", key, value],
            check=True,
            capture_output=True,
        )
    subprocess.run(
        ["git", "-C", str(repo), "fetch", "origin", "main:refs/remotes/origin/main"],
        check=True,
        capture_output=True,
    )
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "feat"], check=True, capture_output=True)
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "-C", str(repo), "add", "pyproject.toml"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "chore: bump version to 0.2.0 (Y: B1)"],
        check=True,
        capture_output=True,
    )

    (seed / "README").write_text("v2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(seed), "add", "README"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "commit", "-m", "advance"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(seed), "push", "-q", "origin", "main"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "fetch", "origin", "main:refs/remotes/origin/main"],
        check=True,
        capture_output=True,
    )
    return repo


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
