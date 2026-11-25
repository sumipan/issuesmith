"""Tests for issuesmith.yaml pin_packages and legacy installs ignoring."""

from __future__ import annotations

import pytest
import yaml

from issuesmith.config import ConfigError, load_config, reset_config_cache


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def test_legacy_installs_is_ignored_and_pin_packages_defaults_empty(tmp_path, monkeypatch):
    """Refs sumipan/issuesmith#8028."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "installs": {
                    "issuesmith": "/var/tmp/issuesmith",
                    "ghdag": "@env",
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    cfg = load_config()
    assert cfg.pin_packages == ()


@pytest.mark.parametrize(
    "pin_packages",
    [
        "ghdag",
        [3],
        [""],
    ],
)
def test_pin_packages_rejects_invalid_shape(
    tmp_path, monkeypatch, pin_packages
):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {"repo": "sumipan/nexus", "pin_packages": pin_packages},
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    with pytest.raises(ConfigError, match="pin_packages"):
        load_config()
