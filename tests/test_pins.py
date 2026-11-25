"""Unit tests for issuesmith.pins pyproject readers."""

from __future__ import annotations

from unittest.mock import MagicMock

from packaging.version import Version

from issuesmith import pins


def test_url_pin_version_reads_plain_and_extras():
    text = (
        'issuesmith = "issuesmith @ git+https://github.com/sumipan/issuesmith.git@v0.133.0"\n'
        'mltgnt = "mltgnt[slack] @ git+https://github.com/sumipan/mltgnt.git@v0.123.3"\n'
    )
    assert pins.url_pin_version(text, "issuesmith") == Version("0.133.0")
    assert pins.url_pin_version(text, "mltgnt") == Version("0.123.3")
    assert pins.url_pin_version(text, "ghdag") is None


def test_lower_bound_version_reads_lower_bound():
    text = 'dependencies = ["ghdag>=1.2.3"]\n'
    assert pins.lower_bound_version(text, "ghdag") == Version("1.2.3")
    text_extras = 'optional = ["ghdag[x] >= 1.2.3"]\n'
    assert pins.lower_bound_version(text_extras, "ghdag") == Version("1.2.3")


def test_lower_bound_version_ignores_other_package_prefix():
    text = 'dep = "ghdag-foo>=9.9.9"\n'
    assert pins.lower_bound_version(text, "ghdag") is None


def test_base_pyproject_text_reads_git_show(tmp_path, monkeypatch):
    pyproject = 'issuesmith = "issuesmith @ git+https://example.invalid/i.git@v1.0.0"\n'
    proc = MagicMock(returncode=0, stdout=pyproject)
    mock_run = MagicMock(return_value=proc)
    monkeypatch.setattr(pins.subprocess, "run", mock_run)
    monkeypatch.setattr(
        "issuesmith.scope_gate.resolve_scope_root",
        lambda metadata, cfg: tmp_path,
    )
    metadata = {"target_repo": "sumipan/nexus", "base_branch": "main"}
    got = pins.base_pyproject_text(metadata, MagicMock())
    assert got == pyproject
    cmd = mock_run.call_args[0][0]
    assert cmd[0:2] == ["git", "-C"]
    assert cmd[2] == str(tmp_path)
    assert cmd[3] == "show"
    assert cmd[4] == "origin/main:pyproject.toml"
