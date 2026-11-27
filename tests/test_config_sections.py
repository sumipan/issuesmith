"""Config unknown keys and milestone_chain.child_priority validation (#8142)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from issuesmith.config import ConfigError, load_config, reset_config_cache

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / "src"

_MINIMAL_PHASE = {
    "name": "draft",
    "role": "design",
    "entry_step": "b1",
    "handler": "brushup",
}


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


def _base_payload(**extra: object) -> dict:
    return {"repo": "example/app", "phases": [_MINIMAL_PHASE], **extra}


def _assert_unknown(exc: ConfigError, *, path_fragment: str, hint_fragment: str | None = None):
    assert exc.code == "CONFIG_UNKNOWN_KEY"
    assert path_fragment in exc.message
    assert exc.hint is not None and exc.hint.startswith("Allowed:")
    if hint_fragment is not None:
        assert hint_fragment in exc.hint


def test_top_level_unknown_key_raises(monkeypatch, tmp_path):
    """Refs sumipan/issuesmith#8142 — unknown top-level key is CONFIG_UNKNOWN_KEY."""
    _write_config(tmp_path, monkeypatch, _base_payload(patrol=True))
    with pytest.raises(ConfigError) as exc_info:
        load_config()
    _assert_unknown(exc_info.value, path_fragment="patrol")


def test_triage_typo_key_raises(monkeypatch, tmp_path):
    """Refs #8142 — triage.timout is rejected with triage allowed keys in hint."""
    _write_config(
        tmp_path,
        monkeypatch,
        _base_payload(triage={"timout": 10, "enabled": True}),
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config()
    exc = exc_info.value
    _assert_unknown(exc, path_fragment="triage.timout", hint_fragment="timeout")


@pytest.mark.parametrize(
    "extra,path_fragment",
    [
        (
            {
                "engines": {
                    "design": {
                        "heavy": [["claude", "m"]],
                        "timeout_sec": 60,
                        "bogus": 1,
                    }
                }
            },
            "engines.design.bogus",
        ),
        (
            {
                "phases": [
                    {
                        **_MINIMAL_PHASE,
                        "extra_phase_key": True,
                    }
                ]
            },
            "phases[0].extra_phase_key",
        ),
        (
            {"steps": {"p1": {"module": "m", "unknown_step_field": 1}}},
            "steps.p1.unknown_step_field",
        ),
        ({"scope_gate": {"enabled": True, "bogus": 1}}, "scope_gate.bogus"),
        ({"milestone_chain": {"enabled": True, "bogus": 1}}, "milestone_chain.bogus"),
        ({"observe": {"stall_minutes": 1, "bogus": 1}}, "observe.bogus"),
        ({"concurrency": {"default": 1, "bogus": 1}}, "concurrency.bogus"),
        ({"paths": {"queue": "jobs/q.jsonl", "bogus": "x"}}, "paths.bogus"),
    ],
)
def test_section_unknown_keys_raise(
    monkeypatch, tmp_path, extra: dict, path_fragment: str
):
    """Refs #8142 — each listed section rejects unknown keys with CONFIG_UNKNOWN_KEY."""
    _write_config(tmp_path, monkeypatch, _base_payload(**extra))
    with pytest.raises(ConfigError) as exc_info:
        load_config()
    _assert_unknown(exc_info.value, path_fragment=path_fragment)


def test_metrics_unknown_key_uses_config_unknown_key(monkeypatch, tmp_path):
    """Refs #8142 — formerly ad-hoc metrics rejection uses CONFIG_UNKNOWN_KEY."""
    _write_config(
        tmp_path,
        monkeypatch,
        _base_payload(metrics={"done_step": "merge", "bogus": 1}),
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config()
    _assert_unknown(exc_info.value, path_fragment="metrics.bogus")


def test_two_unknown_keys_in_one_section(monkeypatch, tmp_path):
    """Refs #8142 — multiple unknown keys appear in one error message."""
    _write_config(
        tmp_path,
        monkeypatch,
        _base_payload(triage={"timout": 1, "typo_engine": "x", "enabled": True}),
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config()
    msg = exc_info.value.message
    assert "triage.timout" in msg
    assert "triage.typo_engine" in msg
    assert exc_info.value.code == "CONFIG_UNKNOWN_KEY"


def test_child_priority_invalid_value(monkeypatch, tmp_path):
    """Refs #8142 — milestone_chain.child_priority must be high, normal, or low."""
    _write_config(
        tmp_path,
        monkeypatch,
        _base_payload(milestone_chain={"child_priority": "urgent"}),
    )
    with pytest.raises(ConfigError) as exc_info:
        load_config()
    exc = exc_info.value
    assert exc.code == "INVALID_VALUE"
    assert exc.hint == "Allowed: high, low, normal"


def test_repo_issuesmith_yaml_loads(monkeypatch):
    """Refs #8142 — repository issuesmith.yaml loads without error."""
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(REPO_ROOT / "issuesmith.yaml"))
    reset_config_cache()
    cfg = load_config()
    assert cfg.repo == "sumipan/issuesmith"


def test_config_show_unknown_key_cli_exit_and_stderr(monkeypatch, tmp_path):
    """Refs #8142 — config show reports CONFIG_UNKNOWN_KEY on stderr, exit 1."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(_base_payload(patrol=True)), encoding="utf-8")
    env = os.environ.copy()
    env["ISSUESMITH_CONFIG"] = str(cfg_path)
    env["PYTHONPATH"] = str(SRC_PATH) + (
        f":{env['PYTHONPATH']}" if env.get("PYTHONPATH") else ""
    )
    proc = subprocess.run(
        [sys.executable, "-m", "issuesmith", "config", "show"],
        env=env,
        capture_output=True,
        text=True,
        cwd=tmp_path,
        check=False,
    )
    assert proc.returncode == 1
    assert "error[CONFIG_UNKNOWN_KEY]:" in proc.stderr
    assert "hint: Allowed:" in proc.stderr
