"""tests for issuesmith.config — load order, path resolution, builtin defaults."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from issuesmith.config import (
    ConfigError,
    MergeConfig,
    ScopeSizeConfig,
    load_config,
    reset_config_cache,
)
from issuesmith.errors import IssuesmithError

# repo is required (#3081).
_LEGACY_REPO = "sumipan/nexus"


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def test_engines_heavy_duplicate_raises():
    """Refs sumipan/nexus#6682 — duplicate engines in heavy raise ConfigError."""
    from issuesmith.config import _build_engines

    with pytest.raises(ConfigError, match="duplicate engine"):
        _build_engines(
            {"design": {"heavy": [["claude", "a"], ["claude", "b"]], "timeout_sec": 60}}
        )


def test_legacy_engine_keys_rejected():
    """Refs sumipan/nexus#6682 AC4 — legacy engine keys raise ConfigError mentioning heavy."""
    from issuesmith.config import _build_engines

    with pytest.raises(ConfigError, match="heavy"):
        _build_engines({"design": {"allowed": ["claude"]}})
    with pytest.raises(ConfigError, match="heavy"):
        _build_engines(
            {
                "design": {
                    "default_model": {"claude": "m"},
                    "heavy": [["claude", "m"]],
                    "timeout_sec": 1,
                }
            }
        )
    with pytest.raises(ConfigError, match="heavy"):
        _build_engines(
            {
                "design": {
                    "light_model": {"claude": "m"},
                    "heavy": [["claude", "m"]],
                    "timeout_sec": 1,
                }
            }
        )


def test_light_engine_not_in_heavy_rejected():
    """Refs sumipan/nexus#6682 AC6 — light engines must appear in heavy."""
    from issuesmith.config import _build_engines

    with pytest.raises(ConfigError, match="light"):
        _build_engines(
            {
                "design": {
                    "heavy": [["claude", "a"]],
                    "light": [["cursor", "b"]],
                    "timeout_sec": 1,
                }
            }
        )


def test_paths_brake_state_raises(tmp_path, monkeypatch):
    """Refs sumipan/nexus#7158 — paths.brake_state raises ConfigError."""
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

    with pytest.raises(ConfigError, match="quota_state"):
        load_config()


