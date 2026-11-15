"""Tests for declarative step verify (sumipan/nexus#5816)."""

from __future__ import annotations

import io
from contextlib import redirect_stderr

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




def _install_host_gate_module(tmp_path, monkeypatch, mod_name: str, source: str) -> str:
    (tmp_path / f"{mod_name}.py").write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return f"{mod_name}:MY_GATE"


def test_collect_violations_runs_host_issue_gate_from_requires(tmp_path, monkeypatch):
    """Refs sumipan/nexus#6332 — B1 verify runs host issue gates declared in requires."""
    from issuesmith.step_verify import collect_violations

    mod_name = "host_issue_gate_verify_ac1"
    gate_ref = _install_host_gate_module(
        tmp_path,
        monkeypatch,
        mod_name,
        """
from issuesmith.gates import GateEntry
from issuesmith.gate_registry import Violation

class _G:
    def check(self, body, labels):
        if "FORBIDDEN_TOKEN" in body:
            return [
                Violation(
                    rule_id="host_test.forbidden_token",
                    severity="fail",
                    message="token present",
                    location=None,
                    auto_fixable=False,
                    fix_hint=None,
                )
            ]
        return []

MY_GATE = GateEntry(input_kind="issue", build=lambda ctx: _G())
""",
    )
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {"p0": {"requires": [gate_ref]}},
        },
    )
    bad_body = _VALID_BODY.replace("clean body", "FORBIDDEN_TOKEN here")
    assert not any(
        v.rule_id == "host_test.forbidden_token"
        for v in collect_violations(_VALID_BODY, [])
    )
    hits = collect_violations(bad_body, [])
    assert any(v.rule_id == "host_test.forbidden_token" for v in hits)


def test_collect_violations_skips_host_worktree_gate(tmp_path, monkeypatch):
    """Refs sumipan/nexus#6332 — worktree-kind host gates are not built during B1 verify."""
    from issuesmith.step_verify import collect_violations

    mod_name = "host_worktree_gate_verify_ac2"
    gate_ref = _install_host_gate_module(
        tmp_path,
        monkeypatch,
        mod_name,
        """
from issuesmith.gates import GateEntry

def _build(ctx):
    raise AssertionError("worktree gate build must not run during design_doc verify")

MY_GATE = GateEntry(input_kind="worktree", build=_build)
""",
    )
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {"p0": {"requires": [gate_ref]}},
        },
    )
    collect_violations(_VALID_BODY, [])


def test_host_gate_build_context_uses_body_yaml(tmp_path, monkeypatch):
    """Refs sumipan/nexus#6332 — host gate build context mirrors body YAML scope fields."""
    from issuesmith.step_verify import collect_violations

    mod_name = "host_gate_ctx_verify_ac3"
    gate_ref = _install_host_gate_module(
        tmp_path,
        monkeypatch,
        mod_name,
        """
from issuesmith.gates import GateEntry

_STATE = {"ctx": None}

class _G:
    def check(self, body, labels):
        return []

def _build(ctx):
    _STATE["ctx"] = ctx
    return _G()

MY_GATE = GateEntry(input_kind="issue", build=_build)
""",
    )
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {"p0": {"requires": [gate_ref]}},
        },
    )
    body = (
        "```yaml\n"
        "target_repo: sumipan/nexus\n"
        "base_branch: release/2.0\n"
        "allow_paths:\n"
        "  - src/foo.py\n"
        "  - tests/bar.py\n"
        "scope_gate:\n"
        "  enabled: false\n"
        "```\n\n"
        "## Overview\nctx probe\n"
    )
    collect_violations(body, [], issue_number=99)
    mod = __import__(mod_name, fromlist=["_STATE"])
    ctx = mod._STATE["ctx"]
    assert ctx is not None
    assert ctx.base_branch == "release/2.0"
    assert ctx.allow_paths == ["src/foo.py", "tests/bar.py"]
    assert ctx.variables.get("issue_number") == "99"


def test_host_gate_check_error_becomes_violation(tmp_path, monkeypatch):
    """Refs sumipan/nexus#6332 — gate check exceptions become step_verify.host_gate_error."""
    from issuesmith.step_verify import collect_violations

    mod_name = "host_gate_check_err_ac4"
    gate_ref = _install_host_gate_module(
        tmp_path,
        monkeypatch,
        mod_name,
        """
from issuesmith.gates import GateEntry

class _G:
    def check(self, body, labels):
        raise RuntimeError("boom")

MY_GATE = GateEntry(input_kind="issue", build=lambda ctx: _G())
""",
    )
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {"p0": {"requires": [gate_ref]}},
        },
    )
    violations = collect_violations(_VALID_BODY, [])
    err = [v for v in violations if v.rule_id == "step_verify.host_gate_error"]
    assert len(err) == 1
    assert "RuntimeError" in err[0].message
    assert gate_ref in err[0].message


def test_host_gate_skipped_when_target_clone_missing(tmp_path, monkeypatch):
    """Refs sumipan/nexus#6332 — skip host issue gates when external clone is absent."""
    from issuesmith.step_verify import collect_violations

    mod_name = "host_gate_skip_clone_ac5"
    gate_ref = _install_host_gate_module(
        tmp_path,
        monkeypatch,
        mod_name,
        """
from issuesmith.gates import GateEntry

_STATE = {"build_called": False}

class _G:
    def check(self, body, labels):
        return []

def _build(ctx):
    _STATE["build_called"] = True
    return _G()

MY_GATE = GateEntry(input_kind="issue", build=_build)
""",
    )
    ext = tmp_path / "ext"
    ext.mkdir()
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "paths": {"external_dir": str(ext)},
            "steps": {"p0": {"requires": [gate_ref]}},
        },
    )
    body = (
        "```yaml\n"
        "target_repo: sumipan/missing-external\n"
        "base_branch: main\n"
        "allow_paths:\n"
        '  - "**"\n'
        "```\n\n"
        "## Overview\nno clone\n"
    )
    violations = collect_violations(body, [])
    assert not any(v.rule_id == "step_verify.host_gate_error" for v in violations)
    mod = __import__(mod_name, fromlist=["_STATE"])
    assert mod._STATE["build_called"] is False
