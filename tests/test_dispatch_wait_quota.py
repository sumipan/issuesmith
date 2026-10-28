"""dispatch: RetrySignal -> PIPELINE_STATUS: WAIT_QUOTA (sumipan/nexus#3515, #5757)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from ghdag.dag._util import WaitQuotaSignal, parse_wait_quota_signal
from ghdag.llm import list_engines

from issuesmith.engine import ROLE_ENGINES, RetryReason, RetrySignal
from issuesmith.ops import dispatch as dispatch_mod

_KNOWN = frozenset(list_engines())


def _parse(out: str) -> WaitQuotaSignal:
    parsed = parse_wait_quota_signal(out, known_engines=_KNOWN)
    assert isinstance(parsed, WaitQuotaSignal), parsed
    return parsed


def test_retry_signal_prints_wait_quota_that_ghdag_parses(capsys):
    after = datetime.now(timezone.utc) + timedelta(minutes=30)
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=after, role="design")

    assert dispatch_mod._handle_retry_signal(sig, "p1", None, task_uuid="task-123") == 0

    out = capsys.readouterr().out
    assert "PIPELINE_STATUS: DEFERRED" not in out
    parsed = _parse(out)
    assert parsed.after == after
    assert set(parsed.engines) == set(ROLE_ENGINES["design"])


def test_retry_signal_without_after_leaves_after_empty(capsys):
    sig = RetrySignal(reason=RetryReason.RATE_LIMITED, after=None, role="implementation")

    assert dispatch_mod._handle_retry_signal(sig, "impl", None, task_uuid="t") == 0

    parsed = _parse(capsys.readouterr().out)
    assert parsed.after is None
    assert set(parsed.engines) == set(ROLE_ENGINES["implementation"])


def test_naive_after_is_taken_as_utc(capsys):
    naive = datetime(2026, 10, 28, 3, 0, 0)
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=naive, role="design")

    assert dispatch_mod._handle_retry_signal(sig, "p1", None, task_uuid="t") == 0

    assert _parse(capsys.readouterr().out).after == naive.replace(tzinfo=timezone.utc)


@pytest.mark.parametrize("role", ["", "unknown-role"])
def test_empty_or_unknown_role_exits_without_wait_quota(role, capsys):
    sig = RetrySignal(reason=RetryReason.BRAKE_PAUSED, after=None, role=role)

    rc = dispatch_mod._handle_retry_signal(sig, "cp2", None, task_uuid="t")

    captured = capsys.readouterr()
    assert rc == 1
    assert "PIPELINE_STATUS: WAIT_QUOTA" not in captured.out
    assert "ERROR" in captured.err


def test_task_uuid_read_from_ghdag_env(monkeypatch, capsys):
    monkeypatch.setenv("GHDAG_TASK_UUID", "env-uuid")
    sig = RetrySignal(reason=RetryReason.RATE_LIMITED, after=None, role="implementation")

    dispatch_mod._handle_retry_signal(sig, "impl", None)

    captured = capsys.readouterr()
    assert "uuid=env-uuid" in captured.err
    _parse(captured.out)


def test_retry_signal_keeps_role():
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="design")
    assert sig.role == "design"


def test_handle_retry_signal_does_not_construct_quota_gate(monkeypatch, capsys):
    constructed: list[object] = []

    class _ExplodingGate:
        def __init__(self, *args, **kwargs):
            constructed.append(self)
            raise AssertionError("QuotaGate must not be constructed in dispatch retry handling")

    monkeypatch.setattr("ghdag.quota.QuotaGate", _ExplodingGate)
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="design")

    assert dispatch_mod._handle_retry_signal(sig, "p1", None, task_uuid="t") == 0
    assert constructed == []
    assert "PIPELINE_STATUS: WAIT_QUOTA" in capsys.readouterr().out


class _LabelForge:
    """In-memory forge holding one Issue's labels."""

    def __init__(self, labels: set[str]) -> None:
        self.labels = set(labels)

    def issue_get(self, number, fields=None):
        return {"number": number, "labels": [{"name": lb} for lb in sorted(self.labels)]}

    def issue_update(self, number, **kwargs):
        self.labels |= set(kwargs.get("labels_add") or [])
        self.labels -= set(kwargs.get("labels_remove") or [])


def test_retry_projects_waiting_and_next_start_clears_it(monkeypatch, capsys):
    """retry adds <ns>:waiting through the projection; the next step start removes it (#4807)."""
    from issuesmith.config import get_config

    ns = get_config().label_namespace
    forge = _LabelForge({f"{ns}:develop-running"})
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="implementation")
    with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
        assert dispatch_mod._handle_retry_signal(sig, "cp2", 42, task_uuid="t-1") == 0
        assert forge.labels == {f"{ns}:develop-running", f"{ns}:waiting"}

        with patch("issuesmith.ops.dispatch._try_python_step", return_value=0):
            assert dispatch_mod.main(["cp2", "issue_number=42"]) == 0
    assert forge.labels == {f"{ns}:develop-running"}
    assert "PIPELINE_STATUS: WAIT_QUOTA" in capsys.readouterr().out


def test_retry_without_issue_number_writes_no_label(capsys):
    forge = MagicMock()
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="design")
    with patch("issuesmith.ops.dispatch.get_forge", return_value=forge):
        assert dispatch_mod._handle_retry_signal(sig, "p1", None, task_uuid="t-2") == 0
    forge.issue_get.assert_not_called()
    forge.issue_update.assert_not_called()


def test_main_returns_nonzero_for_invalid_role(monkeypatch, capsys):
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="")
    monkeypatch.setattr(dispatch_mod, "_try_python_step", MagicMock(side_effect=sig))

    rc = dispatch_mod.main(["cp2", "issue_number=42"])

    captured = capsys.readouterr()
    assert rc == 1
    assert "PIPELINE_STATUS: WAIT_QUOTA" not in captured.out
