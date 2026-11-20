"""Resume base catch-up before redispatch or dag recover."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


def test_resume_aborts_on_base_rebase_conflict_without_recover():
    """refs #7464: base conflict stops resume before in_flight release or recover."""
    store = MagicMock()

    with (
        patch("issuesmith.resume.QueueStore", return_value=store),
        patch("issuesmith.resume._catch_up_base_before_resume", return_value=1),
        patch("issuesmith.resume._generation_keys_available", return_value=True),
        patch("issuesmith.resume._resume_from_step") as recover,
    ):
        from issuesmith.resume import resume

        rc = resume(99, from_step="impl")
    assert rc == 1
    store.remove_in_flight.assert_not_called()
    recover.assert_not_called()


def test_resume_clears_stopped_before_redispatch(tmp_path):
    """refs #7464: phase resume clears stopped memory and drops stale in_flight."""
    from issuesmith.queue_store import QueueStore

    store = QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )
    store.add_in_flight(77, "claude")
    store.drop_stopped_issue(77, "wf:77:p1:0")

    client = MagicMock()
    client.issue_get.return_value = {"labels": []}

    with (
        patch("issuesmith.resume.QueueStore", return_value=store),
        patch("issuesmith.resume._catch_up_base_before_resume", return_value=0),
        patch("issuesmith.resume.get_forge", return_value=client),
        patch("issuesmith.resume.get_config") as mock_cfg,
        patch("issuesmith.resume.apply_redispatch_labels"),
    ):
        mock_cfg.return_value.repo = "sumipan/nexus"
        from issuesmith.resume import resume

        rc = resume(77, phase="develop")
    assert rc == 0
    snap = store.snapshot()
    assert snap.in_flight == []
    assert snap.stopped == []
