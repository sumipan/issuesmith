"""Tests for declarative step tier (sumipan/nexus#5816)."""

from __future__ import annotations

import io
from contextlib import redirect_stderr
from pathlib import Path

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


def test_main_unknown_argument_uses_contract(tmp_path, monkeypatch, capsys) -> None:
    """refs #8140 — unknown tier CLI flags use ContractParser stderr contract."""
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {"p1": {"tier": {"kind": "pr_diff", "light_max_diff_lines": 200}}},
        },
    )
    with pytest.raises(SystemExit) as exc_info:
        main(
            "p1",
            [
                "--diff-lines",
                "1",
                "--unchecked-ac",
                "0",
                "--gates-pass",
                "true",
                "--unknown",
            ],
        )
    assert exc_info.value.code == 2
    err = capsys.readouterr().err
    assert err.splitlines()[0].startswith("error[UNKNOWN_ARGUMENT]:")


def test_command_entries_use_contract_parser() -> None:
    """refs #8140 — gate/step entry modules must not construct argparse.ArgumentParser directly."""
    root = Path(__file__).resolve().parents[1] / "src" / "issuesmith"
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name == "cli_errors.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "argparse.ArgumentParser(" in text:
            offenders.append(str(path.relative_to(root.parent.parent)))
    assert offenders == []


