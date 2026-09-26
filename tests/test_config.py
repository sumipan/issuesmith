"""tests for issuesmith.config — load order, path resolution, builtin defaults."""

from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from issuesmith.config import (
    ConfigError,
    DerivedAllowConfig,
    ScopeCouplingConfig,
    ScopeSizeConfig,
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
    """Without scope_coupling section, defaults apply (ignore_symbols removed in #3628)."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    cfg = load_config()

    assert isinstance(cfg.scope_coupling, ScopeCouplingConfig)
    assert cfg.scope_coupling.enabled is True
    assert not hasattr(cfg.scope_coupling, "ignore_symbols")


def test_scope_coupling_ignore_symbols_key_raises_config_error(tmp_path, monkeypatch):
    """scope_coupling.ignore_symbols was removed in #3628; a stale key is a ConfigError."""
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

    with pytest.raises(ConfigError, match="ignore_symbols"):
        load_config()


def test_scope_coupling_empty_ignore_symbols_list_raises(tmp_path, monkeypatch):
    """Even an empty scope_coupling.ignore_symbols list is rejected (#3628)."""
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

    with pytest.raises(ConfigError, match="ignore_symbols"):
        load_config()


def test_scope_coupling_enabled_flag_loaded_from_yaml(tmp_path, monkeypatch):
    """scope_coupling.enabled: false is honoured; absent means enabled."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/repo", "scope_coupling": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    assert load_config().scope_coupling.enabled is False

    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    reset_config_cache()
    assert load_config().scope_coupling.enabled is True


def test_scope_coupling_search_dirs_default(tmp_path, monkeypatch):
    """search_dirs absent -> default ("tests", "src")."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    cfg = load_config()
    assert cfg.scope_coupling.search_dirs == ("tests", "src")


def test_scope_coupling_search_dirs_explicit(tmp_path, monkeypatch):
    """search_dirs: [tests, src, workflows] -> ("tests", "src", "workflows")."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "scope_coupling": {"search_dirs": ["tests", "src", "workflows"]},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    cfg = load_config()
    assert cfg.scope_coupling.search_dirs == ("tests", "src", "workflows")


def test_scope_coupling_search_dirs_empty_list_raises(tmp_path, monkeypatch):
    """search_dirs: [] -> ConfigError."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "scope_coupling": {"search_dirs": []},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    with pytest.raises(ConfigError, match="search_dirs"):
        load_config()


def test_scope_coupling_search_dirs_non_list_raises(tmp_path, monkeypatch):
    """search_dirs: 'tests' (not a list) -> ConfigError."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "scope_coupling": {"search_dirs": "tests"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    with pytest.raises(ConfigError, match="search_dirs"):
        load_config()


def test_scope_coupling_search_dirs_empty_string_element_raises(tmp_path, monkeypatch):
    """search_dirs with an empty-string element -> ConfigError."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "scope_coupling": {"search_dirs": ["tests", ""]},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    with pytest.raises(ConfigError, match="search_dirs"):
        load_config()


def test_derived_allow_default_enabled(tmp_path, monkeypatch):
    """derived_allow is enabled when unspecified (#3756)."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    assert load_config().derived_allow == DerivedAllowConfig(enabled=True)


def test_derived_allow_enabled_false_loaded(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/repo", "derived_allow": {"enabled": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    assert load_config().derived_allow.enabled is False


def test_derived_allow_unknown_key_raises(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "derived_allow": {"enabled": True, "tests_glob": "tests/**"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError, match="tests_glob"):
        load_config()


def _load_with(tmp_path, monkeypatch, extra: dict):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo", **extra}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    return load_config()


def test_scope_size_defaults_when_section_absent(tmp_path, monkeypatch):
    cfg = _load_with(tmp_path, monkeypatch, {})
    assert cfg.scope_size == ScopeSizeConfig()
    assert cfg.scope_size.enabled is True
    assert cfg.scope_size.max_files == 8
    assert cfg.scope_size.max_concerns == 2
    assert cfg.scope_size.delete_with_new is False
    assert cfg.scope_size.exclude_prefixes == (
        "tests/", "docs/", "README.md", "CHANGELOG.md", "pyproject.toml",
    )


def test_scope_size_vocabulary_overrides(tmp_path, monkeypatch):
    cfg = _load_with(
        tmp_path,
        monkeypatch,
        {
            "scope_size": {
                "delete_words": ["Remove ", "drop"],
                "new_words": ["create"],
                "sub_plan_header": "| # | T | R | C | D |",
                "no_deps_word": "-",
            }
        },
    )
    assert cfg.scope_size.delete_words == ("remove", "drop")
    assert cfg.scope_size.new_words == ("create",)
    assert cfg.scope_size.sub_plan_header == "| # | T | R | C | D |"
    assert cfg.scope_size.no_deps_word == "-"


@pytest.mark.parametrize(
    "bad",
    [
        {"delete_words": []},
        {"delete_words": "delete"},
        {"new_words": [1]},
        {"sub_plan_header": ""},
        {"no_deps_word": 3},
    ],
)
def test_scope_size_vocabulary_rejects_invalid(tmp_path, monkeypatch, bad):
    from issuesmith.config import ConfigError

    with pytest.raises(ConfigError):
        _load_with(tmp_path, monkeypatch, {"scope_size": bad})


def test_scope_size_overrides(tmp_path, monkeypatch):
    cfg = _load_with(
        tmp_path,
        monkeypatch,
        {
            "scope_size": {
                "enabled": False,
                "max_files": 12,
                "max_concerns": 3,
                "delete_with_new": True,
                "exclude_prefixes": ["tests/"],
            }
        },
    )
    assert cfg.scope_size == ScopeSizeConfig(
        enabled=False,
        max_files=12,
        max_concerns=3,
        delete_with_new=True,
        exclude_prefixes=("tests/",),
    )


@pytest.mark.parametrize(
    "section",
    [
        {"max_files": 0},
        {"max_concerns": 0},
        {"max_files": "8"},
        {"max_concerns": 1.5},
        {"max_files": True},
        {"exclude_prefixes": "tests/"},
        {"exclude_prefixes": ["tests/", 1]},
    ],
)
def test_scope_size_invalid_values_raise(tmp_path, monkeypatch, section):
    with pytest.raises(ConfigError, match="scope_size"):
        _load_with(tmp_path, monkeypatch, {"scope_size": section})


def test_scope_coupling_data_file_tests_loaded_from_yaml(tmp_path, monkeypatch):
    """scope_coupling.data_file_tests defaults to true; false is honoured (nexus #3949)."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    assert load_config().scope_coupling.data_file_tests is True

    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/repo", "scope_coupling": {"enabled": True}}),
        encoding="utf-8",
    )
    reset_config_cache()
    assert load_config().scope_coupling.data_file_tests is True

    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/repo", "scope_coupling": {"data_file_tests": False}}),
        encoding="utf-8",
    )
    reset_config_cache()
    cfg = load_config()
    assert cfg.scope_coupling.data_file_tests is False
    assert cfg.scope_coupling.enabled is True


def test_scope_coupling_data_file_tests_via_get_config(tmp_path, monkeypatch):
    from issuesmith.config import get_config

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/repo", "scope_coupling": {"data_file_tests": False}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    assert get_config().scope_coupling.data_file_tests is False
