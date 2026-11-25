"""Develop line pin step (refs sumipan/nexus#8023)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from issuesmith.config import StepConfig
from issuesmith.contract import StepContext
from issuesmith.lines.develop import pin as develop_pin


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(worktree), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def _init_upstream(tmp_path: Path, tags: list[str], *, extra_files: dict[str, str] | None = None) -> str:
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-q", "-b", "main")
    _git(upstream, "config", "user.email", "t@example.com")
    _git(upstream, "config", "user.name", "T")
    (upstream / "README").write_text("up\n", encoding="utf-8")
    if extra_files:
        for rel, content in extra_files.items():
            path = upstream / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-m", "init")
    for tag in tags:
        _git(upstream, "tag", tag)
    return str(upstream.resolve())


def _clone_worktree(tmp_path: Path, upstream: str) -> tuple[Path, Path]:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", upstream, str(origin)], check=True, capture_output=True)
    worktree = tmp_path / "worktree"
    subprocess.run(["git", "clone", str(origin), str(worktree)], check=True, capture_output=True)
    _git(worktree, "config", "user.email", "t@example.com")
    _git(worktree, "config", "user.name", "T")
    remote = str(origin.resolve())
    _git(worktree, "remote", "set-url", "origin", remote)
    return origin, worktree


def _write_pyproject(worktree: Path, dep_line: str) -> None:
    escaped = dep_line.replace('"', '\\"')
    worktree.joinpath("pyproject.toml").write_text(
        f'[project]\nname = "app"\ndependencies = [\n  "{escaped}",\n]\n',
        encoding="utf-8",
    )
    _git(worktree, "add", "pyproject.toml")
    _git(worktree, "commit", "-m", "pyproject")
    _git(worktree, "push", "origin", "main")


def _pin_ctx(worktree: Path, issue_number: str) -> StepContext:
    return StepContext(
        issue_number=issue_number,
        base_branch="main",
        handler_name="develop",
        is_cross_repo="false",
        target_clone_path="",
        target_repo="o/r",
        issue_repo="o/r",
        source="",
        workflow_name="issuesmith",
        m1_result_filename="",
        m1r_result_filename="",
        worktree_path=str(worktree),
        branch="feat/x",
    )


def test_pin_rewrites_only_url_tag_and_commits_latest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#8023 AC1."""
    from ghdag.forge import LocalForge

    up = _init_upstream(tmp_path, ["v1.0.0", "v1.2.0", "not-semver"])
    _origin, worktree = _clone_worktree(tmp_path, up)
    dep = f"libfoo[cli] @ git+file://{up}@v1.0.0"
    _write_pyproject(worktree, dep)
    forge = LocalForge(tmp_path / "forge")
    number = forge.issue_create("task: bump libfoo to v1.2.0", "body")
    monkeypatch.setattr(develop_pin, "get_forge", lambda **_k: forge)
    result = develop_pin.run(_pin_ctx(worktree, str(number)), StepConfig())
    assert result.status == "done"
    assert result.markers == ["PIN_DONE"]
    text = worktree.joinpath("pyproject.toml").read_text(encoding="utf-8")
    assert "@v1.2.0" in text
    show = _git(worktree, "log", "-1", "--format=%s")
    assert "chore: bump libfoo to v1.2.0" in show.stdout


def test_pin_current_makes_no_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refs sumipan/nexus#8023 AC2."""
    from ghdag.forge import LocalForge

    up = _init_upstream(tmp_path, ["v1.2.0"])
    _origin, worktree = _clone_worktree(tmp_path, up)
    dep = f"libfoo @ git+file://{up}@v1.2.0"
    _write_pyproject(worktree, dep)
    before = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    forge = LocalForge(tmp_path / "forge2")
    number = forge.issue_create("task: bump libfoo to v1.2.0", "body")
    monkeypatch.setattr(develop_pin, "get_forge", lambda **_k: forge)
    result = develop_pin.run(_pin_ctx(worktree, str(number)), StepConfig())
    assert result.markers == ["PIN_CURRENT"]
    after = _git(worktree, "rev-parse", "HEAD").stdout.strip()
    assert before == after


