"""Release in_flight for CLOSED issues without terminal labels when DAG is not live (#4910)."""
from __future__ import annotations

from pathlib import Path

from issuesmith.observe.dag_state import DagState
from issuesmith.queue_store import QueueStore

_BODY_TESTS = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "tests/**"\n'
    "```\n"
)


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


class _ListIssuesClient:
    def __init__(self, issues: dict[int, dict]) -> None:
        self.issues = issues

    def issue_get(self, number: int, fields=None):
        data = dict(self.issues.get(number, {}))
        data.setdefault("number", number)
        return data

    def list_issues(self, label: str, state: str = "open") -> list[dict]:
        return []


def _dag_states_for(issue: int, status: str) -> dict[int, DagState]:
    key = f"issuesmith:impl:{issue}"
    return {issue: DagState(issue=issue, key=key, status=status)}


def _closed_draft_done(issue: int, extra_labels: list[str] | None = None) -> dict:
    labels = [{"name": "issuesmith:draft-done"}]
    for lab in extra_labels or []:
        labels.append({"name": lab})
    return {"state": "CLOSED", "labels": labels, "body": _BODY_TESTS}


def test_release_closed_draft_done_when_no_dag_entry(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    store.add_in_flight(4819, "cursor", role="implementation", phase="develop")
    client = _ListIssuesClient({4819: _closed_draft_done(4819)})
    monkeypatch.setattr("issuesmith.observe.dag_state.load_dag_states", lambda *_a, **_k: {})
    qmod._release_finished_in_flight(store, client, [])
    assert store.snapshot().in_flight == []


def test_closed_with_running_dag_not_released(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    entry = {"issue": 4819, "engine": "cursor", "role": "implementation"}
    client = _ListIssuesClient({4819: _closed_draft_done(4819)})
    monkeypatch.setattr(
        "issuesmith.observe.dag_state.load_dag_states",
        lambda *_a, **_k: _dag_states_for(4819, "running"),
    )
    assert qmod._in_flight_should_release(client, entry) is False


def test_load_dag_states_error_fail_closed(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    entry = {"issue": 4819, "engine": "cursor", "role": "implementation"}
    client = _ListIssuesClient({4819: _closed_draft_done(4819)})

    def boom(*_a, **_k):
        raise OSError("unreadable")

    monkeypatch.setattr("issuesmith.observe.dag_state.load_dag_states", boom)
    assert qmod._in_flight_should_release(client, entry) is False


def test_closed_non_terminal_release_logs_stderr(tmp_path, monkeypatch, capsys):
    from issuesmith import queue as qmod

    entry = {"issue": 4819, "engine": "cursor", "role": "implementation"}
    client = _ListIssuesClient({4819: _closed_draft_done(4819)})
    monkeypatch.setattr(
        "issuesmith.observe.dag_state.load_dag_states",
        lambda *_a, **_k: _dag_states_for(4819, "succeeded"),
    )
    assert qmod._in_flight_should_release(client, entry) is True
    assert capsys.readouterr().err.strip() == (
        "[queue] in_flight released: #4819 closed without terminal label"
    )


