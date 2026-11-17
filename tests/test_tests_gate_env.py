"""Tests gate pytest subprocess must not inherit host ``ISSUESMITH_CONFIG`` (#7054)."""
from __future__ import annotations

from pathlib import Path

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
