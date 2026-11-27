"""Tests for config show CLI and required repo / empty supported_repos default (#3081)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest
import yaml

from issuesmith import cli
from issuesmith.config import get_config, load_config, reset_config_cache

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / "src"

_REPO_REQUIRED_MSG = r"issuesmith\.yaml.*repo: owner/name"

_MINIMAL_PHASES = [
    {"name": "draft", "role": "design", "entry_step": "b1", "handler": "brushup"},
]


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _pythonpath() -> str:
    existing = os.environ.get("PYTHONPATH", "")
    return str(SRC_PATH) + (f":{existing}" if existing else "")


def _write_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict) -> Path:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    return cfg_path


def _isolate_no_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ISSUESMITH_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "issuesmith.config._package_fallback_yaml",
        lambda: tmp_path / "missing-issuesmith.yaml",
    )


def test_get_config_requires_repo(tmp_path, monkeypatch):
    """get_config() raises an explicit error when issuesmith.yaml has no repo:."""
    _write_config(tmp_path, monkeypatch, {"timezone": "UTC"})
    with pytest.raises(ValueError, match=_REPO_REQUIRED_MSG):
        get_config()


def test_load_config_requires_repo_when_missing_file(tmp_path, monkeypatch):
    """Missing config file (builtin path) still errors when repo is unset."""
    _isolate_no_config(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match=_REPO_REQUIRED_MSG):
        load_config()


def test_supported_repos_default_empty_when_repo_set(tmp_path, monkeypatch):
    """Default supported_repos when unset is an empty frozenset."""
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    cfg = get_config()
    assert cfg.repo == "example/app"
    assert cfg.supported_repos == frozenset()


def test_config_schema_prints_json_schema(tmp_path, monkeypatch, capsys) -> None:
    """Refs #8167 — config schema prints draft 2020-12 JSON without loading host config."""
    missing = tmp_path / "missing-issuesmith.yaml"
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(missing))
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["config", "schema"])
    assert exc_info.value.code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert isinstance(payload.get("properties"), dict)


def test_config_schema_cli_missing_config_file(tmp_path, monkeypatch) -> None:
    """Refs #8167 — config schema succeeds when ISSUESMITH_CONFIG points at a missing file."""
    env = os.environ.copy()
    env["ISSUESMITH_CONFIG"] = str(tmp_path / "does-not-exist.yaml")
    env["PYTHONPATH"] = _pythonpath()
    import subprocess

    proc = subprocess.run(
        [sys.executable, "-m", "issuesmith", "config", "schema"],
        env=env,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload.get("properties")




