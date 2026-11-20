"""version_bump folds CHANGELOG.md Unreleased into the bumped version section (#5042, #7433)."""
from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from issuesmith.ops.version_bump import (
    _has_cjk,
    changelog_entries,
    fold_unreleased,
    run_bump,
)

_CJK_WORD = chr(0x65E5) + chr(0x672C)
_ASCII_CHANGELOG = (
    "# Changelog\n\n## Unreleased\n\n## 0.71.0 - 2026-09-24\n\n- old\n"
)

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


# --- AC tests (#7433) -------------------------------------------------------


def test_changelog_entries_maps_types_to_sections():
    """Refs: #7433 AC4"""
    assert changelog_entries(
        [
            "feat(x): a (#1)",
            "fix!: b",
            "docs: c",
            "test: d",
            "wip(9): e",
            "plain subject",
            "feat(x): a (#1)",
        ]
    ) == {
        "Added": ["x: a (#1)"],
        "Changed": ["c", "plain subject"],
        "Fixed": ["**Breaking:** b"],
    }


def test_fold_unreleased_merges_into_existing_subsection_and_dedupes():
    """Refs: #7433 AC5"""
    text = "# Changelog\n\n## Unreleased\n\n### Fixed\n\n- Repair beta (#5).\n\n## 0.1.0\n"
    out = fold_unreleased(
        text,
        "0.1.1",
        _TODAY,
        entries={"Fixed": ["repair beta (#5)", "repair gamma"]},
    )

    assert out is not None
    fixed_blocks = out.split("### Fixed\n", 1)[1].split("## ", 1)[0]
    assert fixed_blocks.count("### Fixed") == 0
    assert "- Repair beta (#5)." in fixed_blocks
    assert "- repair gamma" in fixed_blocks
    assert fixed_blocks.count("- repair beta (#5)") == 0


# --- run_bump integration ---------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _init_repo(
    tmp_path: Path, changelog: str | None, extra_commits: list[str] | None = None
) -> Path:
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
    for subject in ["fix: tweak"] if extra_commits is None else extra_commits:
        (repo / "notes.txt").write_text(f"{subject}\n", encoding="utf-8")
        _git(repo, "add", "notes.txt")
        _git(repo, "commit", "-q", "-m", subject)
    return repo


def _head_files(repo: Path) -> list[str]:
    out = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    return sorted(out.split())


def test_run_bump_builds_section_from_commits_when_unreleased_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """Refs: #7433 AC1"""
    changelog = "# Changelog\n\n## Unreleased\n\n## 0.71.0 - 2026-09-24\n\n- old\n"
    repo = _init_repo(
        tmp_path,
        changelog,
        extra_commits=[
            "feat(engine): add alpha (#10)",
            "fix: repair beta",
        ],
    )

    assert run_bump(repo, "origin/main") == 0

    folded_lines = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("CHANGELOG_FOLDED:")
    ]
    assert folded_lines == ["CHANGELOG_FOLDED: 0.71.1"]
    assert _head_files(repo) == ["CHANGELOG.md", "pyproject.toml"]

    today = date.today().isoformat()
    expected = (
        f"# Changelog\n\n## Unreleased\n\n## 0.71.1 - {today}\n\n"
        "### Added\n\n- engine: add alpha (#10)\n\n"
        "### Fixed\n\n- repair beta\n\n"
        "## 0.71.0 - 2026-09-24\n\n- old\n"
    )
    assert (repo / "CHANGELOG.md").read_text(encoding="utf-8") == expected


def test_run_bump_excludes_bump_and_merge_commits(tmp_path: Path):
    """Refs: #7433 AC2"""
    repo = _init_repo(
        tmp_path,
        "# Changelog\n\n## Unreleased\n\n## 0.71.0\n",
        extra_commits=[],
    )
    _git(repo, "checkout", "-q", "-b", "side")
    _git(repo, "commit", "--allow-empty", "-q", "-m", "fix: side fix")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--no-ff", "-q", "side", "-m", "Merge branch 'side'")
    assert _git(repo, "log", "--merges", "--format=%s", "origin/main..HEAD").strip() == (
        "Merge branch 'side'"
    )
    _git(
        repo,
        "commit",
        "--allow-empty",
        "-q",
        "-m",
        "chore: bump version to 0.71.1 (Z: Z — no public API / breaking change detected)",
    )

    assert run_bump(repo, "origin/main") == 0

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "- side fix" in text
    assert "bump version" not in text
    assert "Merge branch" not in text