def test_pin_rejects_invalid_title_without_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#8023 AC3."""
    from ghdag.forge import LocalForge

    up = _init_upstream(tmp_path, ["v1.0.0"])
    _origin, worktree = _clone_worktree(tmp_path, up)
    dep = f"libfoo @ git+file://{up}@v1.0.0"
    _write_pyproject(worktree, dep)
    before = worktree.joinpath("pyproject.toml").read_text(encoding="utf-8")
    forge = LocalForge(tmp_path / "forge3")
    number = forge.issue_create("bad title", "body")
    monkeypatch.setattr(develop_pin, "get_forge", lambda **_k: forge)
    result = develop_pin.run(_pin_ctx(worktree, str(number)), StepConfig())
    assert result.status == "andon"
    assert result.andon is not None
    assert result.andon.rule_id == "pin.title_format"
    assert worktree.joinpath("pyproject.toml").read_text(encoding="utf-8") == before


def test_pin_rejects_unsupported_or_ambiguous_requirement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#8023 AC4."""
    from ghdag.forge import LocalForge

    up = _init_upstream(tmp_path, ["v1.0.0"])
    _origin, worktree = _clone_worktree(tmp_path, up)
    worktree.joinpath("pyproject.toml").write_text(
        "[project]\nname='a'\ndependencies=['libfoo>=1.0.0']\n",
        encoding="utf-8",
    )
    _git(worktree, "add", "pyproject.toml")
    _git(worktree, "commit", "-m", "py")
    _git(worktree, "push", "origin", "main")
    forge = LocalForge(tmp_path / "forge4")
    number = forge.issue_create("task: bump libfoo to v1.0.0", "body")
    monkeypatch.setattr(develop_pin, "get_forge", lambda **_k: forge)
    result = develop_pin.run(_pin_ctx(worktree, str(number)), StepConfig())
    assert result.andon is not None
    assert result.andon.rule_id == "pin.unsupported_requirement"


def test_pin_reports_tag_fetch_failure_without_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#8023 AC5."""
    from ghdag.forge import LocalForge

    up = _init_upstream(tmp_path, [])
    _origin, worktree = _clone_worktree(tmp_path, up)
    dep = f"libfoo @ git+file://{up}@v1.0.0"
    _write_pyproject(worktree, dep)
    forge = LocalForge(tmp_path / "forge5")
    number = forge.issue_create("task: bump libfoo to v1.0.0", "body")
    monkeypatch.setattr(develop_pin, "get_forge", lambda **_k: forge)
    result = develop_pin.run(_pin_ctx(worktree, str(number)), StepConfig())
    assert result.andon is not None
    assert result.andon.rule_id == "pin.tags_fetch_failed"


