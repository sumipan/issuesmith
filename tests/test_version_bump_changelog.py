"""version_bump folds CHANGELOG.md Unreleased into the bumped version section (#5042)."""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from issuesmith.ops.version_bump import fold_unreleased, run_bump

_TODAY = date(2026, 10, 9)

_GHDAG = """# Changelog

## [Unreleased]

- feat: add alpha
- fix: repair beta
- docs: note gamma

## 0.71.0 - 2026-09-24

- feat: old entry
"""

_MLTGNT = """# Changelog

## Unreleased

- feat: add alpha

## v0.33.0

- feat: old entry
"""


def _without_inserted(out: str, inserted: list[str]) -> str:
    lines = out.splitlines(keepends=True)
    i = lines.index(inserted[1])
    assert lines[i - 1 : i + 1] == inserted
    del lines[i - 1 : i + 1]
    return "".join(lines)


def test_ghdag_format_inserts_dated_heading_before_body():
    out = fold_unreleased(_GHDAG, "0.72.0", _TODAY)

    assert out is not None
    lines = out.splitlines()
    i = lines.index("## [Unreleased]")
    following = [line for line in lines[i + 1 :] if line.strip()]
    assert following[0] == "## 0.72.0 - 2026-10-09"
    assert following[1:4] == [
        "- feat: add alpha",
        "- fix: repair beta",
        "- docs: note gamma",
    ]
    assert following[4] == "## 0.71.0 - 2026-09-24"


def test_ghdag_format_keeps_original_lines_in_order():
    out = fold_unreleased(_GHDAG, "0.72.0", _TODAY)

    assert out is not None
    assert _without_inserted(out, ["\n", "## 0.72.0 - 2026-10-09\n"]) == _GHDAG
    assert len(out.splitlines()) == len(_GHDAG.splitlines()) + 2


def test_mltgnt_format_uses_v_prefix_without_date():
    out = fold_unreleased(_MLTGNT, "0.34.0", _TODAY)

    assert out is not None
    lines = out.splitlines()
    i = lines.index("## Unreleased")
    assert [line for line in lines[i + 1 :] if line.strip()][0] == "## v0.34.0"
    assert _without_inserted(out, ["\n", "## v0.34.0\n"]) == _MLTGNT


def test_issuesmith_format_plain_heading_with_date():
    text = "## Unreleased\n\n- fix: x\n\n## 0.62.0 - 2026-09-25\n\n- old\n"

    out = fold_unreleased(text, "0.62.1", _TODAY)

    assert out == (
        "## Unreleased\n\n## 0.62.1 - 2026-10-09\n\n- fix: x\n\n"
        "## 0.62.0 - 2026-09-25\n\n- old\n"
    )


def test_no_existing_version_heading_defaults_to_dated_plain():
    text = "# Changelog\n\n## Unreleased\n\n- feat: first\n"

    out = fold_unreleased(text, "0.1.1", _TODAY)

    assert out == "# Changelog\n\n## Unreleased\n\n## 0.1.1 - 2026-10-09\n\n- feat: first\n"


@pytest.mark.parametrize(
    "text",
    [
        "# Changelog\n\n## Unreleased\n\n   \n\n## 0.1.0 - 2026-01-01\n\n- x\n",
        "# Changelog\n\n## [Unreleased]\n",
        "# Changelog\n\n## 0.1.0 - 2026-01-01\n\n- x\n",
        "",
    ],
)
def test_empty_or_missing_unreleased_returns_none(text: str):
    assert fold_unreleased(text, "0.1.1", _TODAY) is None


def test_breaking_subsection_moves_into_new_section():
    text = (
        "## Unreleased\n\n### Breaking\n\n- feat!: drop old API\n\n"
        "### Added\n\n- feat: new\n\n## 0.5.0 - 2026-01-01\n"
    )

    out = fold_unreleased(text, "0.6.0", _TODAY)

    assert out == (
        "## Unreleased\n\n## 0.6.0 - 2026-10-09\n\n### Breaking\n\n- feat!: drop old API\n\n"
        "### Added\n\n- feat: new\n\n## 0.5.0 - 2026-01-01\n"
    )


# --- run_bump integration ---------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _init_repo(tmp_path: Path, changelog: str | None) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "pyproject.toml").write_text('version = "0.71.0"\n', encoding="utf-8")
    if changelog is not None:
        (repo / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "chore: init")
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    (repo / "notes.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "notes.txt")
    _git(repo, "commit", "-q", "-m", "fix: tweak")
    return repo


def _head_files(repo: Path) -> list[str]:
    out = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    return sorted(out.split())


def test_run_bump_includes_folded_changelog_in_bump_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    repo = _init_repo(tmp_path, _GHDAG)

    assert run_bump(repo, "origin/main") == 0

    folded_lines = [
        line for line in capsys.readouterr().out.splitlines()
        if line.startswith("CHANGELOG_FOLDED:")
    ]
    assert folded_lines == ["CHANGELOG_FOLDED: 0.71.1"]

    assert _head_files(repo) == ["CHANGELOG.md", "pyproject.toml"]
    numstat = _git(repo, "diff", "--numstat", "HEAD~1", "HEAD", "--", "CHANGELOG.md")
    added, deleted, _ = numstat.split("\t")
    assert (added, deleted) == ("2", "0")
    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## 0.71.1 - {date.today().isoformat()}" in text


def test_run_bump_leaves_changelog_without_unreleased_body_untouched(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    original = "# Changelog\n\n## Unreleased\n\n## 0.71.0 - 2026-09-24\n\n- old\n"
    repo = _init_repo(tmp_path, original)

    assert run_bump(repo, "origin/main") == 0

    assert "CHANGELOG_FOLDED:" not in capsys.readouterr().out

    assert _head_files(repo) == ["pyproject.toml"]
    assert (repo / "CHANGELOG.md").read_bytes() == original.encode("utf-8")


@pytest.mark.parametrize(
    "changelog",
    [None, "# Changelog\n\n## 0.71.0 - 2026-09-24\n\n- old\n"],
    ids=["no-changelog", "no-unreleased-heading"],
)
def test_run_bump_without_fold_prints_no_changelog_folded(
    tmp_path: Path, changelog: str | None, capsys: pytest.CaptureFixture[str]
):
    repo = _init_repo(tmp_path, changelog)

    assert run_bump(repo, "origin/main") == 0

    out = capsys.readouterr().out
    assert "pyproject.toml: Z-bumped 0.71.0 → 0.71.1" in out
    assert "CHANGELOG_FOLDED:" not in out
    assert _head_files(repo) == ["pyproject.toml"]
