"""Tests for issuesmith.resume — central resume entry point (#3509)."""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

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
    with (
        patch("issuesmith.resume.QueueStore") as mock_store_cls,
        patch("issuesmith.resume._generation_keys_available", return_value=True),
        patch("issuesmith.resume._run_ghdag_recover", return_value=0) as mock_recover,
    ):
        mock_store_cls.return_value = _make_store_mock()
        rc = resume(123, from_step="m1")
    assert rc == 0
    mock_recover.assert_called_once()
    call_args = mock_recover.call_args
    assert call_args[0][0] == 123  # issue
    assert call_args[0][2] == "m1"  # from_step


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
    with (
        patch("issuesmith.resume.QueueStore", return_value=store),
        patch("issuesmith.resume.get_forge", return_value=client),
        patch("issuesmith.resume.get_config") as mock_cfg,
        patch("issuesmith.resume.apply_redispatch_labels") as mock_labels,
    ):
        mock_cfg.return_value.repo = "sumipan/issuesmith"
        from issuesmith.resume import resume
        resume(123, phase="develop")
    mock_labels.assert_called_once()
    args = mock_labels.call_args[0]
    assert args[1] == 123
    assert args[2] == "develop"


def test_resume_phase_enqueues_issue():
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
        rc = resume(123, phase="merge")
    assert rc == 0
    store.enqueue.assert_called_once()
    eq_kwargs = store.enqueue.call_args[1]
    assert eq_kwargs["issue"] == 123
    assert eq_kwargs["phase"] == "merge"


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
# recovery.cmd_recover / cmd_redispatch emit FutureWarning and delegate to resume
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


def test_cmd_recover_emits_future_warning():
    import warnings
    from issuesmith.recovery import cmd_recover
    with (
        patch("issuesmith.recovery._generation_keys_available", return_value=False),
    ):
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # dry_run avoids subprocess calls
            cmd_recover(123, from_step="cp2", dry_run=True)
    categories = [str(x.category.__name__) for x in w]
    assert "FutureWarning" in categories


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


def test_cmd_redispatch_still_works_with_warning():
    """cmd_redispatch emits FutureWarning but still performs in_flight release + enqueue."""
    from issuesmith.recovery import cmd_redispatch
    store = _make_store_mock()
    client = MagicMock()
    client.issue_get.return_value = {"labels": []}
    with (
        patch("issuesmith.recovery._github_client", return_value=client),
        patch("issuesmith.recovery.plan", return_value=_mock_unblocked_plan()),
        patch("issuesmith.recovery.apply_redispatch_labels", return_value=False),
        patch("issuesmith.recovery.QueueStore", return_value=store),
        patch("warnings.warn"),
    ):
        rc = cmd_redispatch(123, phase="develop")
    assert rc == 0
    store.remove_in_flight.assert_called_once_with(123)
    store.enqueue.assert_called_once()
