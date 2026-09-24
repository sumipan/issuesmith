"""Tests for StepConfig.requires / input_kind validation and doctor requires_chain output.

Acceptance criteria (#3625):
- Step missing requires → ConfigError (via validate_requires_chain)
- Unknown gate id in requires → ConfigError from validate_step_requires, message includes known ids
- input_kind: artifact step cannot use issue gate
- input_kind: worktree step can use worktree gate
- doctor produces requires_chain: ok or requires_chain: <violation>
"""

from __future__ import annotations

import pytest
import yaml

from issuesmith.config import ConfigError, StepConfig, load_config, reset_config_cache
from issuesmith.gates import GATE_REGISTRY, validate_step_requires

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _write_config(tmp_path, monkeypatch, payload: dict) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


# ---------------------------------------------------------------------------
# GATE_REGISTRY
# ---------------------------------------------------------------------------


def test_gate_registry_has_known_ids() -> None:
    assert "m2" in GATE_REGISTRY
    assert "deps" in GATE_REGISTRY
    assert "scope" in GATE_REGISTRY
    assert "pr_scope" in GATE_REGISTRY


def test_gate_registry_issue_kinds() -> None:
    assert GATE_REGISTRY["m2"].input_kind == "issue"
    assert GATE_REGISTRY["deps"].input_kind == "issue"


def test_gate_registry_worktree_kinds() -> None:
    assert GATE_REGISTRY["scope"].input_kind == "worktree"
    assert GATE_REGISTRY["pr_scope"].input_kind == "worktree"


# ---------------------------------------------------------------------------
# StepConfig dataclass
# ---------------------------------------------------------------------------


def test_step_config_default_requires_empty() -> None:
    s = StepConfig(module="issuesmith.steps.foo")
    assert s.requires == ()


def test_step_config_default_input_kind_issue() -> None:
    s = StepConfig(module="issuesmith.steps.foo")
    assert s.input_kind == "issue"


def test_step_config_with_requires_and_input_kind() -> None:
    s = StepConfig(
        module="issuesmith.steps.foo",
        requires=("m2", "deps"),
        input_kind="issue",
    )
    assert s.requires == ("m2", "deps")
    assert s.input_kind == "issue"


# ---------------------------------------------------------------------------
# ConfigError raised for unknown gate id in requires (via _build_steps)
# ---------------------------------------------------------------------------


def test_unknown_gate_id_raises_config_error(tmp_path, monkeypatch) -> None:
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {
                "my-step": {
                    "module": "issuesmith.steps.my_step",
                    "requires": ["nonexistent_gate"],
                    "input_kind": "issue",
                },
            },
        },
    )
    cfg = load_config()  # loading never touches the registry
    with pytest.raises(ConfigError, match="nonexistent_gate"):
        validate_step_requires(cfg.steps)


def test_config_error_message_includes_known_gate_ids(tmp_path, monkeypatch) -> None:
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {
                "my-step": {
                    "module": "issuesmith.steps.my_step",
                    "requires": ["bad_gate"],
                    "input_kind": "issue",
                },
            },
        },
    )
    with pytest.raises(ConfigError) as exc_info:
        validate_step_requires(load_config().steps)
    msg = str(exc_info.value)
    for known_id in GATE_REGISTRY:
        assert known_id in msg, f"expected known gate id '{known_id}' in error: {msg}"


# ---------------------------------------------------------------------------
# ConfigError raised for input_kind mismatch (via _build_steps)
# ---------------------------------------------------------------------------


def test_artifact_step_with_issue_gate_raises_config_error(tmp_path, monkeypatch) -> None:
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {
                "artifact-step": {
                    "module": "issuesmith.steps.my_step",
                    "requires": ["m2"],
                    "input_kind": "artifact",
                },
            },
        },
    )
    with pytest.raises(ConfigError, match="artifact"):
        validate_step_requires(load_config().steps)


def test_issue_step_with_issue_gate_is_valid(tmp_path, monkeypatch) -> None:
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {
                "issue-step": {
                    "module": "issuesmith.steps.my_step",
                    "requires": ["m2", "deps"],
                    "input_kind": "issue",
                },
            },
        },
    )
    cfg = load_config()
    assert cfg.steps["issue-step"].requires == ("m2", "deps")
    assert cfg.steps["issue-step"].input_kind == "issue"


def test_worktree_step_with_worktree_gate_is_valid(tmp_path, monkeypatch) -> None:
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {
                "wt-step": {
                    "module": "issuesmith.steps.my_step",
                    "requires": ["scope"],
                    "input_kind": "worktree",
                },
            },
        },
    )
    cfg = load_config()
    assert cfg.steps["wt-step"].requires == ("scope",)
    assert cfg.steps["wt-step"].input_kind == "worktree"


def test_worktree_step_with_issue_gate_is_valid(tmp_path, monkeypatch) -> None:
    # Design change (#3671): issue gates can be used in worktree steps
    # (body + labels are always readable regardless of input_kind).
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {
                "wt-step": {
                    "module": "issuesmith.steps.my_step",
                    "requires": ["m2"],
                    "input_kind": "worktree",
                },
            },
        },
    )
    cfg = load_config()
    assert cfg.steps["wt-step"].requires == ("m2",)
    assert cfg.steps["wt-step"].input_kind == "worktree"


