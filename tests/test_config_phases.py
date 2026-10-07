"""phases: config derivation — declaration required, custom applies, engine resolve unchanged."""

from __future__ import annotations

import argparse

import pytest
import yaml

from issuesmith import engine
from issuesmith.config import ConfigError, get_config, load_config, reset_config_cache
from tests.conftest import NEXUS_TEST_PHASES


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    reset_config_cache()


_DEFAULT_PHASE_NAMES = ("draft", "sub", "develop", "merge")
_DEFAULT_PHASE_ROLE = {
    "draft": "design",
    "sub": "implementation",
    "develop": "implementation",
    "merge": "implementation",
}
_DEFAULT_ENTRY_STEPS = {
    "draft": "b1",
    "sub": "sub-ready",
    "develop": "cp2",
    "merge": "m2",
}


def _write_config(tmp_path, monkeypatch, payload: dict) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def _subparser_option_choices(
    parser: argparse.ArgumentParser, command: str, option: str
) -> list[str]:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            sub = action.choices[command]
            for a in sub._actions:
                if option in getattr(a, "option_strings", ()):
                    return list(a.choices)
    raise AssertionError(f"{command} {option} choices not found")


@pytest.mark.no_auto_phases
def test_missing_phases_uses_default_phases(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    cfg = load_config()
    assert tuple(p.name for p in cfg.phases) == _DEFAULT_PHASE_NAMES
    assert cfg.phases[0].handler == "brushup"
    assert cfg.phases[0].writes_files is False
    assert cfg.phases[0].advance_when == ("deps_terminal",)
    assert cfg.phases[1].preconditions == ()
    assert cfg.phases[2].excludes == ()


@pytest.mark.no_auto_phases
def test_default_phase_name_omits_handler_uses_default(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "phases": [{"name": "draft", "role": "design", "entry_step": "b1"}],
        },
    )
    cfg = load_config()
    assert cfg.phases[0].handler == "brushup"


@pytest.mark.no_auto_phases
def test_custom_phase_name_requires_handler(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "phases": [{"name": "alpha", "role": "design", "entry_step": "b1"}],
        },
    )
    with pytest.raises(ConfigError, match="handler"):
        load_config()


def test_default_phases_match_nexus_fixture_when_declared(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"repo": "example/app", "phases": NEXUS_TEST_PHASES})
    cfg = load_config()

    assert tuple(p.name for p in cfg.phases) == _DEFAULT_PHASE_NAMES
    assert {p.name: p.role for p in cfg.phases} == _DEFAULT_PHASE_ROLE

    import issuesmith.queue as queue
    import issuesmith.queue_store as queue_store
    import issuesmith.recovery as recovery

    assert queue_store.PHASES == _DEFAULT_PHASE_NAMES
    assert queue.PHASE_ROLE == _DEFAULT_PHASE_ROLE
    assert _subparser_option_choices(queue.build_parser(), "enqueue", "--phase") == list(
        _DEFAULT_PHASE_NAMES
    )
    assert _subparser_option_choices(
        recovery.build_parser(), "redispatch", "--phase"
    ) == list(_DEFAULT_PHASE_NAMES)
    assert {
        p.name: p.entry_step for p in get_config().phases
    } == _DEFAULT_ENTRY_STEPS