def test_host_is_repo_checkout_rejects_non_bool(tmp_path, monkeypatch):
    """Refs sumipan/nexus#6836."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": _LEGACY_REPO, "host_is_repo_checkout": "false"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()

    with pytest.raises(
        ConfigError,
        match=r"host_is_repo_checkout must be true or false",
    ):
        load_config()


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


def test_scope_size_overrides(tmp_path, monkeypatch):
    cfg = _load_with(
        tmp_path,
        monkeypatch,
        {
            "scope_size": {
                "enabled": False,
                "max_files": 12,
                "max_concerns": 3,
                "max_slices": 5,
                "delete_with_new": True,
                "exclude_prefixes": ["tests/"],
            }
        },
    )
    assert cfg.scope_size == ScopeSizeConfig(
        enabled=False,
        max_files=12,
        max_concerns=3,
        max_slices=5,
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
        {"max_slices": 0},
        {"max_slices": 1.5},
        {"exclude_prefixes": "tests/"},
        {"exclude_prefixes": ["tests/", 1]},
    ],
)
def test_scope_size_invalid_values_raise(tmp_path, monkeypatch, section):
    with pytest.raises(ConfigError, match="scope_size"):
        _load_with(tmp_path, monkeypatch, {"scope_size": section})


@pytest.mark.parametrize(
    "bad",
    [
        {"atomic_phrases": "one"},
        {"atomic_phrases": [""]},
        {"atomic_phrases": ["   "]},
        {"atomic_phrases": [1]},
    ],
)
def test_scope_size_atomic_phrases_rejects_invalid(tmp_path, monkeypatch, bad):
    from issuesmith.config import ConfigError

    with pytest.raises(ConfigError, match="atomic_phrases"):
        _load_with(tmp_path, monkeypatch, {"scope_size": bad})


def _load_derived_allow(tmp_path, monkeypatch, derived_allow):
    data = {"repo": "example/repo"}
    if derived_allow is not None:
        data["derived_allow"] = derived_allow
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    return load_config().derived_allow


def test_derived_allow_ledger_globs_default(tmp_path, monkeypatch):
    """ledger_globs defaults to the conventions ratchet ledgers (#4791)."""
    cfg = _load_derived_allow(tmp_path, monkeypatch, None)
    assert cfg.ledger_globs == ("tests/conventions/known_*.txt",)
    cfg = _load_derived_allow(tmp_path, monkeypatch, {"enabled": True})
    assert cfg.ledger_globs == ("tests/conventions/known_*.txt",)


def test_derived_allow_ledger_globs_loaded(tmp_path, monkeypatch):
    cfg = _load_derived_allow(tmp_path, monkeypatch, {"ledger_globs": ["a/known_*.txt"]})
    assert cfg.ledger_globs == ("a/known_*.txt",)
    assert cfg.enabled is True
    cfg = _load_derived_allow(tmp_path, monkeypatch, {"ledger_globs": []})
    assert cfg.ledger_globs == ()


@pytest.mark.parametrize("value", ["a/*.txt", [1], {"a": "b"}, None])
def test_derived_allow_ledger_globs_invalid_raises(tmp_path, monkeypatch, value):
    with pytest.raises(ConfigError, match="ledger_globs must be a list of strings"):
        _load_derived_allow(tmp_path, monkeypatch, {"ledger_globs": value})


def test_path_env_prefixes_default_empty(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    assert load_config().path_env_prefixes == ()


def test_path_env_prefixes_invalid_type_raises(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/repo", "path_env_prefixes": "x"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError, match="path_env_prefixes must be a list"):
        load_config()


def test_observe_workflows_default_empty(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    assert load_config().observe.workflows == ()


def test_observe_workflows_skips_issuesmith_name(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "example/repo",
                "label_namespace": "issuesmith",
                "observe": {"workflows": [{"name": "issuesmith"}, {"name": "bump"}]},
            },
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    assert [wf.name for wf in load_config().observe.workflows] == ["bump"]


@pytest.mark.parametrize(
    "observe_raw,match",
    [
        ({"workflows": "nope"}, "observe.workflows must be a list"),
        ({"workflows": [{}]}, "name is required"),
        ({"workflows": [{"name": "a"}, {"name": "a"}]}, "duplicate name"),
    ],
)
def test_observe_workflows_invalid_raises(tmp_path, monkeypatch, observe_raw, match):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/repo", "observe": observe_raw}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError, match=match):
        load_config()


def _main_health_yaml(tmp_path: Path, main_health: dict) -> Path:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "observe": {"main_health": main_health},
        }),
        encoding="utf-8",
    )
    return cfg_path


@pytest.mark.parametrize("bad", ["false", 0, 1, None])
def test_main_health_halt_on_red_non_bool_raises(tmp_path, monkeypatch, bad):
    wt = tmp_path / "wt"
    wt.mkdir()
    cfg_path = _main_health_yaml(
        tmp_path,
        {
            "worktree": str(wt),
            "command": "python -m pytest -q",
            "halt_on_red": bad,
        },
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError, match="halt_on_red"):
        load_config()


@pytest.mark.parametrize(
    "bad_command",
    [
        "python",
        {},
        [],
        [""],
    ],
)
def test_tests_python_command_invalid_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_command: object,
) -> None:
    """Ref: sumipan/nexus#7273 AC4 — invalid ``tests.python_command`` raises ConfigError."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/repo", "tests": {"python_command": bad_command}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError, match="tests.python_command"):
        load_config()


def test_tests_python_command_relative_head_resolves_against_config_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ref: sumipan/nexus#7273 AC5 — relative launcher paths resolve from config dir."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "py").write_text("#!/bin/sh\n", encoding="utf-8")
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "tests": {"python_command": ["scripts/py", "-X"]},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    cfg = load_config(cfg_path)
    assert cfg.tests.python_command[0] == str((tmp_path / "scripts/py").resolve())
    assert cfg.tests.python_command[1:] == ("-X",)

    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "tests": {"python_command": ["python3"]},
        }),
        encoding="utf-8",
    )
    reset_config_cache()
    cfg = load_config(cfg_path)
    assert cfg.tests.python_command == ("python3",)


def test_tests_python_command_and_bin_command_mutually_exclusive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7402 AC15 — both launcher keys cannot appear together."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "tests": {
                "python_command": ["python3"],
                "python_bin_command": ["scripts/bin"],
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError, match="mutually exclusive"):
        load_config()


def test_tests_python_bin_command_relative_resolves_against_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7402 AC16 — relative python_bin_command paths resolve from config root."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "bin-of").write_text("#!/bin/sh\n", encoding="utf-8")
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "tests": {"python_bin_command": ["scripts/bin-of"]},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    cfg = load_config(cfg_path)
    assert cfg.tests.python_bin_command[0] == str((tmp_path / "scripts/bin-of").resolve())


def test_target_remotes_rejects_unsupported_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs sumipan/nexus#7391 AC5 — target_remotes keys must be in supported_repos."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/host",
            "supported_repos": ["a/b"],
            "target_remotes": {"c/d": "/tmp/x.git"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError, match="supported_repos"):
        load_config(cfg_path)


@pytest.mark.parametrize(
    "fragment,match",
    [
        ({"tests": {"report": "junit"}}, "set together"),
        ({"tests": {"report": "xunit", "report_path": "r.xml", "command": ["t"]}}, "junit"),
        ({"tests": {"command": []}}, "non-empty"),
        (
            {"tests": {"report": "junit", "report_path": "r.xml"}},
            "tests.command",
        ),
        ({"lint": {"bogus": 1}}, "lint supports"),
        ({"lint": {"fix_command": ["f"]}}, "lint.command"),
    ],
)
def test_command_gate_config_rejects_invalid_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fragment: dict,
    match: str,
) -> None:
    """Refs sumipan/issuesmith#7788 AC7 — command gate config validation."""
    cfg_path = tmp_path / "issuesmith.yaml"
    data = {
        "repo": "example/repo",
        "phases": [
            {
                "name": "develop",
                "role": "implementation",
                "entry_step": "prepare",
                "handler": "develop",
            }
        ],
        **fragment,
    }
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError, match=match):
        load_config(cfg_path)


