"""tests for issuesmith.config — load order, path resolution, builtin defaults."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from issuesmith.config import (
    get_config,
    load_config,
    reset_config_cache,
)

# 変更前のモジュール定数と一致すべき内蔵既定値（相対パス / スカラー）
_LEGACY_REPO = "sumipan/nexus"
_LEGACY_TIMEZONE = "Asia/Tokyo"
_LEGACY_SUPPORTED = frozenset(
    {
        "sumipan/mltgnt",
        "sumipan/mltgnt-vscode-extension",
        "sumipan/ghdag",
        "sumipan/slack-project",
        "sumipan/diary",
        "sumipan/nexus-companion",
        "sumipan/nexus",
        "sumipan/okr-core",
        "sumipan/issuesmith",
    }
)
_LEGACY_REL_PATHS = {
    "queue": "jobs/issuesmith-queue.jsonl",
    "queue_state": "logs/issuesmith-queue-state.json",
    "queue_lock": "logs/issuesmith-queue.lock",
    "triage_log": "jobs/issuesmith-triage.jsonl",
    "seed": "configs/night-queue.yaml",
    "night_state": "logs/night-queue-state.json",
    "exec_jsonl": "jobs/exec.jsonl",
    "done_dir": "jobs/done",
    "quota_state": "jobs/quota-gate.json",
    "metrics": "jobs/metrics.jsonl",
    "worktrees_dir": ".claude/worktrees",
    "external_dir": ".claude/external",
    "workflow": "workflows/issuesmith.yml",
    "template_dir": "workflows/issuesmith",
    "engine_state": ".pipeline-state/issuesmith-engine.yml",
}


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def test_builtin_defaults_match_legacy_constants(tmp_path, monkeypatch):
    """issuesmith.yaml 不在時の load_config() が変更前の定数値と一致する。"""
    monkeypatch.delenv("ISSUESMITH_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    # パッケージ配置の互換フォールバックも無効化
    monkeypatch.setattr(
        "issuesmith.config._package_fallback_yaml",
        lambda: tmp_path / "missing-issuesmith.yaml",
    )

    cfg = load_config()

    assert cfg.repo == _LEGACY_REPO
    assert cfg.label_namespace == "issuesmith"
    assert cfg.timezone == _LEGACY_TIMEZONE
    assert cfg.supported_repos == _LEGACY_SUPPORTED
    # root はフォールバック探索失敗時、パッケージから見た nexus ルート相当
    assert isinstance(cfg.root, Path)
    for name, rel in _LEGACY_REL_PATHS.items():
        assert getattr(cfg.paths, name) == (cfg.root / rel).resolve()
    assert ZoneInfo(cfg.timezone) == ZoneInfo("Asia/Tokyo")

    assert set(cfg.engines) == {"design", "implementation"}
    assert cfg.engines["design"].allowed == frozenset({"claude", "codex"})
    assert cfg.engines["design"].default_model == {
        "claude": "claude-opus-4-6",
        "codex": "gpt-5.6-sol",
    }
    assert cfg.engines["design"].light_model == {
        "claude": "claude-sonnet-4-6",
        "codex": "gpt-5.4-mini",
    }
    assert cfg.engines["design"].timeout_sec == 1800
    assert cfg.engines["implementation"].allowed == frozenset({"claude", "cursor"})
    assert cfg.engines["implementation"].default_model == {
        "claude": "claude-sonnet-4-6",
        "cursor": "auto",
    }
    assert cfg.engines["implementation"].timeout_sec == 3600


def test_issuesmith_config_env_overrides_and_resolves_relative(tmp_path, monkeypatch):
    """ISSUESMITH_CONFIG 指定時、値と相対パス絶対化がそのファイル基準になる。"""
    cfg_dir = tmp_path / "instance"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "issuesmith.yaml"
    payload = {
        "repo": "example/other",
        "label_namespace": "issuesmith",
        "timezone": "UTC",
        "supported_repos": ["example/other", "example/extra"],
        "paths": {
            "queue": "data/queue.jsonl",
            "queue_state": "data/state.json",
            "queue_lock": "data/queue.lock",
            "triage_log": "data/triage.jsonl",
            "seed": "data/seed.yaml",
            "night_state": "data/night.json",
            "exec_jsonl": "data/exec.jsonl",
            "done_dir": "data/done",
            "quota_state": "data/quota.json",
            "metrics": "data/metrics.jsonl",
            "worktrees_dir": "wt",
            "external_dir": "ext",
            "workflow": "wf.yml",
            "template_dir": "templates",
            "engine_state": "engine.yml",
        },
        "engines": {
            "design": {
                "allowed": ["claude"],
                "default_model": {"claude": "claude-sonnet-4-6"},
                "timeout_sec": 100,
            },
            "implementation": {
                "allowed": ["cursor"],
                "default_model": {"cursor": "auto"},
                "timeout_sec": 200,
            },
        },
    }
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    cfg = get_config()

    assert cfg.repo == "example/other"
    assert cfg.supported_repos == frozenset({"example/other", "example/extra"})
    assert cfg.root == cfg_dir.resolve()
    assert cfg.paths.queue == (cfg_dir / "data/queue.jsonl").resolve()
    assert cfg.paths.queue.is_absolute()
