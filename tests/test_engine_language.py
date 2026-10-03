"""The pre-LLM gate andon summary comes from the language pack (nexus #4471)."""
from __future__ import annotations

import dataclasses
from unittest.mock import MagicMock, patch

import pytest
import yaml
from ghdag.workflow.gates import Violation

from issuesmith import config as config_module
from issuesmith.config import StepConfig, reset_config_cache
from issuesmith.language import EN, LanguagePack, load_language_pack


def _install_pack(tmp_path, monkeypatch, messages: dict[str, str]) -> None:
    data = {}
    for f in dataclasses.fields(LanguagePack):
        value = getattr(EN, f.name)
        if isinstance(value, tuple):
            value = list(value)
        elif not isinstance(value, str):
            value = dict(value)
        data[f.name] = value
    data["messages"] = {**data["messages"], **messages}
    path = tmp_path / "pack.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    monkeypatch.setattr(config_module, "EN", load_language_pack(path))
    reset_config_cache()


@pytest.fixture(autouse=True)
def _reset_config():
    reset_config_cache()
    yield
    reset_config_cache()


def _run_blocked_pre_gate(capsys):
    from issuesmith.engine import _run_pre_gate_phase

    violation = Violation(
        rule_id="scope_breadth.too_large",
        severity="fail",
        message="scope too large",
        location=None,
        auto_fixable=False,
        fix_hint=None,
    )
    gate = MagicMock()
    gate.check.return_value = [violation]
    step_cfg = StepConfig(
        module="", requires=("scope_breadth",), requires_declared=True, input_kind="issue"
    )
    context = {"issue_number": "42", "workflow_name": "issuesmith"}
    with (
        patch("issuesmith.engine._build_pre_gates", return_value={"scope_breadth": gate}),
        patch("issuesmith.ops.dispatch.fetch_issue_inputs", return_value=("BODY", [])),
        patch("issuesmith.engine.get_forge", return_value=MagicMock()),
        patch("issuesmith.engine._raise_andon") as mock_andon,
    ):
        rc = _run_pre_gate_phase(step_cfg, "p1", context, "IMPL_FAILED")
    assert rc == 1
    mock_andon.assert_called_once()
    return mock_andon.call_args[0][1], capsys.readouterr().out


def test_pre_llm_andon_summary_is_english_with_default_pack(capsys):
    andon, out = _run_blocked_pre_gate(capsys)
    assert andon.summary == "pre-LLM gate violation in step p1: scope too large"
    assert andon.summary in out
    assert "PIPELINE_STATUS: IMPL_FAILED" in out


def test_pre_llm_andon_summary_uses_custom_pack(capsys, tmp_path, monkeypatch):
    _install_pack(
        tmp_path,
        monkeypatch,
        {"engine.pre_llm_gate_violation": "gate stop before LLM at {step} -> {messages}"},
    )
    andon, out = _run_blocked_pre_gate(capsys)
    assert andon.summary == "gate stop before LLM at p1 -> scope too large"
    assert andon.options == ["split", "reject"]
    assert "PIPELINE_STATUS: IMPL_FAILED" in out