# ---------------------------------------------------------------------------
# validate_requires_chain — ConfigError for missing requires
# ---------------------------------------------------------------------------


def test_validate_requires_chain_ok_when_all_present() -> None:
    from issuesmith.ops.doctor import validate_requires_chain

    steps = {
        "my-step": StepConfig(
            module="issuesmith.steps.foo",
            requires=("m2",),
            input_kind="issue",
        ),
    }
    violations = validate_requires_chain(steps)
    assert violations == []


def test_validate_requires_chain_reports_missing_requires() -> None:
    from issuesmith.ops.doctor import validate_requires_chain

    steps = {
        "bare-step": StepConfig(module="issuesmith.steps.foo"),
    }
    violations = validate_requires_chain(steps)
    assert len(violations) == 1
    assert "bare-step" in violations[0]
    assert "requires" in violations[0]


def test_validate_requires_chain_no_violations_for_empty_steps() -> None:
    from issuesmith.ops.doctor import validate_requires_chain

    violations = validate_requires_chain({})
    assert violations == []


# ---------------------------------------------------------------------------
# doctor.main() output — requires_chain line
# ---------------------------------------------------------------------------


def test_doctor_requires_chain_ok(tmp_path, monkeypatch, capsys) -> None:
    from issuesmith.ops.doctor import requires_chain_report

    steps = {
        "valid-step": StepConfig(
            module="issuesmith.steps.foo",
            requires=("m2",),
            input_kind="issue",
        ),
    }
    report = requires_chain_report(steps)
    assert report == "requires_chain: ok"


def test_doctor_requires_chain_violation(tmp_path, monkeypatch, capsys) -> None:
    from issuesmith.ops.doctor import requires_chain_report

    steps = {
        "no-requires-step": StepConfig(module="issuesmith.steps.foo"),
    }
    report = requires_chain_report(steps)
    assert report.startswith("requires_chain:")
    assert "ok" not in report
    assert "no-requires-step" in report


# ---------------------------------------------------------------------------
# AC-1: worktree step can use any combination of gate types (#3671)
# ---------------------------------------------------------------------------


def test_worktree_step_with_mixed_gates_is_valid(tmp_path, monkeypatch) -> None:
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {
                "wt-step": {
                    "module": "issuesmith.steps.my_step",
                    "requires": [
                        "cp1", "scope_breadth", "base_freshness",
                        "lint", "external_leak", "scope_coupling", "milestone_consistency",
                    ],
                    "input_kind": "worktree",
                },
            },
        },
    )
    cfg = load_config()
    assert "cp1" in cfg.steps["wt-step"].requires
    assert "base_freshness" in cfg.steps["wt-step"].requires


# ---------------------------------------------------------------------------
# AC-1b: worktree gate rejected for issue/artifact steps, missing gate id message
# ---------------------------------------------------------------------------


def test_issue_step_with_worktree_gate_raises_config_error(tmp_path, monkeypatch) -> None:
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {
                "issue-step": {
                    "module": "issuesmith.steps.my_step",
                    "requires": ["lint"],
                    "input_kind": "issue",
                },
            },
        },
    )
    with pytest.raises(ConfigError, match="lint"):
        validate_step_requires(load_config().steps)


def test_config_error_missing_gate_ids_in_message(tmp_path, monkeypatch) -> None:
    _write_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "example/app",
            "steps": {
                "bad-step": {
                    "module": "issuesmith.steps.my_step",
                    "requires": ["nonexistent_gate"],
                    "input_kind": "issue",
                },
            },
        },
    )
    with pytest.raises(ConfigError) as exc_info:
        validate_step_requires(load_config().steps)
    msg = str(exc_info.value)
    assert "missing gate ids" in msg
    assert "nonexistent_gate" in msg


# ---------------------------------------------------------------------------
# AC-4: doctor — repair step exempt, unknown gate id FAIL
# ---------------------------------------------------------------------------


def test_validate_requires_chain_repair_step_is_exempt() -> None:
    from issuesmith.ops.doctor import validate_requires_chain

    steps = {
        "repair": StepConfig(module="issuesmith.steps.repair"),
    }
    violations = validate_requires_chain(steps)
    assert violations == []


def test_validate_requires_chain_missing_gate_id_is_violation() -> None:
    from issuesmith.ops.doctor import validate_requires_chain

    steps = {
        "some-step": StepConfig(
            module="issuesmith.steps.foo",
            requires=("nonexistent_gate_xyz",),
            input_kind="issue",
        ),
    }
    violations = validate_requires_chain(steps)
    assert any("nonexistent_gate_xyz" in v for v in violations)
    assert any("missing gate ids" in v for v in violations)


def test_doctor_requires_chain_ok_with_all_registry_ids() -> None:
    from issuesmith.ops.doctor import requires_chain_report

    steps = {}
    for gate_id in GATE_REGISTRY:
        kind = GATE_REGISTRY[gate_id].input_kind
        if kind == "worktree":
            step_kind = "worktree"
        elif kind == "issue":
            step_kind = "issue"
        else:
            step_kind = "artifact"
        steps[f"step-{gate_id}"] = StepConfig(
            module="issuesmith.steps.foo",
            requires=(gate_id,),
            input_kind=step_kind,
        )
    report = requires_chain_report(steps)
    assert report == "requires_chain: ok"