def test_custom_three_phases_propagate_to_consumers(tmp_path, monkeypatch):
    phases = [
        {
            "name": "draft",
            "role": "design",
            "entry_step": "b1",
            "handler": "brushup",
            "advance_when": ["deps_terminal"],
        },
        {
            "name": "develop",
            "role": "implementation",
            "entry_step": "cp2",
            "handler": "impl",
            "advance_when": ["deps_terminal"],
        },
        {
            "name": "merge",
            "role": "implementation",
            "entry_step": "m2",
            "handler": "merge",
            "advance_when": ["deps_terminal", "closing_pr_exists"],
        },
    ]
    _write_config(tmp_path, monkeypatch, {"repo": "example/app", "phases": phases})

    import issuesmith.queue as queue
    import issuesmith.queue_store as queue_store
    import issuesmith.recovery as recovery

    cfg = get_config()
    assert tuple(p.name for p in cfg.phases) == ("draft", "develop", "merge")
    assert queue_store.PHASES == ("draft", "develop", "merge")
    assert queue.PHASE_ROLE == {
        "draft": "design",
        "develop": "implementation",
        "merge": "implementation",
    }
    assert _subparser_option_choices(queue.build_parser(), "enqueue", "--phase") == [
        "draft",
        "develop",
        "merge",
    ]
    assert _subparser_option_choices(
        recovery.build_parser(), "redispatch", "--phase"
    ) == ["draft", "develop", "merge"]
    assert {
        p.name: p.entry_step for p in get_config().phases if p.name != "sub"
    } == {"draft": "b1", "develop": "cp2", "merge": "m2"}


def test_milestone_child_phase_order_follows_reversed_config(tmp_path, monkeypatch):
    import issuesmith.milestone as milestone

    phases = [
        {
            "name": "draft",
            "role": "design",
            "entry_step": "b1",
            "handler": "brushup",
            "advance_when": ["deps_terminal"],
        },
        {
            "name": "develop",
            "role": "implementation",
            "entry_step": "cp2",
            "handler": "impl",
            "advance_when": ["deps_terminal"],
        },
        {
            "name": "merge",
            "role": "implementation",
            "entry_step": "m2",
            "handler": "merge",
            "advance_when": ["deps_terminal", "closing_pr_exists"],
        },
    ]
    _write_config(tmp_path, monkeypatch, {"repo": "example/app", "phases": phases})

    assert milestone._child_phase_label({"issuesmith:develop-done"}) == "develop-done"
    assert (
        milestone._child_phase_label(
            {"issuesmith:merge-ready", "issuesmith:draft-done"}
        )
        == "merge-ready"
    )


def test_engine_resolve_unchanged_across_phase_role_change(tmp_path, monkeypatch):
    """engine.resolve stays role-stable before/after PHASE_ROLE (phases.role) changes."""
    monkeypatch.setattr(engine, "_allowed_models", lambda eng: None)

    states = {
        "claude": {
            "design": {"engine": "claude", "model": "claude-opus-4-6"},
            "implementation": {"engine": "claude", "model": "claude-sonnet-4-6"},
        },
        "cursor": {
            "design": {"engine": "codex", "model": "gpt-5.6-sol"},
            "implementation": {"engine": "cursor", "model": "auto"},
        },
        "codex": {
            "design": {"engine": "codex", "model": "gpt-5.6-sol"},
            "implementation": {"engine": "claude", "model": "claude-sonnet-4-6"},
        },
    }

    def _capture() -> dict[str, dict[str, engine.RoleSelection]]:
        out: dict[str, dict[str, engine.RoleSelection]] = {}
        for eng_name, state in states.items():
            monkeypatch.setattr(engine, "load_state", lambda s=state: s)
            out[eng_name] = {
                "design": engine.resolve("design"),
                "implementation": engine.resolve("implementation"),
            }
        return out

    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "phases": NEXUS_TEST_PHASES},
    )
    before = _capture()

    phases = [
        {
            "name": "draft",
            "role": "implementation",
            "entry_step": "b1",
            "handler": "brushup",
            "advance_when": ["deps_terminal"],
        },
        {
            "name": "develop",
            "role": "design",
            "entry_step": "cp2",
            "handler": "impl",
            "advance_when": ["deps_terminal"],
        },
        {
            "name": "merge",
            "role": "implementation",
            "entry_step": "m2",
            "handler": "merge",
            "advance_when": ["deps_terminal", "closing_pr_exists"],
        },
    ]
    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "phases": phases},
    )
    after = _capture()

    assert before == after
    for eng_name in ("claude", "cursor", "codex"):
        assert eng_name in before


