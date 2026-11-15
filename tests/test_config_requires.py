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
    assert "ac_complete" in GATE_REGISTRY
    assert "deps" in GATE_REGISTRY
    assert "scope" in GATE_REGISTRY
    assert "pr_scope" in GATE_REGISTRY


def test_gate_registry_issue_kinds() -> None:
    assert GATE_REGISTRY["ac_complete"].input_kind == "issue"
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




# ---------------------------------------------------------------------------
# ConfigError raised for unknown gate id in requires (via _build_steps)
# ---------------------------------------------------------------------------






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
                    "requires": ["ac_complete"],
                    "input_kind": "artifact",
                },
            },
        },
    )
    with pytest.raises(ConfigError, match="artifact"):
        validate_step_requires(load_config().steps)








# ---------------------------------------------------------------------------
# validate_requires_chain — ConfigError for missing requires
# ---------------------------------------------------------------------------


def test_validate_requires_chain_ok_when_all_present() -> None:
    from issuesmith.ops.doctor import validate_requires_chain

    steps = {
        "my-step": StepConfig(
            module="issuesmith.steps.foo",
            requires=("ac_complete",),
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






# ---------------------------------------------------------------------------
# AC-1: worktree step can use any combination of gate types (#3671)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# AC-1b: worktree gate rejected for issue/artifact steps, missing gate id message
# ---------------------------------------------------------------------------






# ---------------------------------------------------------------------------
# AC-4: doctor — repair step exempt, unknown gate id FAIL
# ---------------------------------------------------------------------------


def test_validate_requires_chain_repair_step_is_exempt() -> None:
    from issuesmith.ops.doctor import validate_requires_chain

    steps = {
        "repair": StepConfig(module="issuesmith.ops.repair_step"),
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




# ---------------------------------------------------------------------------
# accepts column — unknown gate ids and input_kind (#4804 / sub3)
# ---------------------------------------------------------------------------








def test_worktree_step_with_worktree_gate_in_accepts_is_valid() -> None:
    steps = {
        "wt-step": StepConfig(
            module="issuesmith.steps.foo",
            accepts=("scope",),
            input_kind="worktree",
        ),
    }
    validate_step_requires(steps)


def _write_external_gate_module(tmp_path, mod_name: str, *, pre_llm: bool, repairable: bool) -> str:
    gate_ref = f"{mod_name}:MY_GATE"
    (tmp_path / f"{mod_name}.py").write_text(
        f"""
from issuesmith.gates import GateEntry

class _G:
    def check(self, body, labels):
        return []

MY_GATE = GateEntry(
    input_kind="worktree",
    build=lambda ctx: _G(),
    pre_llm={pre_llm!r},
    repairable={repairable!r},
)
""",
        encoding="utf-8",
    )
    return gate_ref






def test_resolve_gate_errors_and_registry_unchanged(tmp_path, monkeypatch) -> None:
    from issuesmith.gates import GATE_REGISTRY, resolve_gate

    keys_before = set(GATE_REGISTRY)
    with pytest.raises(ConfigError, match="no_such_mod_gates"):
        resolve_gate("no_such_mod_gates:MY_GATE")

    mod_name = "mypkg_gates_resolve"
    (tmp_path / f"{mod_name}.py").write_text("MY_GATE = 42\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    ref = f"{mod_name}:MY_GATE"
    with pytest.raises(ConfigError, match="not a GateEntry"):
        resolve_gate(ref)
    assert set(GATE_REGISTRY) == keys_before


def test_requires_chain_report_external_missing_gate(tmp_path, monkeypatch) -> None:
    from issuesmith.ops.doctor import requires_chain_report

    ref = "mypkg_gates_missing:NO_GATE"
    (tmp_path / "mypkg_gates_missing.py").write_text("pass\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    steps = {
        "p1": StepConfig(
            module="issuesmith.steps.foo",
            requires=(ref,),
            input_kind="worktree",
            requires_declared=True,
        ),
    }
    report = requires_chain_report(steps)
    assert ref in report
