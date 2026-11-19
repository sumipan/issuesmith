"""Real-git fixtures for rebump_onto_base (#7398)."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import patch

from issuesmith.bump_commits import BUMP_SUBJECT_PREFIX
from issuesmith.verbs.publish import rebump_onto_base


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


_CHANGELOG = (
    "# Changelog\n\n## [Unreleased]\n\n### Added\n\n"
)


def _bump_commit(repo: Path, version: str) -> None:
    _write(repo, "pyproject.toml", f'version = "{version}"\n')
    _git(repo, "add", "pyproject.toml")
    _git(repo, "commit", "-m", f"{BUMP_SUBJECT_PREFIX}{version}")


def _fake_run_version_bump(worktree: Path, base_branch: str) -> subprocess.CompletedProcess[str]:
    text = (worktree / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        return subprocess.CompletedProcess([], 1, "", "no version")
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


def _dual_branch_setup(
    tmp_path: Path,
    *,
    extra_wt2_files: dict[str, str] | None = None,
) -> tuple[Path, Path, Path]:
    """Return (remote, seed/main checkout, second feature worktree)."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(remote), str(seed)], check=True, capture_output=True)
    _config_identity(seed)
    _write(seed, "CHANGELOG.md", _CHANGELOG)
    _bump_commit(seed, "0.1.0")
    _git(seed, "push", "-u", "origin", "main")

    wt2 = tmp_path / "wt2"
    subprocess.run(["git", "clone", str(remote), str(wt2)], check=True, capture_output=True)
    _config_identity(wt2)
    _git(wt2, "checkout", "-b", "feat/two")
    if extra_wt2_files:
        for rel, content in extra_wt2_files.items():
            _write(wt2, rel, content)
    else:
        _write(wt2, "CHANGELOG.md", _CHANGELOG + "- second PR\n")
    _git(wt2, "add", "CHANGELOG.md")
    _git(wt2, "commit", "-m", "changelog second")
    _bump_commit(wt2, "0.1.1")
    _git(wt2, "push", "-u", "origin", "feat/two")

    wt1 = tmp_path / "wt1"
    subprocess.run(["git", "clone", str(remote), str(wt1)], check=True, capture_output=True)
    _config_identity(wt1)
    _git(wt1, "checkout", "-b", "feat/one")
    _write(wt1, "CHANGELOG.md", _CHANGELOG + "- first PR\n")
    _git(wt1, "add", "CHANGELOG.md")
    _git(wt1, "commit", "-m", "changelog first")
    _bump_commit(wt1, "0.1.1")
    _git(wt1, "push", "-u", "origin", "feat/one")

    _git(seed, "fetch", "origin", "feat/one", "main")
    _git(seed, "checkout", "main")
    _git(seed, "reset", "--hard", "origin/feat/one")
    _git(seed, "push", "--force", "origin", "main")

    _git(wt2, "fetch", "origin", "main", "feat/two")
    _git(wt2, "checkout", "feat/two")
    _git(wt2, "reset", "--hard", "origin/feat/two")
    return remote, seed, wt2


def test_rebump_onto_base_resolves_bump_only_conflict(tmp_path: Path) -> None:
    """refs #7398 AC4: second parallel branch rebases and rebumped."""
    _, seed, wt2 = _dual_branch_setup(tmp_path)
    head_before = _git(wt2, "rev-parse", "HEAD").stdout.strip()
    remote_before = _git(wt2, "rev-parse", "origin/feat/two").stdout.strip()

    with patch("issuesmith.verbs.publish.run_version_bump", side_effect=_fake_run_version_bump):
        result = rebump_onto_base(wt2, "main", "feat/two")

    assert result.status == "rebumped", result
    assert "0.1.2" in (wt2 / "pyproject.toml").read_text(encoding="utf-8")
    assert _git(wt2, "merge-base", "--is-ancestor", "origin/main", "HEAD").returncode == 0
    _git(seed, "fetch", "origin", "feat/two", "main")
    merge = _git(seed, "merge", "--ff-only", "origin/feat/two", check=False)
    assert merge.returncode == 0
    assert head_before != _git(wt2, "rev-parse", "HEAD").stdout.strip()
    assert remote_before != _git(wt2, "rev-parse", "refs/remotes/origin/feat/two").stdout.strip()


