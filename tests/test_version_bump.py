"""version_bump B1 detection: Conventional Commits only, bump commits excluded (#4508)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from issuesmith.ops.version_bump import decide_bump, run_bump, sync_readme_version

_STALE_BUMP_SUBJECT = (
    "chore: bump version to 0.2.0 (Y: B1 — commit message indicates breaking change)"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _commit(repo: Path, rel: str, content: str, message: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _git(repo, "add", rel)
    _git(repo, "commit", "-q", "-m", message)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _git(repo, "config", "commit.gpgsign", "false")
    _commit(repo, "pyproject.toml", 'version = "0.1.0"\n', "chore: init")
    _commit(repo, "src/pkg/mod.py", "def api():\n    return 1\n", "feat: add api")
    return repo


def _point_origin_main_at_head(repo: Path) -> None:
    _git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")




def test_bump_commit_subject_alone_is_not_b1(tmp_path: Path):
    repo = _init_repo(tmp_path)
    _point_origin_main_at_head(repo)
    _commit(repo, "pyproject.toml", 'version = "0.2.0"\n', _STALE_BUMP_SUBJECT)
    _commit(repo, "src/pkg/mod.py", "def api():\n    return 3\n", "fix: tweak internals")

    decision = decide_bump(repo, "origin/main")

    assert decision.bump_type == "Z", decision.reason


@pytest.mark.parametrize(
    "message",
    [
        "feat!: add feature",
        "fix!: handle edge case",
        "chore(scope)!: rename internal key",
        "fix: drop old API\n\nBREAKING CHANGE: drop old API",
        "fix: remove flag\n\nBREAKING-CHANGE: remove flag",
    ],
)
def test_conventional_commits_breaking_markers_are_b1(tmp_path: Path, message: str):
    repo = _init_repo(tmp_path)
    _point_origin_main_at_head(repo)
    _commit(repo, "src/pkg/mod.py", "def api():\n    return 3\n", message)

    decision = decide_bump(repo, "origin/main")

    assert (decision.bump_type, decision.reason_code) == ("Y", "B1")






_README = """# pkg

Status: `v0.1.0`, current release `0.1.0`.

pip install "pkg @ git+https://example.test/pkg.git@v0.1.0"

- `flag` added since v0.1.0.
- Older: v0.0.9, not a prefix match: v0.1.01 and 0.1.0.5 and `0.1.00`.
"""


def test_run_bump_syncs_readme_current_version(tmp_path: Path, capsys):
    repo = _init_repo(tmp_path)
    _commit(repo, "README.md", _README, "docs: readme")
    _point_origin_main_at_head(repo)
    _commit(repo, "src/pkg/mod.py", "def api():\n    return 3\n", "fix: tweak internals")

    assert run_bump(repo, "origin/main") == 0

    files = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split()
    assert sorted(files) == ["README.md", "pyproject.toml"]
    text = (repo / "README.md").read_text(encoding="utf-8")
    assert "Status: `v0.1.1`, current release `0.1.1`." in text
    assert "@v0.1.1\"" in text
    assert "- `flag` added since v0.1.0." in text
    assert "- Older: v0.0.9, not a prefix match: v0.1.01 and 0.1.0.5 and `0.1.00`." in text
    assert "README_VERSION_SYNCED: 0.1.0 -> 0.1.1 (3)" in capsys.readouterr().out


_STATUS_README = """# pkg

| Current release | **v0.1.0** (`pyproject.toml` `version = "0.1.0"`) |
"""


def test_run_bump_syncs_readme_quoted_pyproject_version(tmp_path: Path):
    """#7245 — quoted pyproject version in README Status table syncs on bump."""
    repo = _init_repo(tmp_path)
    _commit(repo, "README.md", _STATUS_README, "docs: readme")
    _point_origin_main_at_head(repo)
    _commit(repo, "src/pkg/mod.py", "def api():\n    return 3\n", "fix: tweak internals")

    assert run_bump(repo, "origin/main") == 0

    files = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split()
    assert sorted(files) == ["README.md", "pyproject.toml"]
    row = (
        repo / "README.md"
    ).read_text(encoding="utf-8").splitlines()[2]
    assert (
        row
        == '| Current release | **v0.1.1** (`pyproject.toml` `version = "0.1.1"`) |'
    )


def test_sync_readme_version_keeps_other_quoted_versions():
    """#7245 — only version = "…" with matching semver is replaced."""
    text = (
        'version = "0.1.00"\n'
        'version = "10.1.0"\n'
        '- note since v0.1.0 version = "0.1.0"\n'
        'release "0.1.0" tag\n'
        'version = "0.1.0"\n'
    )
    out, count = sync_readme_version(text, "0.1.0", "0.1.1")
    assert count == 1
    assert out.splitlines() == [
        'version = "0.1.00"',
        'version = "10.1.0"',
        '- note since v0.1.0 version = "0.1.0"',
        'release "0.1.0" tag',
        'version = "0.1.1"',
    ]


def test_run_bump_readme_without_old_version_is_untouched(tmp_path: Path, capsys):
    repo = _init_repo(tmp_path)
    _commit(repo, "README.md", "# pkg\n\nNo versions here, v0.0.9 only.\n", "docs: readme")
    _point_origin_main_at_head(repo)
    _commit(repo, "src/pkg/mod.py", "def api():\n    return 3\n", "fix: tweak internals")

    assert run_bump(repo, "origin/main") == 0

    files = _git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD").split()
    assert files == ["pyproject.toml"]
    assert "README_VERSION_SYNCED" not in capsys.readouterr().out
