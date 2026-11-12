"""The permission issuesmith hands to ghdag for each engine (#6282)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from ghdag.llm import LLMResult
from ghdag.quota import QuotaSnapshot

from issuesmith.engine import RoleSelection, _execute


@pytest.fixture
def captured_call(monkeypatch):
    captured: dict = {}

    def fake_call(prompt, **kwargs):
        captured.update(kwargs)
        return LLMResult(
            body="done",
            stdout="done",
            stderr="",
            returncode=0,
            engine_used=kwargs["engine"],
            model_used=kwargs["model"],
        )

    monkeypatch.setattr("issuesmith.engine.call", fake_call)
    monkeypatch.setattr(
        "issuesmith.engine.resolve",
        lambda role, tier=None: RoleSelection(engine="claude", model="m"),
    )
    monkeypatch.setattr("issuesmith.engine._record_task_metrics", lambda **kwargs: None)
    monkeypatch.setattr("issuesmith.engine._resolve_timeout_sec", lambda role: 600.0)
    gate = MagicMock()
    gate.snapshot.return_value = QuotaSnapshot(
        engines={}, draining_engines={}, running_tasks={}, limits={}
    )
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: gate)
    return captured


def test_claude_attempt_streams_and_bypasses_permission_prompts(captured_call):
    _execute("implementation", "prompt")

    permission = captured_call["permission"]
    assert permission.permission_mode == "bypassPermissions"
    assert permission.stream is True


def test_codex_and_cursor_attempts_bypass_without_stream_output(captured_call):
    _execute("implementation", "prompt")

    overrides = captured_call["permission_overrides"]
    for engine in ("codex", "cursor"):
        assert overrides[engine].permission_mode == "bypassPermissions"
        assert overrides[engine].stream is False