def test_new_phase_keys_loaded_with_defaults(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "phases": [
                {
                    "name": "draft",
                    "role": "design",
                    "entry_step": "b1",
                    "handler": "brushup",
                    "advance_when": ["deps_terminal"],
                }
            ],
            "steps": {"p1": {"andon_when": ["external_leak.target_unknown"], "accepts": []}},
        },
    )
    cfg = load_config()
    ph = cfg.phases[0]
    assert ph.excludes == ()
    assert ph.writes_files is True
    assert ph.advance_when == ("deps_terminal",)
    assert cfg.terminal_without_merge == ()
    step = cfg.steps["p1"]
    assert step.accepts == ()
    assert step.andon_when == ("external_leak.target_unknown",)


# ---------------------------------------------------------------------------
# phases[].steps and label_write_guard (#4807)
# ---------------------------------------------------------------------------


def test_steps_default_to_entry_step(tmp_path, monkeypatch):
    from issuesmith.projection import phase_steps

    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "phases": NEXUS_TEST_PHASES},
    )
    cfg = load_config()
    assert {p.name: phase_steps(p) for p in cfg.phases} == {
        "draft": ("b1",),
        "sub": ("sub1",),
        "develop": ("p0", "p1", "p3", "cp2"),
        "merge": ("m1", "m2-role-dispatch"),
    }


def test_steps_are_read_in_order(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {
        "repo": "example/app",
        "phases": [
            {
                "name": "draft",
                "role": "design",
                "entry_step": "b1",
                "handler": "brushup",
                "steps": ["b1"],
                "advance_when": ["deps_terminal"],
            },
            {
                "name": "develop",
                "role": "implementation",
                "entry_step": "cp2",
                "handler": "impl",
                "steps": ["p0", "p1", "p3", "cp2"],
                "advance_when": ["deps_terminal"],
            },
        ],
    })
    cfg = load_config()
    assert [p.steps for p in cfg.phases] == [("b1",), ("p0", "p1", "p3", "cp2")]


def test_steps_must_be_a_list(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {
        "repo": "example/app",
        "phases": [{
            "name": "draft",
            "role": "design",
            "entry_step": "b1",
            "handler": "brushup",
            "steps": "b1",
            "advance_when": ["deps_terminal"],
        }],
    })
    with pytest.raises(ValueError, match="steps"):
        load_config()


def test_duplicate_step_across_phases_is_config_error(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {
        "repo": "example/app",
        "phases": [
            {
                "name": "draft",
                "role": "design",
                "entry_step": "b1",
                "handler": "brushup",
                "steps": ["b1", "p0"],
                "advance_when": ["deps_terminal"],
            },
            {
                "name": "develop",
                "role": "implementation",
                "entry_step": "cp2",
                "handler": "impl",
                "steps": ["p0", "cp2"],
                "advance_when": ["deps_terminal"],
            },
        ],
    })
    with pytest.raises(ConfigError, match="p0"):
        load_config()


def test_duplicate_implicit_entry_step_is_config_error(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {
        "repo": "example/app",
        "phases": [
            {
                "name": "draft",
                "role": "design",
                "entry_step": "b1",
                "handler": "brushup",
                "advance_when": ["deps_terminal"],
            },
            {
                "name": "develop",
                "role": "implementation",
                "entry_step": "b1",
                "handler": "impl",
                "advance_when": ["deps_terminal"],
            },
        ],
    })
    with pytest.raises(ConfigError):
        load_config()


def test_label_write_guard_defaults_to_warn(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "phases": NEXUS_TEST_PHASES},
    )
    assert load_config().label_write_guard == "warn"


@pytest.mark.parametrize("mode", ["warn", "enforce"])
def test_label_write_guard_accepts_known_modes(tmp_path, monkeypatch, mode):
    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "phases": NEXUS_TEST_PHASES, "label_write_guard": mode},
    )
    assert load_config().label_write_guard == mode


@pytest.mark.parametrize("mode", ["off", "Enforce", True, 1])
def test_label_write_guard_rejects_other_values(tmp_path, monkeypatch, mode):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "phases": NEXUS_TEST_PHASES,
            "label_write_guard": mode,
        },
    )
    with pytest.raises(ConfigError, match="label_write_guard"):
        load_config()
