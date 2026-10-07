"""Write → read-back round trips on a real local forge (GHDAG_FORGE=local)."""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import yaml
from ghdag.forge import get_forge

from issuesmith import config as config_module
from issuesmith.andon import Andon, answer, list_open, raise_andon
from issuesmith.contract import StepResult
from issuesmith.ops.dispatch import map_step_result
from issuesmith.ops.labels import ExecRecord, apply, project
from issuesmith.queue_store import QueueStore


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


# Merge declared as a multi-step phase: m1 -> m2. Only the final step (m2) projects merge-done.
_MERGE_STEPS_PHASES = [
    {"name": "draft", "role": "design", "entry_step": "b1"},
    {"name": "sub", "role": "implementation", "entry_step": "sub-ready"},
    {"name": "develop", "role": "implementation", "entry_step": "cp2"},
    {"name": "merge", "role": "implementation", "entry_step": "m2", "steps": ["m1", "m2"]},
]

_HAS_PHASE_STEPS = "steps" in {f.name for f in dataclasses.fields(config_module.PhaseConfig)}
_requires_final_step_projection = pytest.mark.skipif(
    not _HAS_PHASE_STEPS,
    reason="final-step label projection (PhaseConfig.steps, parent #4788 sub1/sub2) not installed",
)


@pytest.fixture
def merge_steps_config(tmp_path: Path, monkeypatch):
    """Real issuesmith.yaml declaring merge as phases[].steps = [m1, m2]."""
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump({"repo": "owner/round-trip", "phases": _MERGE_STEPS_PHASES}), encoding="utf-8"
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    config_module.reset_config_cache()
    yield
    config_module.reset_config_cache()


def _merge_running_issue(client) -> int:
    number = client.issue_create("merge", "body")
    client.issue_update(number, labels_add=["issuesmith:merge-running", "bug"])
    return number


def _merge_done_desired(number: int) -> set[str]:
    return project(number, queue_state=None, exec_records=[ExecRecord("merge", "done")], andon_inbox=[])


def test_merge_non_final_step_done_keeps_merge_running(client, merge_steps_config, capsys):
    """A done non-final merge step (m1) neither adds merge-done nor removes merge-running."""
    number = _merge_running_issue(client)

    rc = map_step_result(
        StepResult(status="done"),
        step_id="m1",
        context={"issue_number": str(number), "workflow_name": "issuesmith"},
    )

    capsys.readouterr()
    assert rc == 0
    labels = {lb["name"] for lb in client.issue_get(number, fields=["labels"])["labels"]}
    assert "issuesmith:merge-done" not in labels
    assert "issuesmith:merge-running" in labels
    assert "bug" in labels


@_requires_final_step_projection
def test_merge_final_step_done_projects_labels_consistent_with_project(client, merge_steps_config, capsys):
    """The final merge step (m2) done -> merge-done added, merge-running removed, unmanaged kept,
    and project() agrees (zero drift). No marker is set: projection is decided by the step, not
    by a marker name."""
    number = _merge_running_issue(client)

    rc = map_step_result(
        StepResult(status="done"),
        step_id="m2",
        context={"issue_number": str(number), "workflow_name": "issuesmith"},
    )

    capsys.readouterr()
    assert rc == 0
    labels = {lb["name"] for lb in client.issue_get(number, fields=["labels"])["labels"]}
    assert "bug" in labels  # unmanaged labels are never touched
    actual = _managed(client, number)
    assert "issuesmith:merge-done" in actual
    assert "issuesmith:merge-running" not in actual
    desired = _merge_done_desired(number)
    assert actual == desired, f"dispatch projection drifts from labels.project(): {actual ^ desired}"


def test_merge_final_step_without_issue_context_leaves_labels_untouched(client, merge_steps_config, capsys):
    """Without an issue context the runner cannot project, so labels stay exactly as they were."""
    number = _merge_running_issue(client)
    before = _managed(client, number)

    map_step_result(StepResult(status="done"), step_id="m2", context={})

    capsys.readouterr()
    actual = _managed(client, number)
    assert actual == before == {"issuesmith:merge-running"}
    assert actual != _merge_done_desired(number)


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
