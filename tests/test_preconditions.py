"""preconditions.evaluate and registry (#4790)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from issuesmith.config import ConfigError, PhaseConfig, load_config, reset_config_cache
from issuesmith.preconditions import (
    PRECONDITION_REGISTRY,
    PreconditionContext,
    evaluate,
    register,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _phase(**kwargs) -> PhaseConfig:
    defaults = {
        "name": "draft",
        "role": "design",
        "entry_step": "b1",
        "handler": "brushup",
        "advance_when": ("deps_terminal",),
    }
    defaults.update(kwargs)
    return PhaseConfig(**defaults)


def _ctx(issue=None, labels=None, issue_number=1):
    issue = issue or {"state": "OPEN", "labels": [], "body": ""}
    return PreconditionContext(
        issue=issue,
        labels=labels or set(),
        client=MagicMock(),
        issue_number=issue_number,
    )


def test_evaluate_closed_issue():
    from issuesmith.config import get_config

    cfg = get_config()
    ph = cfg.phase("draft")
    ok, why = evaluate(ph, _ctx(issue={"state": "CLOSED", "labels": []}), cfg)
    assert ok is False
    assert why == "issue not OPEN"


def test_evaluate_running_label_blocks():
    from issuesmith.config import get_config

    cfg = get_config()
    ph = cfg.phase("develop")
    _, running, _ = cfg.phase_labels("develop")
    ok, why = evaluate(ph, _ctx(labels={running}), cfg)
    assert ok is False
    assert why.endswith("present")


def test_evaluate_required_labels():
    from issuesmith.config import get_config

    cfg = get_config()
    ph = cfg.phase("develop")
    ok, why = evaluate(ph, _ctx(labels=set()), cfg)
    assert ok is False
    assert "required" in why


def test_evaluate_excluded_labels():
    from issuesmith.config import get_config

    cfg = get_config()
    ph = cfg.phase("develop")
    draft_done = cfg.required_labels("develop").pop() if False else None
    _, _, draft_done = cfg.phase_labels("draft")
    ok, why = evaluate(
        ph,
        _ctx(labels={draft_done, "scope:milestone"}),
        cfg,
    )
    assert ok is False
    assert "excluded" in why


def test_evaluate_all_true_returns_ok():
    from issuesmith.config import get_config

    cfg = get_config()
    ph = cfg.phase("draft")
    ok, why = evaluate(ph, _ctx(), cfg)
    assert ok is True
    assert why == "ok"


def test_register_duplicate_raises():
    def _noop(ctx, config):
        return True, "ok"

    name = "_test_duplicate_predicate"
    if name in PRECONDITION_REGISTRY:
        del PRECONDITION_REGISTRY[name]
    register(name, _noop)
    with pytest.raises(ValueError, match="already registered"):
        register(name, _noop)
    del PRECONDITION_REGISTRY[name]


@pytest.mark.no_auto_phases
def test_load_config_rejects_unknown_advance_predicate(tmp_path, monkeypatch):
    import yaml

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "example/app",
                "phases": [
                    {
                        "name": "draft",
                        "role": "design",
                        "entry_step": "b1",
                        "handler": "brushup",
                        "advance_when": ["no_such_pred"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    with pytest.raises(ConfigError, match="no_such_pred"):
        load_config()
