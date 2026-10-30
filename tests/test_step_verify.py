"""Tests for declarative step verify (sumipan/nexus#5816)."""

from __future__ import annotations

import io
from contextlib import redirect_stderr
from unittest.mock import MagicMock

import pytest
import yaml

from issuesmith.config import ConfigError, load_config, reset_config_cache
from issuesmith.step_verify import main

_VALID_BODY = (
    '```yaml\n'
    'target_repo: sumipan/nexus\n'
    'base_branch: main\n'
    'allow_paths:\n'
    '  - "**"\n'
    'scope_gate:\n'
    '  enabled: false\n'
    '```\n\n'
    "## Overview\nclean body\n"
)


@pytest.fixture(autouse=True)
def _clear_config():
    reset_config_cache()
    yield
    reset_config_cache()


def _write_config(tmp_path, monkeypatch, payload: dict) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_main_clean_body_rc0(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {"x": {"verify": "design_doc"}},
        },
    )
    fake = MagicMock()
    fake.issue_get.return_value = {"body": _VALID_BODY, "labels": [], "milestone": None}
    monkeypatch.setattr("ghdag.forge.get_forge", lambda: fake)
    import sys
    from io import StringIO

    out = StringIO()
    sys.stdout = out
    try:
        rc = main("x", ["42", "--no-apply-deterministic"])
    finally:
        sys.stdout = sys.__stdout__
    assert rc == 0
    assert "VERIFY_FAILED_CHECKS: (none)" in out.getvalue()
    assert "B1_RECOVERY:" not in out.getvalue()
    assert "VERIFY_RECOVERY:" not in out.getvalue()


_HOLD_ONLY_BODY = (
    '```yaml\n'
    'target_repo: sumipan/nexus\n'
    'base_branch: main\n'
    'allow_paths:\n'
    '  - "**"\n'
    'scope_gate:\n'
    '  enabled: false\n'
    'cp1_must_fail: true\n'
    '```\n\n'
    "## Overview\nintentional hold only\n"
)


def test_main_intentional_hold_only_rc0(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {"x": {"verify": "design_doc"}},
        },
    )
    fake = MagicMock()
    fake.issue_get.return_value = {
        "body": _HOLD_ONLY_BODY,
        "labels": [],
        "milestone": None,
    }
    monkeypatch.setattr("ghdag.forge.get_forge", lambda: fake)
    rc = main("x", ["42", "--no-apply-deterministic"])
    assert rc == 0


def test_main_ac_format_fail_rc1(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {"x": {"verify": "design_doc"}},
        },
    )
    fake = MagicMock()
    fake.issue_get.return_value = {
        "body": "## Overview\nno yaml\n",
        "labels": [],
        "milestone": None,
    }
    monkeypatch.setattr("ghdag.forge.get_forge", lambda: fake)
    rc = main("x", ["42", "--no-apply-deterministic"])
    assert rc == 1


def test_main_undeclared_step_returns_rc2(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    err = io.StringIO()
    with redirect_stderr(err):
        rc = main("missing", ["1"])
    assert rc == 2
    assert "not declared" in err.getvalue()


def test_config_unknown_verify_raises(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {"x": {"verify": "unknown"}},
        },
    )
    with pytest.raises(ConfigError, match="verify must be"):
        load_config()