def test_tests_rejects_legacy_marker_config_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs #7793 AC4 — removed tests marker config key is rejected as unknown."""
    removed_key = "readme_" + "contract_marker"
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "phases": [
                {
                    "name": "develop",
                    "role": "implementation",
                    "entry_step": "prepare",
                    "handler": "develop",
                }
            ],
            "tests": {removed_key: "x"},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError, match=removed_key):
        load_config(cfg_path)


def test_version_policy_defaults_to_auto(tmp_path, monkeypatch):
    """Refs #7789 AC1 — omitted version section defaults to auto / empty bump_command."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    cfg = load_config()
    assert cfg.version.policy == "auto"
    assert cfg.version.bump_command == ()


def test_version_command_requires_bump_command(tmp_path, monkeypatch):
    """Refs #7789 AC2 — command policy requires bump_command."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/repo", "version": {"policy": "command"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError, match="bump_command"):
        load_config()


@pytest.mark.parametrize(
    "version_raw,match",
    [
        ({"policy": "bogus"}, "version.policy"),
        ({"policy": "none", "extra": 1}, "unknown keys"),
    ],
)
def test_version_rejects_unknown_policy_and_keys(
    tmp_path, monkeypatch, version_raw, match,
):
    """Refs #7789 AC3 — invalid policy and extra keys raise ConfigError."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/repo", "version": version_raw}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError, match=match):
        load_config()


def test_merge_config_defaults_and_values(tmp_path, monkeypatch):
    """Refs #7797 AC10 — merge section defaults and explicit values load correctly."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump({"repo": "example/repo"}), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    cfg = load_config()
    assert cfg.merge == MergeConfig(5, 3.0, 3)

    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/repo",
            "merge": {
                "state_wait_attempts": 2,
                "state_wait_seconds": 0,
                "rebump_max": 0,
            },
        }),
        encoding="utf-8",
    )
    reset_config_cache()
    cfg = load_config()
    assert cfg.merge == MergeConfig(2, 0.0, 0)


@pytest.mark.parametrize(
    "merge_raw,match",
    [
        ({"foo": 1}, "unknown keys"),
        ({"state_wait_attempts": 0}, "state_wait_attempts"),
        ({"state_wait_seconds": -1}, "state_wait_seconds"),
        ({"rebump_max": True}, "rebump_max"),
    ],
)
def test_merge_config_rejects_invalid_values(
    tmp_path, monkeypatch, merge_raw, match,
):
    """Refs #7797 AC11 — invalid merge keys and values raise ConfigError."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/repo", "merge": merge_raw}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError, match=match):
        load_config()


def test_clone_url_resolves_target_remotes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refs sumipan/nexus#7391 AC6 — clone_url resolves paths and keeps SSH URLs."""
    rel_bare = tmp_path.parent / "b.git"
    rel_bare.mkdir()
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({
            "repo": "example/host",
            "supported_repos": ["a/b", "a/c", "a/d"],
            "target_remotes": {
                "a/b": "../b.git",
                "a/c": "ssh://git@example.com/c.git",
            },
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    cfg = load_config(cfg_path)
    assert cfg.clone_url("a/b") == str(rel_bare.resolve())
    assert cfg.clone_url("a/c") == "ssh://git@example.com/c.git"
    assert cfg.clone_url("a/d") == "https://github.com/a/d.git"


def test_invalid_yaml_raises_config_error_with_path_hint(tmp_path, monkeypatch):
    """Refs sumipan/issuesmith#8128 — broken YAML surfaces ConfigError with path hint."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text("repo: [\n", encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError) as excinfo:
        load_config(cfg_path)
    assert excinfo.value.code == "CONFIG_ERROR"
    assert str(cfg_path) in (excinfo.value.hint or "")


def test_invalid_scalar_type_reports_key_path(tmp_path, monkeypatch):
    """Refs sumipan/issuesmith#8128 — non-integer concurrency.default reports key path."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "example/repo", "concurrency": {"default": "nope"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    with pytest.raises(ConfigError) as excinfo:
        load_config()
    assert "concurrency.default" in excinfo.value.message


def test_config_error_preserves_value_error_mro():
    """Refs sumipan/issuesmith#8128 — ConfigError remains a ValueError subclass."""
    assert issubclass(ConfigError, IssuesmithError)
    assert issubclass(ConfigError, ValueError)
