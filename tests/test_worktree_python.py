"""Refs sumipan/issuesmith#7402 — tests.python_bin_command resolution."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.worktree_python import WorktreePythonError, python_bin_for


def test_python_bin_for_uses_worktree_as_last_argument(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7402 AC8 — script receives worktree path and returns venv bin."""
    host = tmp_path / "host"
    host.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (venv_bin / "python").chmod(0o755)
    seen: list[str] = []
    script = host / "resolver"
    script.write_text(
        "#!/bin/sh\nlast=\"$1\"; shift; while [ $# -gt 0 ]; do last=\"$1\"; shift; done; "
        "printf '%s\\n' \"$last\"\n",
        encoding="utf-8",
    )
    script.chmod(0o755)

    def _run(cmd, **kwargs):
        seen.extend(cmd)
        return subprocess.CompletedProcess(cmd, 0, str(venv_bin) + "\n", "")

    monkeypatch.setattr("issuesmith.worktree_python.subprocess.run", _run)
    cfg = host / "issuesmith.yaml"
    cfg.write_text(
        yaml.safe_dump({"repo": "r", "tests": {"python_bin_command": [str(script)]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg))
    reset_config_cache()
    assert python_bin_for(worktree) == venv_bin.resolve()
    assert seen[-1] == str(worktree.resolve())
    reset_config_cache()


def test_python_bin_for_raises_on_command_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7402 AC9 — non-zero exit and bad output become WorktreePythonError."""
    host = tmp_path / "host"
    host.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    script = host / "bad"
    script.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    script.chmod(0o755)
    cfg = host / "issuesmith.yaml"
    cfg.write_text(
        yaml.safe_dump({"repo": "r", "tests": {"python_bin_command": [str(script)]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg))
    reset_config_cache()
    with pytest.raises(WorktreePythonError) as exc:
        python_bin_for(worktree)
    msg = str(exc.value)
    assert str(script) in msg
    assert str(worktree.resolve()) in msg

    script2 = host / "rel"
    script2.write_text("#!/bin/sh\necho relative\n", encoding="utf-8")
    script2.chmod(0o755)
    cfg.write_text(
        yaml.safe_dump({"repo": "r", "tests": {"python_bin_command": [str(script2)]}}),
        encoding="utf-8",
    )
    reset_config_cache()
    with pytest.raises(WorktreePythonError):
        python_bin_for(worktree)
    reset_config_cache()
