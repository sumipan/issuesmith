"""F3: 存在しない Issue（404）を dispatch_one が not_found として除去する."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ghdag.core.exceptions import GitHubApiError

REPO_ROOT = Path(__file__).resolve().parents[3]
from issuesmith.queue_store import QueueStore


_NOW = datetime.now().astimezone().isoformat()
_VALID_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "tools/**"\n'
    "```\n"
)


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def test_dispatch_one_completes_404_as_not_found(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    result = store.enqueue(
        issue=12345,
        phase="draft",
        source="release-watcher",
        actor_kind="automation",
        priority="low",
        requested_by=["release-watcher"],
        requested_at=_NOW,
    )
    rid = result.request_id

    class Client:
        def __init__(self):
            self.comments = []

        def issue_get(self, number, fields=None):
            raise GitHubApiError(f"issue #{number} not found", status_code=404)

        def issue_comment(self, number, body):
            self.comments.append((number, body))

        def get_issue_comments(self, number):
            return []

        def list_issues(self, label, state="open"):
            return []

        def list_open_issues_for_queue(self):
            return []

        def api_request(self, path, **kwargs):
            return []

    client = Client()
    monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
    now = datetime(2026, 9, 4, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))

    out = qmod.dispatch_one(
        now=now,
        client=client,
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )

    assert out.dispatched is False
    snap = store.snapshot()
    assert rid not in snap.active_order
    assert rid in snap.completed_request_ids
    meta = snap.request_meta.get(rid) or {}
    assert meta.get("outcome") == "not_found"
    assert "404" in str(meta.get("reason", ""))
    # Issue が存在しないためコメントは投稿しない
    assert client.comments == []


def test_dispatch_one_keeps_non_404_api_errors_in_queue(tmp_path, monkeypatch):
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    result = store.enqueue(
        issue=50,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )
    rid = result.request_id

    class Client:
        def issue_get(self, number, fields=None):
            raise GitHubApiError("temporary", status_code=503)

        def list_issues(self, label, state="open"):
            return []

        def list_open_issues_for_queue(self):
            return []

        def api_request(self, path, **kwargs):
            return []

    monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
    now = datetime(2026, 9, 4, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))

    qmod.dispatch_one(
        now=now,
        client=Client(),
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )

    snap = store.snapshot()
    assert rid in snap.active_order
    assert rid not in snap.completed_request_ids


def test_dispatch_one_404_among_valid_issues(tmp_path, monkeypatch):
    """404 のエントリだけ除去し、有効 Issue は dispatch できる."""
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    bad = store.enqueue(
        issue=12345,
        phase="draft",
        source="release-watcher",
        actor_kind="automation",
        priority="low",
        requested_by=["release-watcher"],
        requested_at=_NOW,
    )
    good = store.enqueue(
        issue=50,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="high",
        requested_by=["alice"],
        requested_at=_NOW,
    )

    class Client:
        def __init__(self):
            self.updates = []

        def issue_get(self, number, fields=None):
            if number == 12345:
                raise GitHubApiError("missing", status_code=404)
            return {
                "number": number,
                "state": "OPEN",
                "title": "t",
                "body": _VALID_BODY,
                "labels": [],
            }

        def issue_update(self, number, labels_add=None, labels_remove=None):
            self.updates.append((number, labels_add))

        def issue_comment(self, number, body):
            pass

        def get_issue_comments(self, number):
            return []

        def list_issues(self, label, state="open"):
            return []

        def list_open_issues_for_queue(self):
            return []

        def api_request(self, path, **kwargs):
            return []

    client = Client()
    monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda: [])
    now = datetime(2026, 9, 4, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo"))

    out = qmod.dispatch_one(
        now=now,
        client=client,
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )

    snap = store.snapshot()
    assert bad.request_id not in snap.active_order
    assert (snap.request_meta.get(bad.request_id) or {}).get("outcome") == "not_found"
    assert out.dispatched is True
    assert out.issue == 50
    assert good.request_id in snap.completed_request_ids
