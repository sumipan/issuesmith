"""Refs sumipan/issuesmith#7320 — GitHub env stripping for implementation LLM calls."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from ghdag.llm import LLMResult
from ghdag.quota import EngineQuotaState, QuotaSnapshot

from issuesmith.engine import (
    _LLM_GITHUB_ENV_DENY,
    RoleSelection,
    _execute,
    _run_guarded_order,
)

_T0 = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

_GITHUB_ENV = {
    "GITHUB_TOKEN": "tok",
    "GH_TOKEN": "gh",
    "GITHUB_REPOSITORIES": "org/repo",
    "GITHUB_REPOSITORY": "org/repo",
}


def _available():
    return EngineQuotaState(
        status="available",
        observed_at=_T0,
        resume_at=None,
        reason=None,
    )


def _snap(engines):
    return QuotaSnapshot(
        engines=engines,
        draining_engines={},
        running_tasks={},
        limits={},
    )


def _result(engine="claude", *, rc=0, body="ok", failure_class=None):
    return LLMResult(
        stdout="",
        stderr="",
        body=body,
        usage=None,
        returncode=rc,
        failure_class=failure_class,
        engine_used=engine,
        model_used="m",
        attempts=1,
        quota_reported=False,
        additional_tags={},
    )


@pytest.fixture
def exe_mocks(monkeypatch):
    monkeypatch.setattr(
        "issuesmith.engine.resolve",
        lambda role, tier=None: RoleSelection(engine="claude", model="m"),
    )
    monkeypatch.setattr("issuesmith.engine._record_task_metrics", lambda **kwargs: None)
    monkeypatch.setattr("issuesmith.engine._resolve_timeout_sec", lambda role: 600.0)
    qg = MagicMock()
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: qg)
    snap = _snap({"claude": _available(), "codex": _available()})
    qg.snapshot.return_value = snap
    return {"quota_gate": qg}


def _set_github_env(monkeypatch):
    for key, value in _GITHUB_ENV.items():
        monkeypatch.setenv(key, value)


def test_implementation_call_has_no_github_env(exe_mocks, monkeypatch):
    """Refs #7320 — implementation strips GitHub env for the first LLM call."""
    _set_github_env(monkeypatch)
    captured: list[dict[str, str]] = []

    def _record_env(*_args, **_kwargs):
        captured.append(dict(os.environ))
        return _result()

    cm = MagicMock(side_effect=_record_env)
    monkeypatch.setattr("issuesmith.engine.call", cm)

    _execute("implementation", "prompt")

    assert len(captured) == 1
    for name in _LLM_GITHUB_ENV_DENY:
        assert name not in captured[0]


def test_implementation_retry_call_has_no_github_env(exe_mocks, monkeypatch):
    """Refs #7320 — rate-limit retry also runs without GitHub env."""
    _set_github_env(monkeypatch)
    captured: list[dict[str, str]] = []

    def _record_env(*_args, **_kwargs):
        captured.append(dict(os.environ))
        return _result()

    rate_limited = _result("claude", rc=1, body="fail")

    def _side(*_args, **_kwargs):
        captured.append(dict(os.environ))
        if len(captured) == 1:
            return rate_limited
        return _result("codex")

    monkeypatch.setattr("issuesmith.engine.call", MagicMock(side_effect=_side))
    monkeypatch.setattr("issuesmith.engine._is_rate_limited", lambda _text: True)
    monkeypatch.setattr(
        "issuesmith.engine._rate_limit_evidence", lambda _text: "rate limit"
    )

    _execute("implementation", "prompt")

    assert len(captured) == 2
    for snap in captured:
        for name in _LLM_GITHUB_ENV_DENY:
            assert name not in snap


def test_github_env_restored_after_call(exe_mocks, monkeypatch):
    """Refs #7320 — engine process env is restored after LLM call (or timeout)."""
    _set_github_env(monkeypatch)

    def _timeout(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="llm", timeout=1)

    monkeypatch.setattr("issuesmith.engine.call", MagicMock(side_effect=_timeout))

    _execute("implementation", "prompt")

    for key, value in _GITHUB_ENV.items():
        assert os.environ.get(key) == value


def test_design_call_keeps_github_env(exe_mocks, monkeypatch):
    """Refs #7320 — design role keeps GitHub credentials for order work."""
    _set_github_env(monkeypatch)
    captured: dict[str, str] = {}

    def _record_env(*_args, **_kwargs):
        captured.update(os.environ)
        return _result()

    monkeypatch.setattr("issuesmith.engine.call", MagicMock(side_effect=_record_env))

    _execute("design", "prompt")

    assert captured.get("GITHUB_TOKEN") == "tok"


def test_issue_body_file_holds_body_only_during_call(
    exe_mocks, monkeypatch, tmp_path: Path
) -> None:
    """Refs #7320 — issue body file exists only for the LLM subprocess."""
    order = tmp_path / "order.md"
    order.write_text("body at ${issue_body_file}\n", encoding="utf-8")
    seen_paths: list[Path] = []
    seen_during_call: dict[str, str] = {}

    def _fake_fetch(_context):
        return "BODY", []

    monkeypatch.setattr(
        "issuesmith.ops.dispatch.fetch_issue_inputs", _fake_fetch
    )

    def _mkstemp_in_tmp(*_args, **_kwargs):
        p = tmp_path / "issuesmith-issue-body.md"
        fd = os.open(p, os.O_CREAT | os.O_RDWR)
        return fd, str(p)

    monkeypatch.setattr(tempfile, "mkstemp", _mkstemp_in_tmp)

    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = "PIPELINE_STATUS: IMPL_DONE\n"
    proc.stderr = ""

    def _fake_execute(_role, content, **_kwargs):
        match = re.search(r"(/\S*issuesmith-issue-\S+\.md)", content)
        if match:
            p = Path(match.group(1))
            if p.is_file():
                seen_paths.append(p)
                seen_during_call["content"] = p.read_text(encoding="utf-8")
        return proc

    monkeypatch.setattr("issuesmith.engine._execute", _fake_execute)

    _run_guarded_order(
        "implementation",
        str(order),
        ["issue_number=7"],
        ["IMPL_DONE"],
        "IMPL_FAILED",
    )

    assert seen_during_call.get("content") == "BODY"
    assert seen_paths
    assert not seen_paths[0].is_file()


def test_empty_issue_body_does_not_start_llm(
    exe_mocks, monkeypatch, tmp_path: Path, capsys
) -> None:
    """Refs #7320 — empty fetched body skips LLM and reports failure."""
    order = tmp_path / "order.md"
    order.write_text("${issue_body_file}\n", encoding="utf-8")

    monkeypatch.setattr(
        "issuesmith.ops.dispatch.fetch_issue_inputs",
        lambda _ctx: ("", []),
    )
    call_mock = MagicMock()
    monkeypatch.setattr("issuesmith.engine.call", call_mock)
    exec_mock = MagicMock()
    monkeypatch.setattr("issuesmith.engine._execute", exec_mock)

    rc, _stdout = _run_guarded_order(
        "implementation",
        str(order),
        ["issue_number=7"],
        ["IMPL_DONE"],
        "IMPL_FAILED",
    )

    assert rc == 1
    exec_mock.assert_not_called()
    call_mock.assert_not_called()
    out = capsys.readouterr().out
    from ghdag.core import reply_block

    block = reply_block.parse_reply_block(out)
    assert block is not None and block.status == "failed" and block.code == "IMPL"
    assert "REASON: issue body fetch failed for #7" in out
