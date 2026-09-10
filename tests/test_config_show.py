"""config show CLI と repo 必須化 / supported_repos 空既定のテスト (#3081)。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from issuesmith.config import get_config, load_config, reset_config_cache

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / "src"

_REPO_REQUIRED_MSG = "issuesmith.yaml に repo: owner/name を設定してください"


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
    """issuesmith.yaml に repo: が無いと get_config() が明示エラーになる。"""
    _write_config(tmp_path, monkeypatch, {"timezone": "UTC"})
    with pytest.raises(ValueError, match=_REPO_REQUIRED_MSG):
        get_config()


def test_load_config_requires_repo_when_missing_file(tmp_path, monkeypatch):
    """設定ファイル不在（builtin 経路）でも repo 未設定はエラー。"""
    _isolate_no_config(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match=_REPO_REQUIRED_MSG):
        load_config()


def test_supported_repos_default_empty_when_repo_set(tmp_path, monkeypatch):
    """supported_repos 未指定の既定は空 frozenset。"""
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    cfg = get_config()
    assert cfg.repo == "example/app"
    assert cfg.supported_repos == frozenset()


def test_config_show_prints_json(tmp_path, monkeypatch, capsys):
    """config show が解決後設定を JSON で出力し exit 0。"""
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "supported_repos": ["example/app", "example/other"],
        },
    )
    from issuesmith.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["config", "show"])
    assert exc.value.code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["repo"] == "example/app"
    assert "phases" in data
    assert isinstance(data["phases"], list)
    assert data["phases"]
    assert {"name", "role", "entry_step"} <= set(data["phases"][0])
    assert "sections" in data
    assert "supported_repos" in data


def test_config_show_via_module_subprocess(tmp_path, monkeypatch):
    """python -m issuesmith config show が JSON を返し exit 0。"""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/cli-show", "supported_repos": ["example/cli-show"]}),
        encoding="utf-8",
    )
    env = {**os.environ, "PYTHONPATH": _pythonpath(), "ISSUESMITH_CONFIG": str(cfg_path)}
    proc = subprocess.run(
        [sys.executable, "-m", "issuesmith", "config", "show"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["repo"] == "example/cli-show"
    assert "phases" in data
    assert "sections" in data
    assert "supported_repos" in data
