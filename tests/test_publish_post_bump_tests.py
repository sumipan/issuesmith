"""publish re-runs CHANGELOG-reading tests after a bump folded CHANGELOG.md (#5128)."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from issuesmith.ops.publish import _post_bump_tests, publish
from issuesmith.ops.version_bump import run_bump

_CHANGELOG = """# Changelog

## [Unreleased]

- feat: add alpha

## 0.71.0 - 2026-09-24

- feat: old entry
"""

# Reads only the Unreleased section, like ghdag tests/conventions/test_port_compat.py.
_FAILING_TEST = '''from pathlib import Path


def test_unreleased_lists_alpha():
    text = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text()
    unreleased = text.split("## [Unreleased]", 1)[1].split("\\n## ", 1)[0]
    assert "alpha" in unreleased
'''

_PASSING_TEST = '''from pathlib import Path


def test_changelog_mentions_alpha():
    text = (Path(__file__).resolve().parents[1] / "CHANGELOG.md").read_text()
    assert "alpha" in text
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    ).stdout


def make_folded_repo(
    tmp_path: Path, test_body: str | None, changelog: str | None = _CHANGELOG
) -> Path:
    """origin/main + feature commit + a real publish bump commit (folds CHANGELOG.md)."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "pyproject.toml").write_text('version = "0.71.0"\n', encoding="utf-8")
    if changelog is not None:
        (repo / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    if test_body is not None:
        (repo / "tests" / "test_changelog.py").write_text(test_body, encoding="utf-8")
    (repo / "tests" / "test_other.py").write_text(
        "def test_other():\n    assert True\n", encoding="utf-8"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "chore: init")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / "notes.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "notes.txt")
    _git(repo, "commit", "-q", "-m", "fix: tweak")
    assert run_bump(repo, "origin/main") == 0
    return repo


def bump_commit_count(repo: Path) -> int:
    log = _git(repo, "log", "--format=%s", "origin/main..HEAD").splitlines()
    return sum(1 for s in log if s.startswith("chore: bump version to "))


def run_publish(repo: Path):
    """publish() against a real Git repo; push and forge are mocked."""
    client = MagicMock()
    client.pr_list.return_value = []
    client.pr_create.return_value = "https://example.test/pr/1"
    with patch("issuesmith.ops.publish._commit_if_needed"), \
         patch("issuesmith.ops.publish._ensure_rebased", return_value=None), \
         patch("issuesmith.ops.publish._check_commit_diff_gates", return_value=None), \
         patch("issuesmith.ops.publish._run_version_bump") as run_version_bump, \
         patch("issuesmith.ops.publish._push_branch", return_value=None) as push, \
         patch("issuesmith.ops.publish.get_forge", return_value=client):
        result = publish(
            issue_number=5128, branch="feat/issue-5128", base_branch="main",
            worktree=repo, repo="sumipan/ghdag", issue_repo="sumipan/nexus",
        )
    return result, push, client, run_version_bump


def test_publish_rejects_when_changelog_test_fails_after_fold(tmp_path: Path):
    repo = make_folded_repo(tmp_path, _FAILING_TEST)

    result, push, client, _ = run_publish(repo)

    assert result.status == "P3_GATE_FAILED"
    assert result.exit_code == 1
    assert result.stderr.startswith("[publish.post_bump_tests]")
    assert "tests/test_changelog.py" in result.stderr
    assert "tests/test_other.py" not in result.stderr
    push.assert_not_called()
    client.pr_list.assert_not_called()
    client.pr_create.assert_not_called()


def test_publish_runs_only_changelog_tests_then_continues(tmp_path: Path):
    repo = make_folded_repo(tmp_path, _PASSING_TEST)
    real_run = subprocess.run
    pytest_calls: list[tuple[list[str], dict]] = []

    def _spy(cmd, *args, **kwargs):
        if cmd[:3] == [sys.executable, "-m", "pytest"]:
            pytest_calls.append((cmd, kwargs))
        return real_run(cmd, *args, **kwargs)

    with patch("issuesmith.ops.publish.subprocess.run", side_effect=_spy):
        result, push, client, _ = run_publish(repo)

    assert result.status == "OK"
    assert len(pytest_calls) == 1
    cmd, kwargs = pytest_calls[0]
    assert cmd == [
        sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "tests/test_changelog.py",
    ]
    assert kwargs["cwd"] == repo
    assert kwargs["timeout"] == 600
    push.assert_called_once()
    client.pr_create.assert_called_once()


def test_no_fold_bump_skips_pytest(tmp_path: Path):
    empty = "# Changelog\n\n## [Unreleased]\n\n## 0.71.0 - 2026-09-24\n\n- old\n"
    repo = make_folded_repo(tmp_path, _FAILING_TEST, changelog=empty)
    with patch("issuesmith.ops.publish.subprocess.run", wraps=subprocess.run) as run:
        assert _post_bump_tests(repo, "main") is None
    assert not any("pytest" in c.args[0] for c in run.call_args_list)


def test_no_changelog_skips_pytest(tmp_path: Path):
    repo = make_folded_repo(tmp_path, _FAILING_TEST, changelog=None)
    with patch("issuesmith.ops.publish.subprocess.run", wraps=subprocess.run) as run:
        assert _post_bump_tests(repo, "main") is None
    assert not any("pytest" in c.args[0] for c in run.call_args_list)


def test_no_bump_commit_in_range_skips_pytest():
    """same-repo publish / no pyproject.toml: no bump commit, no pytest."""
    log = MagicMock(returncode=0, stdout="abc123 fix: x\n", stderr="")
    with patch("issuesmith.ops.publish._run_git", return_value=log) as git, \
         patch("issuesmith.ops.publish.subprocess.run") as run:
        assert _post_bump_tests(Path("/tmp/wt"), "main") is None
    assert git.call_count == 1
    run.assert_not_called()


def _git_router(*, log_rc=0, tree_rc=0, grep_rc=0, grep_out="tests/test_changelog.py\n"):
    def _run(worktree, *args, check=True):
        if args[0] == "log":
            return MagicMock(
                returncode=log_rc, stdout="abc123 chore: bump version to 0.71.1 (Z: Z)\n",
                stderr="fatal: bad revision" if log_rc else "",
            )
        if args[0] == "diff-tree":
            return MagicMock(
                returncode=tree_rc, stdout="CHANGELOG.md\npyproject.toml\n",
                stderr="fatal: bad object" if tree_rc else "",
            )
        if args[0] == "grep":
            return MagicMock(
                returncode=grep_rc, stdout=grep_out if grep_rc == 0 else "",
                stderr="fatal: grep broke" if grep_rc > 1 else "",
            )
        raise AssertionError(args)

    return _run


def test_git_grep_no_match_skips_pytest():
    with patch("issuesmith.ops.publish._run_git", side_effect=_git_router(grep_rc=1)), \
         patch("issuesmith.ops.publish.subprocess.run") as run:
        assert _post_bump_tests(Path("/tmp/wt"), "main") is None
    run.assert_not_called()


def test_git_grep_error_fails_closed():
    with patch("issuesmith.ops.publish._run_git", side_effect=_git_router(grep_rc=2)), \
         patch("issuesmith.ops.publish.subprocess.run") as run:
        result = _post_bump_tests(Path("/tmp/wt"), "main")
    assert result is not None and result.status == "P3_GATE_FAILED"
    assert result.stderr.startswith("[publish.post_bump_tests]")
    run.assert_not_called()


def test_history_error_fails_closed():
    for router in (_git_router(log_rc=128), _git_router(tree_rc=128)):
        with patch("issuesmith.ops.publish._run_git", side_effect=router), \
             patch("issuesmith.ops.publish.subprocess.run") as run:
            result = _post_bump_tests(Path("/tmp/wt"), "main")
        assert result is not None and result.status == "P3_GATE_FAILED"
        assert result.exit_code == 1
        assert result.stderr.startswith("[publish.post_bump_tests]")
        run.assert_not_called()


def test_publish_does_not_push_on_history_error(tmp_path: Path):
    with patch("issuesmith.ops.publish._run_git", side_effect=_git_router(log_rc=128)), \
         patch("issuesmith.ops.publish._maybe_bump_version", return_value=None):
        result, push, client, _ = run_publish(tmp_path)
    assert result.status == "P3_GATE_FAILED"
    push.assert_not_called()
    client.pr_create.assert_not_called()


def test_pytest_failure_includes_only_last_40_lines():
    out = "\n".join(f"out-{i}" for i in range(100))
    err = "\n".join(f"err-{i}" for i in range(30))
    proc = subprocess.CompletedProcess(args=[], returncode=1, stdout=out, stderr=err)
    with patch("issuesmith.ops.publish._run_git", side_effect=_git_router()), \
         patch("issuesmith.ops.publish.subprocess.run", return_value=proc):
        result = _post_bump_tests(Path("/tmp/wt"), "main")
    assert result is not None and result.status == "P3_GATE_FAILED"
    assert "tests/test_changelog.py" in result.stderr
    assert "err-29" in result.stderr and "err-0" in result.stderr
    assert "out-99" in result.stderr and "out-90" in result.stderr
    assert "out-89" not in result.stderr
    assert "out-0\n" not in result.stderr


def test_pytest_timeout_fails_closed():
    exc = subprocess.TimeoutExpired(cmd=["pytest"], timeout=600, output="slow\n", stderr="")
    with patch("issuesmith.ops.publish._run_git", side_effect=_git_router()), \
         patch("issuesmith.ops.publish.subprocess.run", side_effect=exc):
        result = _post_bump_tests(Path("/tmp/wt"), "main")
    assert result is not None and result.status == "P3_GATE_FAILED"
    assert result.stderr.startswith("[publish.post_bump_tests]")
    assert "timeout" in result.stderr
    assert "tests/test_changelog.py" in result.stderr
