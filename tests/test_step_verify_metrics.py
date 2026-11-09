"""Tests for verify_check metrics emission (#6207)."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from io import StringIO
from unittest.mock import MagicMock

import pytest
import yaml

from issuesmith.config import get_config, reset_config_cache
from issuesmith.step_verify import main

_VALID_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "**"\n'
    "scope_gate:\n"
    "  enabled: false\n"
    "```\n\n"
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


def _metrics_cfg(tmp_path, monkeypatch):
    import issuesmith.config as config_mod

    metrics_path = tmp_path / "metrics.jsonl"
    cfg = replace(get_config(), paths=replace(get_config().paths, metrics=metrics_path))
    monkeypatch.setattr(config_mod, "get_config", lambda: cfg)
    return metrics_path


def _run_capture(argv: list[str]) -> tuple[int, str]:
    buf = StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        rc = main("b1", argv)
    finally:
        sys.stdout = old_stdout
    return rc, buf.getvalue()


def test_verify_pass_appends_verify_check(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "steps": {"b1": {"verify": "design_doc"}}},
    )
    metrics_path = _metrics_cfg(tmp_path, monkeypatch)
    fake = MagicMock()
    fake.issue_get.return_value = {"body": _VALID_BODY, "labels": [], "milestone": None}
    monkeypatch.setattr("ghdag.forge.get_forge", lambda: fake)

    rc = main("b1", ["42", "--no-apply-deterministic"])
    assert rc == 0
    line = json.loads(metrics_path.read_text().strip())
    assert line["event"] == "verify_check"
    assert line["step"] == "b1"
    assert line["issue"] == 42
    assert line["rule_ids"] == []
    assert line["violations"] == []


def test_verify_fail_appends_rule_ids_and_violations(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "steps": {"b1": {"verify": "design_doc"}}},
    )
    metrics_path = _metrics_cfg(tmp_path, monkeypatch)
    fake = MagicMock()
    fake.issue_get.return_value = {
        "body": "## Overview\nno yaml\n",
        "labels": [],
        "milestone": None,
    }
    monkeypatch.setattr("ghdag.forge.get_forge", lambda: fake)

    buf = StringIO()
    old_stdout = sys.stdout
    sys.stdout = buf
    try:
        rc = main("b1", ["42", "--no-apply-deterministic"])
    finally:
        sys.stdout = old_stdout
    assert rc == 1
    stdout = buf.getvalue()
    line = json.loads(metrics_path.read_text().strip())
    assert line["event"] == "verify_check"
    failed_ids = stdout.split("VERIFY_FAILED_CHECKS:")[1].split("\n")[0].split()
    assert line["rule_ids"] == failed_ids
    assert {v["rule_id"] for v in line["violations"]} == set(failed_ids)


def test_dry_run_does_not_append_metrics(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "steps": {"b1": {"verify": "design_doc"}}},
    )
    metrics_path = _metrics_cfg(tmp_path, monkeypatch)
    fake = MagicMock()
    fake.issue_get.return_value = {"body": _VALID_BODY, "labels": [], "milestone": None}
    monkeypatch.setattr("ghdag.forge.get_forge", lambda: fake)

    rc_normal, out_normal = _run_capture(["42", "--no-apply-deterministic"])
    lines_after_normal = metrics_path.read_text().count("\n")
    rc_dry, out_dry = _run_capture(["42", "--no-apply-deterministic", "--dry-run"])
    assert rc_normal == rc_dry == 0
    assert out_normal == out_dry
    assert metrics_path.read_text().count("\n") == lines_after_normal


def test_dry_run_fail_does_not_append_metrics(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "steps": {"b1": {"verify": "design_doc"}}},
    )
    metrics_path = _metrics_cfg(tmp_path, monkeypatch)
    fake = MagicMock()
    fake.issue_get.return_value = {
        "body": "## Overview\nno yaml\n",
        "labels": [],
        "milestone": None,
    }
    monkeypatch.setattr("ghdag.forge.get_forge", lambda: fake)

    rc_normal, out_normal = _run_capture(["42", "--no-apply-deterministic"])
    lines_after_normal = metrics_path.read_text().count("\n")
    rc_dry, out_dry = _run_capture(["42", "--no-apply-deterministic", "--dry-run"])
    assert rc_normal == rc_dry == 1
    assert out_normal == out_dry
    assert metrics_path.read_text().count("\n") == lines_after_normal


def test_metrics_write_failure_preserves_rc_and_stdout(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "steps": {"b1": {"verify": "design_doc"}}},
    )
    _metrics_cfg(tmp_path, monkeypatch)
    fake = MagicMock()
    fake.issue_get.return_value = {
        "body": "## Overview\nno yaml\n",
        "labels": [],
        "milestone": None,
    }
    monkeypatch.setattr("ghdag.forge.get_forge", lambda: fake)

    rc_normal, out_normal = _run_capture(["99", "--no-apply-deterministic"])

    def _boom(path, event):
        raise OSError("disk full")

    monkeypatch.setattr("issuesmith.metrics_events.append_event", _boom)

    rc, out = _run_capture(["99", "--no-apply-deterministic"])
    assert rc == rc_normal == 1
    assert "VERIFY_FAILED_CHECKS:" in out
    assert out == out_normal
