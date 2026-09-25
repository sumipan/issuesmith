"""Real-git fixtures for CHANGELOG.md-only rebase conflict auto-resolution (#3587).

Parallel issuesmith Issues all append to CHANGELOG.md, so a branch cut before
other PRs merged conflicts only on CHANGELOG.md at publish time.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from issuesmith.ops.publish import _ensure_rebased, _resolve_changelog_conflicts

_ALLOW = ["src/issuesmith/ops/publish.py", "CHANGELOG.md"]
_BASE_CHANGELOG = "# Changelog\n\n## 0.43.0 - 2026-09-20\n- old entry\n"


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


def _commit(repo: Path, message: str, files: dict[str, str]) -> None:
    for rel, content in files.items():
        _write(repo, rel, content)
    _git(repo, "add", "--", *files)
    _git(repo, "commit", "-m", message)


def _changelog_with(entry: str) -> str:
    return _BASE_CHANGELOG.replace("# Changelog\n\n", f"# Changelog\n\n{entry}\n", 1)


def _setup(
    tmp_path: Path,
    feat_commits: list[dict[str, str]],
    origin_files: dict[str, str],
) -> tuple[Path, Path]:
    """bare origin + feature clone whose commits diverge from origin/main."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(remote), str(seed)], check=True, capture_output=True)
    _config_identity(seed)
    _commit(
        seed,
        "init",
        {"CHANGELOG.md": _BASE_CHANGELOG, "src/issuesmith/ops/publish.py": "publish-v1\n"},
    )
    _git(seed, "branch", "-M", "main")
    _git(seed, "push", "-u", "origin", "main")

    wt = tmp_path / "wt"
    subprocess.run(["git", "clone", str(remote), str(wt)], check=True, capture_output=True)
    _config_identity(wt)
    _git(wt, "checkout", "-b", "feat/issue-3587")
    for i, files in enumerate(feat_commits):
        _commit(wt, f"feat change {i}", files)

    _commit(seed, "origin advances", origin_files)
    _git(seed, "push", "origin", "main")
    return wt, seed


def _assert_rebase_aborted(wt: Path, pre_head: str) -> None:
    assert _git(wt, "rev-parse", "HEAD").stdout.strip() == pre_head
    assert not (wt / ".git" / "rebase-merge").exists()
    assert not (wt / ".git" / "rebase-apply").exists()
    assert _git(wt, "status", "--porcelain").stdout == ""


def test_changelog_only_conflict_is_auto_resolved(tmp_path: Path) -> None:
    wt, _ = _setup(
        tmp_path,
        feat_commits=[
            {
                "CHANGELOG.md": _changelog_with("## 0.44.0 - 2026-09-22\n- our entry\n"),
                "src/issuesmith/ops/publish.py": "publish-v1-feat\n",
            }
        ],
        origin_files={
            "CHANGELOG.md": _changelog_with("## 0.50.0 - 2026-09-22\n- other PR's entry\n"),
        },
    )

    result = _ensure_rebased(wt, "main", _ALLOW)

    assert result is None
    ancestor = _git(wt, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False)
    assert ancestor.returncode == 0
    assert not (wt / ".git" / "rebase-merge").exists()
    assert not (wt / ".git" / "rebase-apply").exists()
    changelog = (wt / "CHANGELOG.md").read_text(encoding="utf-8")
    for marker in ("<<<<<<< ", "=======", ">>>>>>> "):
        assert marker not in changelog
    assert "- other PR's entry" in changelog
    assert "- our entry" in changelog
    assert "- old entry" in changelog
    assert (wt / "src/issuesmith/ops/publish.py").read_text(encoding="utf-8") == (
        "publish-v1-feat\n"
    )
    assert _git(wt, "status", "--porcelain").stdout == ""


