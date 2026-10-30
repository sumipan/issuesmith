"""Tests for declarative step tier (sumipan/nexus#5816)."""

from __future__ import annotations

import io
from contextlib import redirect_stderr

import pytest
import yaml

from issuesmith.config import ConfigError, load_config, reset_config_cache
from issuesmith.step_tier import design_doc_tier, main, pr_diff_tier


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


def test_design_doc_tier_heavy_with_design_heading():
    body = "## Design\n\ncontent\n"
    assert design_doc_tier(body) == "heavy"


def test_design_doc_tier_light_without_design_headings():
    assert design_doc_tier("## Overview\nplain\n") == "light"


def test_pr_diff_tier_respects_light_max_threshold():
    assert pr_diff_tier(199, 0, True, light_max_diff_lines=200) == "light"
    assert pr_diff_tier(200, 0, True, light_max_diff_lines=200) == "heavy"
    assert pr_diff_tier(100, 1, True, light_max_diff_lines=200) == "heavy"
    assert pr_diff_tier(100, 0, False, light_max_diff_lines=200) == "heavy"


def test_main_design_doc_via_body_file(tmp_path, monkeypatch):
    body_file = tmp_path / "body.md"
    body_file.write_text("## Design\n\nx\n", encoding="utf-8")
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {"x": {"tier": {"kind": "design_doc"}}},
        },
    )
    import sys
    from io import StringIO

    out = StringIO()
    sys.stdout = out
    try:
        rc = main("x", ["--body-file", str(body_file)])
    finally:
        sys.stdout = sys.__stdout__
    assert rc == 0
    assert out.getvalue().strip() == "heavy"


def test_main_pr_diff_via_gates_pass(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {"x": {"tier": {"kind": "pr_diff", "light_max_diff_lines": 200}}},
        },
    )
    import sys
    from io import StringIO

    out = StringIO()
    sys.stdout = out
    try:
        rc = main(
            "x",
            ["--diff-lines", "199", "--unchecked-ac", "0", "--gates-pass", "true"],
        )
    finally:
        sys.stdout = sys.__stdout__
    assert rc == 0
    assert out.getvalue().strip() == "light"


def test_main_undeclared_step_returns_rc2(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    err = io.StringIO()
    with redirect_stderr(err):
        rc = main("missing", [])
    assert rc == 2
    assert "not declared" in err.getvalue()


def test_b1_tier_module_still_delegates():
    from issuesmith.b1_tier import determine_b1_tier

    assert determine_b1_tier("## Design\n\nx\n") == "heavy"


def test_cp2_tier_module_still_delegates():
    from issuesmith.cp2_tier import determine_cp2_tier

    assert determine_cp2_tier(199, 0, True) == "light"
    assert determine_cp2_tier(200, 0, True) == "heavy"


def test_config_unknown_tier_kind_raises(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {"x": {"tier": {"kind": "unknown"}}},
        },
    )
    with pytest.raises(ConfigError, match="kind must be"):
        load_config()


def test_legacy_tier_cli_emits_deprecation(tmp_path, monkeypatch):
    body_file = tmp_path / "body.md"
    body_file.write_text("## Overview\n", encoding="utf-8")
    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "phases": [{"name": "draft", "role": "design", "entry_step": "b1", "handler": "brushup"}]},
    )
    from tests.test_cli import _run_module

    cfg_env = {"ISSUESMITH_CONFIG": str(tmp_path / "issuesmith.yaml")}
    proc = _run_module(
        "tier",
        "b1",
        "--body-file",
        str(body_file),
        extra_env=cfg_env,
        check=False,
        warn_default=True,
    )
    assert "deprecated" in proc.stderr.lower()
    assert proc.returncode == 0
    assert proc.stdout.strip() == "light"