def test_rebump_onto_base_stops_on_source_conflict(tmp_path: Path) -> None:
    """refs #7398 AC5: non-bump paths are not auto-rebumped."""
    _, seed, wt2 = _dual_branch_setup(tmp_path)
    _write(wt2, "src/x.py", "feat\n")
    _git(wt2, "add", "src/x.py")
    _git(wt2, "commit", "-m", "feat src")
    _git(wt2, "push", "origin", "feat/two")

    _write(seed, "src/x.py", "main\n")
    _git(seed, "add", "src/x.py")
    _git(seed, "commit", "-m", "main src")
    _git(seed, "push", "origin", "main")
    _git(wt2, "fetch", "origin", "main")

    remote_sha = _git(wt2, "rev-parse", "refs/remotes/origin/feat/two").stdout.strip()
    main_sha = _git(wt2, "rev-parse", "origin/main").stdout.strip()

    result = rebump_onto_base(wt2, "main", "feat/two")
    assert result.status == "not_bump_only"
    assert "src/x.py" in result.conflict_files
    assert _git(wt2, "rev-parse", "refs/remotes/origin/feat/two").stdout.strip() == remote_sha
    assert _git(wt2, "rev-parse", "origin/main").stdout.strip() == main_sha


def test_rebump_onto_base_stops_when_pyproject_changes_beyond_version(tmp_path: Path) -> None:
    """refs #7398 AC6: dependency edits outside version line block rebump."""
    dep_extra = 'dependencies = ["foo>=1"]\n'
    _, _, wt2 = _dual_branch_setup(tmp_path)
    text = (wt2 / "pyproject.toml").read_text(encoding="utf-8") + dep_extra
    (wt2 / "pyproject.toml").write_text(text, encoding="utf-8")
    _git(wt2, "add", "pyproject.toml")
    _git(wt2, "commit", "-m", "pin dep")
    _git(wt2, "push", "origin", "feat/two")
    remote_sha = _git(wt2, "rev-parse", "refs/remotes/origin/feat/two").stdout.strip()
    _git(wt2, "fetch", "origin", "main")

    result = rebump_onto_base(wt2, "main", "feat/two")
    assert result.status == "not_bump_only"
    assert result.detail == "pyproject.toml changes beyond the version line"
    assert _git(wt2, "rev-parse", "refs/remotes/origin/feat/two").stdout.strip() == remote_sha


def test_rebump_onto_base_refuses_push_when_non_bump_diff_changes(tmp_path: Path) -> None:
    """refs #7398 AC7: fingerprint mismatch rolls back before push."""
    _, _, wt2 = _dual_branch_setup(tmp_path)
    head_before = _git(wt2, "rev-parse", "HEAD").stdout.strip()
    remote_sha = _git(wt2, "rev-parse", "refs/remotes/origin/feat/two").stdout.strip()

    def _mischief_bump(worktree: Path, base_branch: str) -> subprocess.CompletedProcess[str]:
        proc = _fake_run_version_bump(worktree, base_branch)
        if proc.returncode != 0:
            return proc
        _write(worktree, "src/y.py", "extra\n")
        _git(worktree, "add", "src/y.py")
        _git(worktree, "commit", "-m", "sneaky")
        return proc

    with patch("issuesmith.verbs.publish.run_version_bump", side_effect=_mischief_bump):
        result = rebump_onto_base(wt2, "main", "feat/two")

    assert result.status == "failed"
    assert result.detail == "non-bump diff changed after rebase"
    assert _git(wt2, "rev-parse", "HEAD").stdout.strip() == head_before
    assert _git(wt2, "rev-parse", "refs/remotes/origin/feat/two").stdout.strip() == remote_sha
