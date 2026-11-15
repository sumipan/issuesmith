"""sections: / sub_design_subsections: — unset keeps current behavior; custom values apply."""

from __future__ import annotations

import pytest
import yaml

from issuesmith.config import load_config, reset_config_cache


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _write_config(tmp_path, monkeypatch, payload: dict) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def _builtin_defaults(tmp_path, monkeypatch) -> None:
    """Minimal config with only repo (everything else uses package defaults)."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/app"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_terminal_labels_default(tmp_path, monkeypatch):
    _builtin_defaults(tmp_path, monkeypatch)
    cfg = load_config()
    assert "issuesmith:merge-done" in cfg.terminal_labels
    assert "bump:done" in cfg.terminal_labels


