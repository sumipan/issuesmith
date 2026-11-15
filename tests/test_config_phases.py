"""phases: config derivation — declaration required, custom applies, engine resolve unchanged."""

from __future__ import annotations

import argparse

import pytest
import yaml

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
def test_missing_phases_raises_config_error(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    with pytest.raises(ConfigError, match="phases must be declared"):
        load_config()


@pytest.mark.no_auto_phases
def test_default_phase_name_omits_handler_raises(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "phases": [{"name": "draft", "role": "design", "entry_step": "b1"}],
        },
    )
    with pytest.raises(ConfigError, match=r"phases\[0\]\.handler is required"):
        load_config()






def test_default_phases_match_nexus_fixture_when_declared(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"repo": "example/app", "phases": NEXUS_TEST_PHASES})
    cfg = load_config()

    assert tuple(p.name for p in cfg.phases) == _DEFAULT_PHASE_NAMES
    assert {p.name: p.role for p in cfg.phases} == _DEFAULT_PHASE_ROLE

    import issuesmith.queue as queue
    import issuesmith.queue_store as queue_store

    assert queue_store.PHASES == _DEFAULT_PHASE_NAMES
    assert queue.PHASE_ROLE == _DEFAULT_PHASE_ROLE
    assert _subparser_option_choices(queue.build_parser(), "enqueue", "--phase") == list(
        _DEFAULT_PHASE_NAMES
    )
    assert {
        p.name: p.entry_step for p in get_config().phases
    } == _DEFAULT_ENTRY_STEPS








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


@pytest.mark.no_auto_phases
def test_advance_when_report_ok(tmp_path, monkeypatch):
    from issuesmith.ops.doctor import advance_when_report

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
        },
    )
    cfg = load_config()
    assert advance_when_report(cfg.phases) == "advance_when: ok"


@pytest.mark.no_auto_phases
def test_advance_when_report_external_unresolved(tmp_path, monkeypatch):
    from issuesmith.ops.doctor import advance_when_report

    mod_name = "mypkg_preds_report"
    (tmp_path / f"{mod_name}.py").write_text("pass\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    ref = f"{mod_name}:nope"
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
                    "advance_when": [ref],
                }
            ],
        },
    )
    cfg = load_config()
    report = advance_when_report(cfg.phases)
    assert report.startswith("advance_when:")
    assert ref in report
