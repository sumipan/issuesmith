"""Tests gate pytest subprocess must not inherit host ``ISSUESMITH_CONFIG`` (#7054)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.gates import worktree as wt

_PROBE = """\
import os


def test_probe():
    assert os.environ.get("ISSUESMITH_CONFIG") != "/nonexistent/issuesmith.yaml"
    assert os.environ.get("MARKER_FOR_TEST") == "kept"
"""


def test_pytest_run_does_not_inherit_host_config_path(
    tmp_path: Path, monkeypatch,
) -> None:
    """Ref: sumipan/nexus#7054."""
    (tmp_path / "test_probe.py").write_text(_PROBE, encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", "/nonexistent/issuesmith.yaml")
    monkeypatch.setenv("MARKER_FOR_TEST", "kept")
    rc, _output = wt._run_pytest_impl(tmp_path, ["test_probe.py"])
    assert rc == 0


def test_run_pytest_default_command_starts_with_python(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ref: sumipan/nexus#7273 AC1 — default argv uses ``python -m pytest``."""
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = list(cmd)
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(wt.subprocess, "run", fake_run)
    wt._run_pytest_impl(tmp_path, ["test_x.py"])
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == "python"
    assert cmd[1:3] == ["-m", "pytest"]


def test_run_pytest_uses_configured_command_in_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ref: sumipan/nexus#7273 AC2 — ``tests.python_command`` replaces the pytest launcher."""
    launcher = tmp_path / "launcher"
    launcher.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "tests": {"python_command": [str(launcher), "--flag"]},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = list(cmd)
        captured["cwd"] = kwargs.get("cwd")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(wt.subprocess, "run", fake_run)
    wt._run_pytest_impl(tmp_path, ["test_x.py"])
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[:2] == [str(launcher), "--flag"]
    assert cmd[2:4] == ["-m", "pytest"]
    assert captured["cwd"] == str(tmp_path)
