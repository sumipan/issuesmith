"""Tests for issuesmith.yaml installs section."""

from __future__ import annotations

import pytest
import yaml

from issuesmith.config import ConfigError, load_config, reset_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_config_cache()
    yield
    reset_config_cache()






def test_load_config_missing_installs_defaults_empty(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "sumipan/nexus"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    cfg = load_config()
    assert cfg.installs == {}


def test_load_config_rejects_non_mapping_installs(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/nexus", "installs": ["a"]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    with pytest.raises(ConfigError, match="mapping"):
        load_config()


def test_load_config_rejects_non_string_install_path(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "sumipan/nexus", "installs": {"issuesmith": 3}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    with pytest.raises(ConfigError, match="string path"):
        load_config()


def test_load_config_installs_env_marker_kept(tmp_path, monkeypatch):
    """Refs sumipan/nexus#7096 acceptance: @env is not resolved as a filesystem path."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {"repo": "sumipan/nexus", "installs": {"issuesmith": "@env"}},
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    cfg = load_config()
    assert cfg.installs["issuesmith"] == "@env"