def test_changelog_conflicts_across_multiple_commits_are_resolved(tmp_path: Path) -> None:
    first = _changelog_with("## 0.44.0 - 2026-09-22\n- our entry\n")
    wt, _ = _setup(
        tmp_path,
        feat_commits=[
            {"CHANGELOG.md": first},
            {"CHANGELOG.md": first.replace("- our entry\n", "- our entry\n- our follow-up\n")},
        ],
        origin_files={
            "CHANGELOG.md": _changelog_with("## 0.50.0 - 2026-09-22\n- other PR's entry\n"),
        },
    )

    assert _ensure_rebased(wt, "main", _ALLOW) is None

    changelog = (wt / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "<<<<<<< " not in changelog
    assert "- other PR's entry" in changelog
    assert "- our follow-up" in changelog
    ancestor = _git(wt, "merge-base", "--is-ancestor", "origin/main", "HEAD", check=False)
    assert ancestor.returncode == 0


def test_non_changelog_conflict_returns_rebase_conflict(tmp_path: Path) -> None:
    wt, _ = _setup(
        tmp_path,
        feat_commits=[{"src/issuesmith/ops/publish.py": "publish-v1-feat\n"}],
        origin_files={"src/issuesmith/ops/publish.py": "publish-origin-conflict\n"},
    )
    pre_head = _git(wt, "rev-parse", "HEAD").stdout.strip()

    result = _ensure_rebased(wt, "main", _ALLOW)

    assert result is not None
    assert result.status == "REBASE_CONFLICT"
    assert result.exit_code == 1
    assert "src/issuesmith/ops/publish.py" in result.stderr
    _assert_rebase_aborted(wt, pre_head)


def test_mixed_changelog_and_source_conflict_is_not_partially_resolved(tmp_path: Path) -> None:
    wt, _ = _setup(
        tmp_path,
        feat_commits=[
            {
                "CHANGELOG.md": _changelog_with("## 0.44.0 - 2026-09-22\n- our entry\n"),
                "src/issuesmith/ops/publish.py": "publish-v1-feat\n",
            }
        ],
        origin_files={
            "CHANGELOG.md": _changelog_with("## 0.50.0 - 2026-09-22\n- other PR's entry\n"),
            "src/issuesmith/ops/publish.py": "publish-origin-conflict\n",
        },
    )
    pre_head = _git(wt, "rev-parse", "HEAD").stdout.strip()

    result = _ensure_rebased(wt, "main", _ALLOW)

    assert result is not None
    assert result.status == "REBASE_CONFLICT"
    assert "CHANGELOG.md" in result.stderr
    assert "src/issuesmith/ops/publish.py" in result.stderr
    _assert_rebase_aborted(wt, pre_head)
    changelog = (wt / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "- other PR's entry" not in changelog


def test_resolve_changelog_conflicts_keeps_both_sides(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "<<<<<<< HEAD\n"
        "## 0.50.0 - 2026-09-22\n"
        "- other PR's entry\n"
        "\n"
        "=======\n"
        "## 0.44.0 - 2026-09-22\n"
        "- our entry\n"
        "\n"
        ">>>>>>> abc123 (our commit)\n"
        "## 0.43.0 - 2026-09-20\n"
        "- old entry\n",
        encoding="utf-8",
    )

    assert _resolve_changelog_conflicts(path) is True
    assert path.read_text(encoding="utf-8") == (
        "## 0.50.0 - 2026-09-22\n"
        "- other PR's entry\n"
        "\n"
        "## 0.44.0 - 2026-09-22\n"
        "- our entry\n"
        "\n"
        "## 0.43.0 - 2026-09-20\n"
        "- old entry\n"
    )


def test_resolve_changelog_conflicts_without_markers_returns_false(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(_BASE_CHANGELOG, encoding="utf-8")

    assert _resolve_changelog_conflicts(path) is False
    assert path.read_text(encoding="utf-8") == _BASE_CHANGELOG


def test_resolve_changelog_conflicts_missing_file_returns_false(tmp_path: Path) -> None:
    assert _resolve_changelog_conflicts(tmp_path / "CHANGELOG.md") is False


def test_changelog_conflict_without_markers_falls_back_to_rebase_conflict(
    tmp_path: Path, monkeypatch
) -> None:
    wt, _ = _setup(
        tmp_path,
        feat_commits=[{"CHANGELOG.md": _changelog_with("## 0.44.0 - 2026-09-22\n- our entry\n")}],
        origin_files={
            "CHANGELOG.md": _changelog_with("## 0.50.0 - 2026-09-22\n- other PR's entry\n"),
        },
    )
    monkeypatch.setattr(
        "issuesmith.ops.publish._resolve_changelog_conflicts", lambda path: False
    )
    pre_head = _git(wt, "rev-parse", "HEAD").stdout.strip()

    result = _ensure_rebased(wt, "main", _ALLOW)

    assert result is not None
    assert result.status == "REBASE_CONFLICT"
    assert result.stderr == "CHANGELOG.md"
    _assert_rebase_aborted(wt, pre_head)


def test_resolve_changelog_conflicts_drops_diff3_base_section(tmp_path: Path) -> None:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "<<<<<<< HEAD\n"
        "- theirs\n"
        "||||||| base\n"
        "- base\n"
        "=======\n"
        "- ours\n"
        ">>>>>>> abc123 (our commit)\n"
        "- old entry\n",
        encoding="utf-8",
    )

    assert _resolve_changelog_conflicts(path) is True
    assert path.read_text(encoding="utf-8") == "- theirs\n- ours\n- old entry\n"
