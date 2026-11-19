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


def test_run_pytest_uses_python_bin_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7402 AC12 — pytest launcher and PATH use python_bin_command."""
    host = tmp_path / "host"
    host.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\n", encoding="utf-8")
    (venv_bin / "python").chmod(0o755)
    script = host / "resolve"
    script.write_text(
        '#!/bin/sh\nlast="$1"; shift; while [ $# -gt 0 ]; do last="$1"; shift; done; '
        'echo "$last"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    cfg_path = host / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "tests": {"python_bin_command": [str(script)]},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["cmd"] = list(cmd)
        captured["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(
        "issuesmith.worktree_python.python_bin_for",
        lambda _wt: venv_bin.resolve(),
    )
    monkeypatch.setattr(wt.subprocess, "run", fake_run)
    wt._run_pytest_impl(worktree, ["test_x.py"])
    cmd = captured["cmd"]
    env = captured["env"]
    assert isinstance(cmd, list)
    assert cmd[0] == str(venv_bin / "python")
    assert cmd[1:3] == ["-m", "pytest"]
    assert isinstance(env, dict)
    assert str(env.get("PATH", "")).split(":")[0] == str(venv_bin.resolve())


def test_run_pytest_python_root_selects_worktree_not_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7402 AC13 — baseline temp worktree uses python_root for bin lookup."""
    host = tmp_path / "host"
    host.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    tmp_wt = tmp_path / "tmp-wt"
    tmp_wt.mkdir()
    seen: list[str] = []
    script = host / "resolve"
    script.write_text(
        '#!/bin/sh\nlast="$1"; shift; while [ $# -gt 0 ]; do last="$1"; shift; done; '
        'echo "$last" >&2; echo /tmp/bin\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    cfg_path = host / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "tests": {"python_bin_command": [str(script)]},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    def fake_bin_for(path: Path):
        seen.append(str(path.resolve()))
        return None

    monkeypatch.setattr("issuesmith.worktree_python.python_bin_for", fake_bin_for)
    wt._run_pytest_impl(tmp_wt, ["t.py"], python_root=worktree)
    assert seen == [str(worktree.resolve())]
    reset_config_cache()


def test_run_pytest_python_bin_failure_returns_127(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7402 AC14 — WorktreePythonError surfaces as exit 127."""
    host = tmp_path / "host"
    host.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    script = host / "fail"
    script.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    script.chmod(0o755)
    cfg_path = host / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "tests": {"python_bin_command": [str(script)]},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    rc, msg = wt._run_pytest_impl(worktree, ["test_x.py"])
    assert rc == 127
    assert str(script) in msg
    reset_config_cache()
