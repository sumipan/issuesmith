"""Regression tests for issuesmith LLM capabilities passed to call_managed (#6282)."""

from __future__ import annotations

import json
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from ghdag.core.models.metrics import FailureClass
from ghdag.llm import LLMParseError, call_managed
from ghdag.llm import managed as llm_managed
from ghdag.llm.engines import LLMResult
from ghdag.quota import QuotaSnapshot

from issuesmith.engine import (
    _DEFAULT_CAPABILITIES,
    _ISSUESMITH_CAPABILITIES,
    RoleSelection,
    _execute,
    _issuesmith_call,
)


def _claude_stream_jsonl(
    result_text: str,
    *,
    usage: dict[str, int] | None = None,
) -> str:
    payload: dict[str, object] = {
        "type": "result",
        "subtype": "success",
        "result": result_text,
    }
    if usage is not None:
        payload["usage"] = usage
    lines = [
        '{"type":"system","subtype":"init"}',
        json.dumps(payload, ensure_ascii=False),
    ]
    return "\n".join(lines) + "\n"


def _codex_agent_jsonl(message: str) -> str:
    item = {
        "type": "item.completed",
        "item": {"type": "agent_message", "text": message},
    }
    return json.dumps(item, ensure_ascii=False) + "\n"


def _validated_call(raw_stdout: str, **kwargs) -> LLMResult:
    engine = kwargs.get("engine", "claude")
    caps = kwargs["capabilities"]
    llm = LLMResult(stdout=raw_stdout, stderr="", returncode=0)
    return llm.validate(caps, engine=engine)


@pytest.fixture
def wired_call_managed(monkeypatch):
    """Route call_managed through _issuesmith_call with a synthetic ghdag call()."""

    @contextmanager
    def _wire(fake_call):
        monkeypatch.setattr("issuesmith.engine._ghdag_call", fake_call)
        original = llm_managed.call
        llm_managed.call = _issuesmith_call
        try:
            yield
        finally:
            llm_managed.call = original

    return _wire


def test_issuesmith_capabilities_use_text_stream():
    """Issue #6282: Claude outer capabilities are text + stream-json."""
    assert _ISSUESMITH_CAPABILITIES.output_format == "text"
    assert _ISSUESMITH_CAPABILITIES.permission_mode == "bypassPermissions"
    assert _ISSUESMITH_CAPABILITIES.stream is True


def test_claude_stream_json_prose_preserves_status_marker(wired_call_managed):
    prose = "Verdict: PASS\n\nPIPELINE_STATUS: CP2_PASS"
    jsonl = _claude_stream_jsonl(prose)

    def fake_call(_prompt, **kwargs):
        assert kwargs["capabilities"] is _ISSUESMITH_CAPABILITIES
        return _validated_call(jsonl, **kwargs)

    with wired_call_managed(fake_call):
        managed = call_managed(
            "review",
            engine="claude",
            model="claude-sonnet-4-6",
            capabilities=_ISSUESMITH_CAPABILITIES,
        )

    assert managed.returncode == 0
    assert "PIPELINE_STATUS: CP2_PASS" in managed.body.splitlines()


def test_codex_fallback_jsonl_preserves_status_marker(
    wired_call_managed, monkeypatch
):
    message = "Verdict: PASS\n\nPIPELINE_STATUS: CP2_PASS"
    codex_jsonl = _codex_agent_jsonl(message)
    calls: list[str] = []

    def fake_call(_prompt, **kwargs):
        engine = kwargs.get("engine", "claude")
        calls.append(engine)
        if engine == "claude":
            return LLMResult(
                stdout="",
                stderr="quota exceeded",
                returncode=1,
                failure_class=FailureClass.QUOTA_EXHAUSTED,
            )
        assert kwargs["capabilities"] is _DEFAULT_CAPABILITIES
        return _validated_call(codex_jsonl, **kwargs)

    with wired_call_managed(fake_call):
        managed = call_managed(
            "review",
            engine="claude",
            model="claude-sonnet-4-6",
            capabilities=_ISSUESMITH_CAPABILITIES,
            fallback_candidates=[("codex", "gpt-5.5")],
        )

    assert calls == ["claude", "codex"]
    assert managed.returncode == 0
    assert managed.engine_used == "codex"
    assert "PIPELINE_STATUS: CP2_PASS" in managed.body.splitlines()


def test_claude_stream_json_preserves_token_usage(monkeypatch, wired_call_managed):
    usage = {"input_tokens": 100, "output_tokens": 50}
    jsonl = _claude_stream_jsonl("ok", usage=usage)
    recorded: list[dict] = []

    monkeypatch.setattr(
        "issuesmith.engine.resolve",
        lambda role, tier=None: RoleSelection(
            engine="claude", model="claude-sonnet-4-6"
        ),
    )
    monkeypatch.setattr(
        "issuesmith.engine._record_task_metrics",
        lambda **kwargs: recorded.append(kwargs),
    )
    monkeypatch.setattr("issuesmith.engine._resolve_timeout_sec", lambda role: 600.0)
    empty_snap = QuotaSnapshot(
        engines={},
        draining_engines={},
        running_tasks={},
        limits={},
    )
    qg = MagicMock()
    qg.snapshot.return_value = empty_snap
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: qg)

    def fake_call(_prompt, **kwargs):
        return _validated_call(jsonl, **kwargs)

    with wired_call_managed(fake_call):
        proc = _execute("design", "prompt")

    assert proc.returncode == 0
    assert recorded
    assert recorded[-1]["usage"] is not None
    assert recorded[-1]["usage"].token_count == 150


def test_claude_stream_json_without_result_returns_failed_attempt(
    wired_call_managed,
):
    incomplete = '{"type":"system","subtype":"init"}\n'

    def fake_call(_prompt, **kwargs):
        return LLMResult(stdout=incomplete, stderr="agent failed", returncode=1)

    with wired_call_managed(fake_call):
        try:
            managed = call_managed(
                "review",
                engine="claude",
                model="claude-sonnet-4-6",
                capabilities=_ISSUESMITH_CAPABILITIES,
            )
        except LLMParseError:
            pytest.fail("call_managed must not propagate LLMParseError")

    assert managed.returncode != 0
    assert managed.body.strip() != "Verdict: PASS"
