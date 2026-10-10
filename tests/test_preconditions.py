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


def test_deps_terminal_ignores_self_reference_in_dependencies_prose():
    from issuesmith.config import get_config

    cfg = get_config()
    ph = cfg.phase("draft")
    body = "## Dependencies\n\n#5061 mentioned only in prose here\n"
    ok, why = evaluate(
        ph,
        _ctx(issue={"state": "OPEN", "labels": [], "body": body}, issue_number=5061),
        cfg,
    )
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


def _minimal_phases(advance_when: list[str]) -> dict:
    return {
        "repo": "example/app",
        "phases": [
            {
                "name": "draft",
                "role": "design",
                "entry_step": "b1",
                "handler": "brushup",
                "advance_when": advance_when,
            }
        ],
    }


@pytest.mark.no_auto_phases
def test_load_config_accepts_external_advance_when(tmp_path, monkeypatch):
    import yaml

    mod_name = "mypkg_preds_accepts"
    (tmp_path / f"{mod_name}.py").write_text(
        "def always_true(ctx, config):\n    return True, ''\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(_minimal_phases(["deps_terminal", f"{mod_name}:always_true"])),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    cfg = load_config()
    assert f"{mod_name}:always_true" in cfg.phases[0].advance_when


@pytest.mark.no_auto_phases
def test_evaluate_calls_external_predicate(tmp_path, monkeypatch):
    import yaml

    mod_name = "mypkg_preds_eval"
    (tmp_path / f"{mod_name}.py").write_text(
        "def always_true(ctx, config):\n    return True, ''\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(_minimal_phases([f"{mod_name}:always_true"])),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    cfg = load_config()
    ph = cfg.phases[0]
    ok, why = evaluate(ph, _ctx(), cfg)
    assert ok is True
    assert why == "ok"


@pytest.mark.no_auto_phases
def test_evaluate_external_predicate_false_reason(tmp_path, monkeypatch):
    import yaml

    mod_name = "mypkg_preds_false"
    (tmp_path / f"{mod_name}.py").write_text(
        "def always_false(ctx, config):\n    return False, 'nope'\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(_minimal_phases([f"{mod_name}:always_false"])),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    cfg = load_config()
    ok, why = evaluate(cfg.phases[0], _ctx(), cfg)
    assert ok is False
    assert why == "nope"


@pytest.mark.parametrize(
    "bad_ref",
    ["bad:", ":attr", "a b:c"],
)
@pytest.mark.no_auto_phases
def test_load_config_rejects_malformed_external_advance_when(
    tmp_path, monkeypatch, bad_ref
):
    import yaml

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(_minimal_phases([bad_ref])),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    with pytest.raises(ConfigError, match="invalid external reference"):
        load_config()


def test_resolve_predicate_errors(tmp_path, monkeypatch):
    from issuesmith.preconditions import resolve_predicate

    with pytest.raises(ConfigError, match="no_such_mod_xyz"):
        resolve_predicate("no_such_mod_xyz:f")

    mod_name = "mypkg_preds_resolve"
    (tmp_path / f"{mod_name}.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    with pytest.raises(ConfigError, match="missing"):
        resolve_predicate(f"{mod_name}:missing")

    mod2 = "mypkg_preds_not_callable"
    (tmp_path / f"{mod2}.py").write_text("NOT_CALLABLE = 1\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not callable"):
        resolve_predicate(f"{mod2}:NOT_CALLABLE")


def test_evaluate_unknown_external_predicate_no_exception(tmp_path, monkeypatch):
    from issuesmith.config import get_config
    from issuesmith.preconditions import resolve_predicate

    mod_name = "mypkg_preds_unknown"
    ref = f"{mod_name}:missing"
    ph = _phase(advance_when=(ref,))
    ok, why = evaluate(ph, _ctx(), get_config())
    assert ok is False
    assert ref in why
    assert why.startswith("unknown predicate")

    keys_before = set(PRECONDITION_REGISTRY)
    try:
        resolve_predicate(ref)
    except ConfigError:
        pass
    assert set(PRECONDITION_REGISTRY) == keys_before
