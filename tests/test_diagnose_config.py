"""Configuration for andon.diagnose (issue #8077)."""
from __future__ import annotations

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
        ("    handler: ''\n", "handler"),
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