def test_pin_syncs_language_pack_preserving_existing_blocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#8023 AC6."""
    from ghdag.forge import LocalForge

    language_src = (
        "from dataclasses import dataclass\n"
        "from typing import Mapping\n"
        "@dataclass(frozen=True)\n"
        "class LanguagePack:\n"
        "    sections: Mapping[str, str]\n"
        "    extra_word: str\n"
        "EN = LanguagePack(sections={'design': 'Design'}, extra_word='new')\n"
    )
    up = _init_upstream(
        tmp_path,
        ["v0.2.0", "v0.3.0"],
        extra_files={"src/issuesmith/language.py": language_src},
    )
    _origin, worktree = _clone_worktree(tmp_path, up)
    pack_path = worktree / "configs" / "lang.yaml"
    pack_path.parent.mkdir(parents=True)
    pack_path.write_text("sections:\n  design: Design\n  # keep this comment\n", encoding="utf-8")
    worktree.joinpath("issuesmith.yaml").write_text(
        "language_pack: configs/lang.yaml\n",
        encoding="utf-8",
    )
    dep = f"issuesmith @ git+file://{up}@v0.2.0"
    _write_pyproject(worktree, dep)
    _git(worktree, "add", "issuesmith.yaml", "configs/lang.yaml")
    _git(worktree, "commit", "-m", "host cfg")
    _git(worktree, "push", "origin", "main")
    before_pack = pack_path.read_text(encoding="utf-8")
    forge = LocalForge(tmp_path / "forge6")
    number = forge.issue_create("task: bump issuesmith to v0.3.0", "body")
    monkeypatch.setattr(develop_pin, "get_forge", lambda **_k: forge)
    result = develop_pin.run(_pin_ctx(worktree, str(number)), StepConfig())
    assert result.markers == ["PIN_DONE"]
    after_pack = pack_path.read_text(encoding="utf-8")
    assert "# keep this comment" in after_pack
    assert before_pack.splitlines()[0] in after_pack
    assert "extra_word" in after_pack


def test_pin_rejects_nonliteral_language_default_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#8023 AC7."""
    from ghdag.forge import LocalForge

    language_src = (
        "from dataclasses import dataclass\n"
        "def factory():\n    return {}\n"
        "@dataclass(frozen=True)\n"
        "class LanguagePack:\n"
        "    sections: dict\n"
        "EN = LanguagePack(sections=factory())\n"
    )
    up = _init_upstream(
        tmp_path,
        ["v0.2.0", "v0.3.0"],
        extra_files={"src/issuesmith/language.py": language_src},
    )
    _origin, worktree = _clone_worktree(tmp_path, up)
    pack_path = worktree / "configs" / "lang.yaml"
    pack_path.parent.mkdir(parents=True)
    pack_path.write_text("sections: {}\n", encoding="utf-8")
    worktree.joinpath("issuesmith.yaml").write_text(
        "language_pack: configs/lang.yaml\n",
        encoding="utf-8",
    )
    dep = f"issuesmith @ git+file://{up}@v0.2.0"
    _write_pyproject(worktree, dep)
    _git(worktree, "add", "issuesmith.yaml", "configs/lang.yaml")
    _git(worktree, "commit", "-m", "host cfg")
    _git(worktree, "push", "origin", "main")
    forge = LocalForge(tmp_path / "forge7")
    number = forge.issue_create("task: bump issuesmith to v0.3.0", "body")
    monkeypatch.setattr(develop_pin, "get_forge", lambda **_k: forge)
    result = develop_pin.run(_pin_ctx(worktree, str(number)), StepConfig())
    assert result.andon is not None
    assert result.andon.rule_id == "pin.language_pack_default"
    pyproject = worktree.joinpath("pyproject.toml").read_text(encoding="utf-8")
    assert "@v0.2.0" in pyproject
    assert "@v0.3.0" not in pyproject


def test_pin_reports_invalid_pyproject_without_partial_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refs sumipan/nexus#8023 AC17."""
    from ghdag.forge import LocalForge

    up = _init_upstream(tmp_path, ["v1.0.0"])
    _origin, worktree = _clone_worktree(tmp_path, up)
    worktree.joinpath("pyproject.toml").write_text("not = [toml\n", encoding="utf-8")
    _git(worktree, "add", "pyproject.toml")
    _git(worktree, "commit", "-m", "bad")
    _git(worktree, "push", "origin", "main")
    forge = LocalForge(tmp_path / "forge8")
    number = forge.issue_create("task: bump libfoo to v1.0.0", "body")
    monkeypatch.setattr(develop_pin, "get_forge", lambda **_k: forge)
    result = develop_pin.run(_pin_ctx(worktree, str(number)), StepConfig())
    assert result.andon is not None
    assert result.andon.rule_id == "pin.invalid_pyproject"
