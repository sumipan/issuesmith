"""dispatch: RetrySignal -> PIPELINE_STATUS: WAIT_QUOTA (sumipan/nexus#3515, #5757)."""
from __future__ import annotations

from datetime import datetime, timezone

from ghdag.dag import WaitQuotaSignal, parse_wait_quota_signal
from ghdag.llm import list_engines

from issuesmith.engine import ROLE_ENGINES, RetryReason, RetrySignal
from issuesmith.ops import dispatch as dispatch_mod

_KNOWN = frozenset(list_engines())


def _parse(out: str) -> WaitQuotaSignal:
    parsed = parse_wait_quota_signal(out, known_engines=_KNOWN)
    assert isinstance(parsed, WaitQuotaSignal), parsed
    return parsed


def test_naive_after_is_taken_as_utc(capsys):
    naive = datetime(2026, 10, 28, 3, 0, 0)
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=naive, role="design")

    dispatch_mod._handle_retry_signal(sig, "p1", None, task_uuid="t")

    assert _parse(capsys.readouterr().out).after == naive.replace(tzinfo=timezone.utc)


def test_unknown_role_pauses_every_configured_engine(capsys):
    sig = RetrySignal(reason=RetryReason.BRAKE_PAUSED, after=None, role="")

    dispatch_mod._handle_retry_signal(sig, "cp2", None, task_uuid="t")

    expected = {engine for engines in ROLE_ENGINES.values() for engine in engines}
    assert set(_parse(capsys.readouterr().out).engines) == expected


def test_retry_signal_keeps_role():
    sig = RetrySignal(reason=RetryReason.QUOTA_PAUSED, after=None, role="design")
    assert sig.role == "design"


class _LabelForge:
    """In-memory forge holding one Issue's labels."""

    def __init__(self, labels: set[str]) -> None:
        self.labels = set(labels)

    def issue_get(self, number, fields=None):
        return {"number": number, "labels": [{"name": lb} for lb in sorted(self.labels)]}

    def issue_update(self, number, **kwargs):
        self.labels |= set(kwargs.get("labels_add") or [])
        self.labels -= set(kwargs.get("labels_remove") or [])


