"""phases: 設定の導出 — 無設定は現行動作、カスタムは反映、engine resolve は不変。"""

from __future__ import annotations

import argparse

import pytest
import yaml

from issuesmith import engine
from issuesmith.config import PhaseConfig, get_config, load_config, reset_config_cache


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
_DEFAULT_ENTRY_STEPS = {"draft": "b1", "develop": "cp2", "merge": "m2"}


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


def test_default_phases_match_legacy_when_unset(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    cfg = load_config()

    assert cfg.phases == (
        PhaseConfig("draft", "design", "b1"),
        PhaseConfig("sub", "implementation", "sub-ready"),
        PhaseConfig("develop", "implementation", "cp2"),
        PhaseConfig("merge", "implementation", "m2"),
    )
    assert tuple(p.name for p in cfg.phases) == _DEFAULT_PHASE_NAMES
    assert {p.name: p.role for p in cfg.phases} == _DEFAULT_PHASE_ROLE

    import issuesmith.queue as queue
    import issuesmith.queue_store as queue_store
    import issuesmith.recovery as recovery

    reset_config_cache()
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    assert queue_store.PHASES == _DEFAULT_PHASE_NAMES
    assert queue.PHASE_ROLE == _DEFAULT_PHASE_ROLE
    assert _subparser_option_choices(queue.build_parser(), "enqueue", "--phase") == list(
        _DEFAULT_PHASE_NAMES
    )
    assert _subparser_option_choices(
        recovery.build_parser(), "redispatch", "--phase"
    ) == list(_DEFAULT_ENTRY_STEPS)
    assert {
        p.name: p.entry_step for p in get_config().phases if p.name != "sub"
    } == _DEFAULT_ENTRY_STEPS


def test_custom_three_phases_propagate_to_consumers(tmp_path, monkeypatch):
    phases = [
        {"name": "draft", "role": "design", "entry_step": "b1"},
        {"name": "develop", "role": "implementation", "entry_step": "cp2"},
        {"name": "merge", "role": "implementation", "entry_step": "m2"},
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
        {"name": "draft", "role": "design", "entry_step": "b1"},
        {"name": "develop", "role": "implementation", "entry_step": "cp2"},
        {"name": "merge", "role": "implementation", "entry_step": "m2"},
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
    """PHASE_ROLE（phases.role）変更前後で engine.resolve は role 単位で不変。"""
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

    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    before = _capture()

    phases = [
        {"name": "draft", "role": "implementation", "entry_step": "b1"},
        {"name": "develop", "role": "design", "entry_step": "cp2"},
        {"name": "merge", "role": "implementation", "entry_step": "m2"},
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
