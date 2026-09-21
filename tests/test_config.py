"""tests for issuesmith.config — load order, path resolution, builtin defaults."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from issuesmith.config import (
    ScopeCouplingConfig,
    get_config,
    load_config,
    reset_config_cache,
)

# Builtin defaults that must match pre-change module constants (relative paths / scalars).
# repo is required (#3081). Package default for supported_repos is empty.
_LEGACY_REPO = "sumipan/nexus"
_LEGACY_TIMEZONE = "Asia/Tokyo"
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
    """With only repo set, other builtin defaults match pre-change constant values."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": _LEGACY_REPO}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    cfg = load_config()

    assert cfg.repo == _LEGACY_REPO
    assert cfg.label_namespace == "issuesmith"
    assert cfg.timezone == _LEGACY_TIMEZONE
    assert cfg.supported_repos == frozenset()
    assert isinstance(cfg.root, Path)
    for name, rel in _LEGACY_REL_PATHS.items():
        assert getattr(cfg.paths, name) == (cfg.root / rel).resolve()
    # AC-1: when brake_state is omitted, fall back to quota_state (single gate)
    assert cfg.paths.brake_state == cfg.paths.quota_state
    assert ZoneInfo(cfg.timezone) == ZoneInfo("Asia/Tokyo")

    assert set(cfg.engines) == {"design", "implementation"}
    assert cfg.engines["design"].allowed == frozenset({"claude", "codex"})
    assert cfg.engines["design"].default_model == {
        "claude": "claude-opus-4-6",
        "codex": "gpt-5.6-sol",
    }
    assert cfg.engines["design"].light_model == {
        "claude": "claude-sonnet-4-6",
        "codex": "gpt-5.5",
    }
    assert cfg.engines["design"].timeout_sec == 1800
    assert cfg.engines["implementation"].allowed == frozenset({"claude", "cursor"})
    assert cfg.engines["implementation"].default_model == {
        "claude": "claude-sonnet-4-6",
        "cursor": "auto",
    }
    assert cfg.engines["implementation"].timeout_sec == 3600
    assert cfg.forbidden_pr_paths == (
        "jobs/**",
        "logs/**",
        ".sessions/**",
        "*.jsonl",
        "*.pid",
        "*.lock",
    )


def test_missing_repo_raises(tmp_path, monkeypatch):
    """Missing repo raises an explicit error from get_config / load_config."""
    monkeypatch.delenv("ISSUESMITH_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "issuesmith.config._package_fallback_yaml",
        lambda: tmp_path / "missing-issuesmith.yaml",
    )
    with pytest.raises(ValueError, match=r"issuesmith\.yaml.*repo: owner/name"):
        load_config()


def test_issuesmith_config_env_overrides_and_resolves_relative(tmp_path, monkeypatch):
    """With ISSUESMITH_CONFIG set, values and relative paths resolve against that file."""
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
    # brake_state omitted → same value as quota_state
    assert cfg.paths.brake_state == cfg.paths.quota_state
    assert cfg.paths.quota_state == (cfg_dir / "data/quota.json").resolve()


def test_brake_state_explicit_resolves_relative(tmp_path, monkeypatch):
    """AC-2: explicit paths.brake_state becomes an absolute Path relative to the config file."""
    cfg_dir = tmp_path / "instance"
    cfg_dir.mkdir()
    cfg_path = cfg_dir / "issuesmith.yaml"
    payload = {
        "repo": "example/other",
        "paths": {
            "quota_state": "jobs/quota-gate.json",
            "brake_state": "jobs/issuesmith-brake.json",
        },
    }
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    cfg = load_config()

    assert cfg.paths.quota_state == (cfg_dir / "jobs/quota-gate.json").resolve()
    assert cfg.paths.brake_state == (cfg_dir / "jobs/issuesmith-brake.json").resolve()
    assert cfg.paths.brake_state != cfg.paths.quota_state


def test_brake_state_omitted_equals_quota_state(tmp_path, monkeypatch):
    """AC-1: even when the brake_state key is absent, brake_state == quota_state."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "example/other",
                "paths": {"quota_state": "data/quota.json"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    cfg = load_config()
    assert cfg.paths.brake_state == cfg.paths.quota_state
    assert cfg.paths.quota_state == (tmp_path / "data/quota.json").resolve()


def test_scope_coupling_defaults_when_section_absent(tmp_path, monkeypatch):
    """Without scope_coupling section, defaults to empty ignore_symbols."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    cfg = load_config()

    assert isinstance(cfg.scope_coupling, ScopeCouplingConfig)
    assert cfg.scope_coupling.ignore_symbols == ()


def test_scope_coupling_ignore_symbols_loaded_from_yaml(tmp_path, monkeypatch):
    """scope_coupling.ignore_symbols from YAML is a tuple of strings."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "scope_coupling": {"ignore_symbols": ["run_guarded", "my_func"]},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    cfg = load_config()

    assert isinstance(cfg.scope_coupling, ScopeCouplingConfig)
    assert "run_guarded" in cfg.scope_coupling.ignore_symbols
    assert "my_func" in cfg.scope_coupling.ignore_symbols


def test_scope_coupling_empty_ignore_symbols_list(tmp_path, monkeypatch):
    """scope_coupling.ignore_symbols: [] results in empty tuple."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "scope_coupling": {"ignore_symbols": []},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    cfg = load_config()

    assert cfg.scope_coupling.ignore_symbols == ()
