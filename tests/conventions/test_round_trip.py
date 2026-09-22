"""Write → read-back round trips on a real local forge (GHDAG_FORGE=local)."""
from __future__ import annotations

from pathlib import Path

import pytest
from ghdag.forge import get_forge

from issuesmith.andon import Andon, answer, list_open, raise_andon
from issuesmith.ops.dispatch import map_step_result
from issuesmith.ops.labels import ExecRecord, apply, project
from issuesmith.queue_store import QueueStore
from issuesmith.steps.base import StepResult


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
    monkeypatch.chdir(tmp_path)  # metrics / jobs side effects land in tmp, not the repo
    return get_forge()


def test_andon_raise_list_answer_round_trip(client, tmp_path: Path):
    number = client.issue_create("round trip", "body")
    andon_id = f"issuesmith:{number}:cp2:0"
    metrics = tmp_path / "metrics.jsonl"
    raise_andon(
        client,
        Andon(id=andon_id, kind="decision", issue=number, step="cp2", summary="s", options=["resume", "accept"]),
        metrics_path=metrics,
    )
    assert "andon_raised" in metrics.read_text()
    assert [a.id for a in list_open(client)] == [andon_id]
    labels = {lb["name"] for lb in client.issue_get(number, fields=["labels"])["labels"]}
    assert any(lb.endswith(":andon-decision") for lb in labels)

    answer(client, andon_id, "accept", metrics_path=metrics)
    assert list_open(client) == []
    labels = {lb["name"] for lb in client.issue_get(number, fields=["labels"])["labels"]}
    assert not any(lb.endswith(":andon-decision") for lb in labels)


def test_label_apply_round_trip(client):
    number = client.issue_create("labels", "body")
    client.issue_update(number, labels_add=["issuesmith:draft-done", "issuesmith:develop-running", "bug"])
    issue = client.issue_get(number, fields=["labels"])
    issue["number"] = number
    apply(client, issue, {"issuesmith:develop-running"})
    labels = {lb["name"] for lb in client.issue_get(number, fields=["labels"])["labels"]}
    assert "issuesmith:draft-done" not in labels
    assert "issuesmith:develop-running" in labels
    assert "bug" in labels  # unmanaged labels are never touched


def _managed(client, number: int) -> set[str]:
    labels = {lb["name"] for lb in client.issue_get(number, fields=["labels"])["labels"]}
    return {lb for lb in labels if lb.startswith("issuesmith:")}


def test_merge_done_marker_projects_labels_consistent_with_project(client, capsys):
    """(b) MERGE_DONE -> merge-done added, merge-running removed, and project() agrees (zero drift)."""
    number = client.issue_create("merge", "body")
    client.issue_update(number, labels_add=["issuesmith:merge-running"])

    rc = map_step_result(
        StepResult(status="done", markers=["MERGE_DONE"]),
        step_id="m2",
        context={"issue_number": str(number), "workflow_name": "issuesmith"},
    )

    assert rc == 0
    assert "PIPELINE_STATUS: MERGE_DONE" in capsys.readouterr().out
    actual = _managed(client, number)
    assert "issuesmith:merge-done" in actual
    assert "issuesmith:merge-running" not in actual
    desired = project(number, queue_state=None, exec_records=[ExecRecord("merge", "done")], andon_inbox=[])
    assert actual == desired, f"dispatch projection drifts from labels.project(): {actual ^ desired}"


def test_merge_done_without_issue_context_leaves_labels_untouched(client, capsys):
    """Reproduction fixture for AC-3: with projection unreachable the labels stay stale, so the
    consistency check above is the one that catches a disabled projection."""
    number = client.issue_create("merge", "body")
    client.issue_update(number, labels_add=["issuesmith:merge-running"])

    map_step_result(StepResult(status="done", markers=["MERGE_DONE"]), step_id="m2", context={})

    capsys.readouterr()
    actual = _managed(client, number)
    desired = project(number, queue_state=None, exec_records=[ExecRecord("merge", "done")], andon_inbox=[])
    assert actual != desired


def test_queue_enqueue_dispatch_complete_round_trip(tmp_path: Path):
    """(c) enqueue -> visible in snapshot -> in_flight -> complete -> gone."""
    store = QueueStore(
        queue_path=tmp_path / "q.jsonl", state_path=tmp_path / "state.json", lock_path=tmp_path / "q.lock"
    )
    result = store.enqueue(
        issue=7, phase="develop", source="test", actor_kind="human", priority="normal", requested_by=["t"]
    )
    rid = result.request_id
    snap = store.snapshot()
    assert rid in snap.active_order and snap.requests[rid].issue == 7

    store.add_in_flight(7, "claude", role="implementation")
    assert [e["issue"] for e in store.snapshot().in_flight] == [7]

    store.complete(rid, "done")
    store.remove_in_flight(7)
    snap = store.snapshot()
    assert rid not in snap.active_order
    assert snap.in_flight == []