def test_run_bump_keeps_unreleased_text_in_release_section(tmp_path: Path):
    """Refs: #7433 AC3"""
    changelog = (
        "# Changelog\n\n## Unreleased\n\n- Hand-written note.\n\n## 0.71.0\n"
    )
    repo = _init_repo(tmp_path, changelog, extra_commits=["fix: tweak"])

    assert run_bump(repo, "origin/main") == 0

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "- Hand-written note." in text
    assert "### Fixed" in text
    assert "- tweak" in text
    assert text.index("- Hand-written note.") < text.index("### Fixed")

    numstat = _git(repo, "diff", "--numstat", "HEAD~1", "HEAD", "--", "CHANGELOG.md")
    added, deleted, _ = numstat.split("\t", 2)
    assert deleted == "0"
    assert int(added) > 0


def test_run_bump_without_changelog_commits_only_pyproject(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """Refs: #7433 AC6"""
    repo = _init_repo(tmp_path, changelog=None, extra_commits=["fix: tweak"])

    assert run_bump(repo, "origin/main") == 0

    assert "CHANGELOG_FOLDED:" not in capsys.readouterr().out
    assert _head_files(repo) == ["pyproject.toml"]
    assert not (repo / "CHANGELOG.md").exists()


# --- CJK commit subjects (#7471) --------------------------------------------


def test_run_bump_drops_cjk_subjects_when_changelog_has_no_cjk(
    tmp_path: Path,
):
    """Refs: #7471 AC1"""
    repo = _init_repo(
        tmp_path,
        _ASCII_CHANGELOG,
        extra_commits=[
            "feat: add alpha",
            f"fix: {_CJK_WORD}",
        ],
    )

    assert run_bump(repo, "origin/main") == 0

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "- add alpha" in text
    assert _CJK_WORD not in text
    assert not any(_has_cjk(line) for line in text.splitlines() if line.strip())


def test_run_bump_prints_each_skipped_subject(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """Refs: #7471 AC2"""
    repo = _init_repo(
        tmp_path,
        _ASCII_CHANGELOG,
        extra_commits=[
            f"fix: {_CJK_WORD} one",
            "feat: add alpha",
            f"fix: {_CJK_WORD} two",
        ],
    )

    assert run_bump(repo, "origin/main") == 0

    skipped = [
        line
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("CHANGELOG_SKIPPED_SUBJECT:")
    ]
    assert skipped == [
        f"CHANGELOG_SKIPPED_SUBJECT: fix: {_CJK_WORD} one",
        f"CHANGELOG_SKIPPED_SUBJECT: fix: {_CJK_WORD} two",
    ]


def test_run_bump_all_subjects_skipped_returns_zero(tmp_path: Path):
    """Refs: #7471 AC3"""
    repo = _init_repo(
        tmp_path,
        _ASCII_CHANGELOG,
        extra_commits=[f"fix: {_CJK_WORD}"],
    )

    assert run_bump(repo, "origin/main") == 0

    assert (
        (repo / "CHANGELOG.md").read_text(encoding="utf-8") == _ASCII_CHANGELOG
    )
    assert _head_files(repo) == ["pyproject.toml"]
    assert not any(_has_cjk(line) for line in _ASCII_CHANGELOG.splitlines())


def test_run_bump_keeps_cjk_subjects_when_changelog_has_cjk(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    """Refs: #7471 AC4"""
    changelog = (
        "# Changelog\n\n## Unreleased\n\n"
        f"## 0.71.0 - 2026-09-24\n\n- {_CJK_WORD}\n"
    )
    repo = _init_repo(
        tmp_path,
        changelog,
        extra_commits=[f"fix: {_CJK_WORD}"],
    )

    assert run_bump(repo, "origin/main") == 0

    text = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    fixed_blocks = text.split("### Fixed\n", 1)[1].split("## ", 1)[0]
    assert f"- {_CJK_WORD}" in fixed_blocks
    out = capsys.readouterr().out
    assert "CHANGELOG_SKIPPED_SUBJECT:" not in out
