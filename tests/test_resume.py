"""Tests for issuesmith.resume — central resume entry point (#3509)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_store_mock():
    store = MagicMock()
    store.remove_in_flight.return_value = None
    enqueue_result = MagicMock()
    enqueue_result.request_id = "req-123"
    enqueue_result.created = True
    store.enqueue.return_value = enqueue_result
    return store


def _make_client_mock(labels=None):
    client = MagicMock()
    client.issue_get.return_value = {
        "labels": [{"name": lbl} for lbl in (labels or [])]
    }
    return client


# ---------------------------------------------------------------------------
# ValueError when no arguments
# ---------------------------------------------------------------------------

def test_resume_raises_without_args():
    from issuesmith.resume import resume
    with pytest.raises(ValueError, match="from_step"):
        resume(123)


# ---------------------------------------------------------------------------
# from_step path: in_flight release + ghdag recover
# ---------------------------------------------------------------------------


def test_resume_from_step_returns_1_when_ghdag_unavailable(capsys):
    from issuesmith.resume import resume
    with (
        patch("issuesmith.resume.QueueStore") as mock_store_cls,
        patch("issuesmith.resume._generation_keys_available", return_value=False),
    ):
        mock_store_cls.return_value = _make_store_mock()
        rc = resume(123, from_step="cp2")
    assert rc == 1
    captured = capsys.readouterr()
    assert "not available" in captured.err


# ---------------------------------------------------------------------------
# phase path: in_flight release + label reset + enqueue
# ---------------------------------------------------------------------------


def test_resume_phase_calls_apply_redispatch_labels():
    store = _make_store_mock()
    client = _make_client_mock(labels=["issuesmith:develop-running"])
    captured: list[tuple] = []

    def fake_labels(*args, **kwargs):
        captured.append(args)
        return None

    with (
        patch("issuesmith.resume.QueueStore", return_value=store),
        patch("issuesmith.resume.get_forge", return_value=client),
        patch("issuesmith.resume.get_config") as mock_cfg,
        patch("issuesmith.resume.apply_redispatch_labels", side_effect=fake_labels),
    ):
        mock_cfg.return_value.repo = "sumipan/issuesmith"
        from issuesmith.resume import resume
        resume(123, phase="develop")
    assert captured[0][1] == 123
    assert captured[0][2] == "develop"


def test_cmd_resume_both_flags_exits_2():
    """AC-3: --from and --phase together is argparse error, exits 2."""
    from issuesmith.cli import _cmd_resume
    with pytest.raises(SystemExit) as exc_info:
        _cmd_resume(["123", "--from", "cp2", "--phase", "develop"])
    assert exc_info.value.code == 2


def test_cmd_resume_no_flags_exits_2():
    """AC-4: neither --from nor --phase exits 2."""
    from issuesmith.cli import _cmd_resume
    with pytest.raises(SystemExit) as exc_info:
        _cmd_resume(["123"])
    assert exc_info.value.code == 2


# ---------------------------------------------------------------------------
# held in_flight entries are dropped by resume and not carried over (#4816)
# ---------------------------------------------------------------------------

def _held_store(tmp_path):
    from issuesmith.queue_store import QueueStore

    store = QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )
    store.add_in_flight(123, "claude", role="implementation", phase="develop")
    assert store.hold_in_flight(123, by="wf:123:p1:0") is True
    return store


def _assert_redispatch_has_no_held(store):
    assert store.snapshot().in_flight == []
    store.add_in_flight(123, "claude", role="implementation", phase="develop")
    [entry] = store.snapshot().in_flight
    assert "held" not in entry


