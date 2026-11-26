"""Configuration for andon.diagnose (issue #8077)."""
from __future__ import annotations

import io
import json
import sys
from unittest.mock import patch

import pytest

from issuesmith.config import ConfigError, get_config, load_config, reset_config_cache


def test_diagnose_config_defaults_paths_and_validation(tmp_path, monkeypatch):
    """Refs #8077 AC9."""
    path = tmp_path / "issuesmith.yaml"
    path.write_text("repo: org/repo\n", encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(path))
    reset_config_cache()
    cfg = get_config()
    diagnose = cfg.andon.diagnose
    assert diagnose.enabled is False
    assert diagnose.step == "diagnose"
    assert diagnose.handler == "diagnose"
    assert diagnose.reuse_days == 7
    assert cfg.paths.diagnoses == (tmp_path / "jobs/issuesmith-diagnoses.jsonl").resolve()
    reset_config_cache()


@pytest.mark.parametrize(
    "andon_yaml,msg",
    [
        ("    enabled: 1\n", "enabled"),
        ("    step: '  '\n", "step"),
        ("    step: 1\n", "step"),
        ("    handler: ''\n", "handler"),
        ("    handler: [diagnose]\n", "handler"),
        ("    reuse_days: 0\n", "reuse_days"),
        ("    reuse_days: -3\n", "reuse_days"),
        ("    reuse_days: true\n", "reuse_days"),
        ("    reuse_days: 1.5\n", "reuse_days"),
    ],
)
def test_diagnose_config_rejects_invalid_values(tmp_path, monkeypatch, andon_yaml, msg):
    """Refs #8077 AC9."""
    path = tmp_path / "issuesmith.yaml"
    path.write_text(
        f"repo: org/repo\nandon:\n  diagnose:\n{andon_yaml}",
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(path))
    with pytest.raises(ConfigError, match=msg):
        load_config(path)


def test_disabled_diagnose_cli_does_not_scan_forge_active_dags_or_ledger(tmp_path, monkeypatch):
    """Refs #8077 AC9."""
    from issuesmith import cli

    path = tmp_path / "issuesmith.yaml"
    path.write_text("repo: org/repo\n", encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(path))
    reset_config_cache()

    class NoScanForge:
        def __getattr__(self, name):
            raise AssertionError(f"forge must not be scanned: {name}")

    output = io.StringIO()
    with (
        patch("ghdag.forge.get_forge", return_value=NoScanForge()),
        patch(
            "issuesmith.cli._active_issue_numbers",
            side_effect=AssertionError("active DAGs must not be scanned"),
        ),
        patch.object(sys, "stdout", output),
    ):
        with pytest.raises(SystemExit) as exc:
            cli.main(["andon", "diagnose", "--json"])
    assert exc.value.code == 0
    assert json.loads(output.getvalue()) == {"bundled": [], "requested": [], "failures": []}
