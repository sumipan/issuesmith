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

def test_resume_from_step_releases_in_flight():
    store = _make_store_mock()
    with (
        patch("issuesmith.resume.QueueStore", return_value=store),
        patch("issuesmith.resume._resume_from_step", return_value=0),
    ):
        from issuesmith.resume import resume
        resume(123, from_step="cp2")
    store.remove_in_flight.assert_called_once_with(123)


def test_resume_from_step_calls_ghdag_recover():
    from issuesmith.resume import resume
    captured: list[tuple] = []

    def fake_recover(*args, **kwargs):
        captured.append((args, kwargs))
        return 0

    with (
        patch("issuesmith.resume.QueueStore") as mock_store_cls,
        patch("issuesmith.resume._generation_keys_available", return_value=True),
        patch("issuesmith.resume._run_ghdag_recover", side_effect=fake_recover),
    ):
        mock_store_cls.return_value = _make_store_mock()
        rc = resume(123, from_step="m1")
    assert rc == 0
    assert captured[0][0][0] == 123
    assert captured[0][0][2] == "m1"


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

def test_resume_phase_releases_in_flight():
    store = _make_store_mock()
    client = _make_client_mock()
    with (
        patch("issuesmith.resume.QueueStore", return_value=store),
        patch("issuesmith.resume.get_forge", return_value=client),
        patch("issuesmith.resume.get_config") as mock_cfg,
        patch("issuesmith.resume.apply_redispatch_labels"),
    ):
        mock_cfg.return_value.repo = "sumipan/issuesmith"
        from issuesmith.resume import resume
        resume(123, phase="develop")
    store.remove_in_flight.assert_called_once_with(123)


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


def test_resume_phase_enqueues_issue():
    store = _make_store_mock()
    client = _make_client_mock()
    captured: list[dict] = []

    def fake_enqueue(**kwargs):
        captured.append(kwargs)
        return store.enqueue.return_value

    store.enqueue.side_effect = fake_enqueue
    with (
        patch("issuesmith.resume.QueueStore", return_value=store),
        patch("issuesmith.resume.get_forge", return_value=client),
        patch("issuesmith.resume.get_config") as mock_cfg,
        patch("issuesmith.resume.apply_redispatch_labels"),
    ):
        mock_cfg.return_value.repo = "sumipan/issuesmith"
        from issuesmith.resume import resume
        rc = resume(123, phase="merge")
    assert rc == 0
    assert captured[0]["issue"] == 123
    assert captured[0]["phase"] == "merge"


def test_resume_phase_prints_request_id(capsys):
    store = _make_store_mock()
    client = _make_client_mock()
    with (
        patch("issuesmith.resume.QueueStore", return_value=store),
        patch("issuesmith.resume.get_forge", return_value=client),
        patch("issuesmith.resume.get_config") as mock_cfg,
        patch("issuesmith.resume.apply_redispatch_labels"),
    ):
        mock_cfg.return_value.repo = "sumipan/issuesmith"
        from issuesmith.resume import resume
        resume(123, phase="develop")
    captured = capsys.readouterr()
    assert "req-123" in captured.out


# ---------------------------------------------------------------------------
# recovery.cmd_redispatch emits FutureWarning and delegates to resume
# ---------------------------------------------------------------------------

def _mock_unblocked_plan():
    from issuesmith import recovery as rec
    return rec.Plan(
        action="redispatch",
        reason="test",
        command="issuesmith redispatch 123 --phase develop",
        required_labels=frozenset(),
        blocked_by=None,
    )


def test_cmd_redispatch_emits_future_warning():
    import warnings

    from issuesmith.recovery import cmd_redispatch
    store = _make_store_mock()
    client = MagicMock()
    client.issue_get.return_value = {"labels": []}
    with (
        patch("issuesmith.recovery._github_client", return_value=client),
        patch("issuesmith.recovery.plan", return_value=_mock_unblocked_plan()),
        patch("issuesmith.recovery.apply_redispatch_labels", return_value=False),
        patch("issuesmith.recovery.QueueStore", return_value=store),
    ):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            cmd_redispatch(123, phase="develop")
    categories = [str(x.category.__name__) for x in w]
    assert "FutureWarning" in categories


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


def test_resume_from_step_drops_held_entry(tmp_path):
    store = _held_store(tmp_path)
    with (
        patch("issuesmith.resume.QueueStore", return_value=store),
        patch("issuesmith.resume._resume_from_step", return_value=0),
    ):
        from issuesmith.resume import resume
        resume(123, from_step="p1")
    _assert_redispatch_has_no_held(store)


def test_resume_phase_drops_held_entry(tmp_path):
    store = _held_store(tmp_path)
    client = _make_client_mock()
    with (
        patch("issuesmith.resume.QueueStore", return_value=store),
        patch("issuesmith.resume.get_forge", return_value=client),
        patch("issuesmith.resume.get_config") as mock_cfg,
        patch("issuesmith.resume.apply_redispatch_labels"),
    ):
        mock_cfg.return_value.repo = "sumipan/issuesmith"
        from issuesmith.resume import resume
        resume(123, phase="develop")
    _assert_redispatch_has_no_held(store)
