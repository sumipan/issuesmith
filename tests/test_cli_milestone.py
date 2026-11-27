"""CLI tests for milestone status / resume / prune."""

from __future__ import annotations

import json

import pytest

from issuesmith.milestone import milestone_resume, milestone_status
from issuesmith.queue_store import QueueStore


def _cli_main(argv: list[str]) -> int:
    from issuesmith import cli

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["milestone", *argv])
    code = exc_info.value.code
    return 0 if code is None else int(code)


class FakeClient:
    def __init__(self):
        self.issues = {
            100: {
                "number": 100,
                "state": "OPEN",
                "title": "milestone parent",
                "body": """```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - src/**
```""",
                "milestone": {"number": 1},
                "labels": [{"name": "scope:milestone"}, {"name": "issuesmith:sub-done"}],
            },
            101: {
                "number": 101,
                "state": "OPEN",
                "title": "child one",
                "body": """```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - src/**
```""",
                "milestone": {"number": 1},
                "labels": [{"name": "issuesmith:draft-done"}],
            },
            102: {
                "number": 102,
                "state": "OPEN",
                "title": "child two",
                "body": """```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - src/**
```""",
                "milestone": {"number": 1},
                "labels": [{"name": "issuesmith:draft-done"}],
            },
        }

    def issue_get(self, number, fields=None):
        return dict(self.issues[number])

    def get_issue_comments(self, number):
        return []

    def list_sub_issues(self, parent_number):
        if parent_number == 100:
            return [self.issues[101], self.issues[102]]
        return []

    def api_request(self, path, paginate=False):
        if path.startswith("issues?state=all&milestone="):
            return [self.issues[101], self.issues[102]]
        return []


class FakeStore:
    def __init__(self, chains=None):
        self.chains = chains or {"100": {"stage": "children_validated"}}

    def get_milestone_chain(self, parent):
        return dict(self.chains.get(str(parent), {}))

    def snapshot(self):
        from issuesmith.queue_store import QueueSnapshot

        return QueueSnapshot(
            schema_version=1,
            revision=0,
            active_order=[],
            completed_request_ids=[],
            last_triaged_revision=0,
            halt=False,
            halt_reason=None,
            requests={},
            request_meta={},
            milestone_chains=dict(self.chains),
        )

    def resume_milestone_chain(self, parent):
        entry = self.chains.get(str(parent))
        if not entry or entry.get("stage") != "halted":
            return False
        entry = dict(entry)
        entry.pop("halted_reason", None)
        entry["stage"] = "active"
        self.chains[str(parent)] = entry
        return True


def test_milestone_status_shows_parent_state_and_closed_parent(capsys):
    client = FakeClient()
    client.issues[100]["state"] = "CLOSED"
    store = FakeStore(
        chains={
            "100": {
                "stage": "children_validated",
                "notified_all_done": True,
                "closed_parent": True,
            }
        }
    )
    code = milestone_status(100, client=client, store=store)
    assert code == 0
    out = capsys.readouterr().out
    assert "state: CLOSED" in out
    assert "closed_parent: true" in out


def test_milestone_resume_success(capsys):
    store = FakeStore()
    store.chains["100"] = {"stage": "halted", "halted_reason": "validation failed"}
    code = milestone_resume(100, store=store)
    assert code == 0
    assert "resumed" in capsys.readouterr().out


def test_milestone_resume_not_halted(capsys):
    code = milestone_resume(100, store=FakeStore())
    assert code == 1
    assert "not halted" in capsys.readouterr().err


def _cli_store(tmp_path):
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def test_milestone_prune_applies(tmp_path, capsys, monkeypatch):
    store = _cli_store(tmp_path)
    store.update_milestone_chain(100, {"stage": "children_validated", "closed_parent": True})
    store.update_milestone_chain(150, {"stage": "active"})
    monkeypatch.setattr("issuesmith.milestone.QueueStore", lambda: store)
    code = _cli_main(["prune"])
    assert code == 0
    out = capsys.readouterr().out
    assert "pruned" in out
    assert "#100" in out
    assert "100" not in store.snapshot().milestone_chains
    assert "150" in store.snapshot().milestone_chains


def test_milestone_prune_empty(tmp_path, capsys, monkeypatch):
    store = _cli_store(tmp_path)
    store.update_milestone_chain(150, {"stage": "active"})
    before = store.state_path.read_bytes()
    monkeypatch.setattr("issuesmith.milestone.QueueStore", lambda: store)
    code = _cli_main(["prune"])
    assert code == 0
    out = capsys.readouterr().out
    assert "0" in out
    assert store.state_path.read_bytes() == before


class ConsolidateClient:
    def __init__(self, *, child_state="OPEN", child_labels=None, fail_get=False):
        self.issues = {
            101: {"number": 101, "state": child_state, "labels": child_labels or []},
            102: {"number": 102, "state": "CLOSED", "labels": [{"name": "issuesmith:merge-done"}]},
        }
        self.comments: dict[int, list[dict]] = {}
        self.posted: list[tuple[int, str]] = []
        self.updates: list[tuple[int, list, list]] = []
        self.closed: list[int] = []
        self.fail_get = fail_get

    def issue_get(self, number, fields=None):
        if self.fail_get:
            raise RuntimeError("boom")
        return dict(self.issues[number])

    def get_issue_comments(self, number):
        return list(self.comments.get(number, []))

    def issue_comment(self, number, body):
        self.posted.append((number, body))
        self.comments.setdefault(number, []).append({"body": body})

    def issue_update(self, number, *, labels_add=None, labels_remove=None, **kwargs):
        self.updates.append((number, labels_add, labels_remove))
        issue = self.issues[number]
        issue["labels"] = list(issue["labels"]) + [{"name": n} for n in labels_add or []]

    def issue_close(self, number, *, reason="completed"):
        self.closed.append(number)
        self.issues[number]["state"] = "CLOSED"
        self.issues[number]["state_reason"] = reason


def test_milestone_consolidate_closes_not_planned(tmp_path, monkeypatch):
    """Refs #7962 — consolidate close records not_planned state_reason."""
    from ghdag.forge import LocalForge

    from issuesmith.milestone import milestone_consolidate

    client = LocalForge(tmp_path / "forge")
    child = client.issue_create("child", "body")
    into = client.issue_create("into", "body")
    assert milestone_consolidate(child, into, client=client) == 0
    got = client.issue_get(child, fields=["state", "state_reason"])
    assert got["state"] == "CLOSED"
    assert got["state_reason"] == "not_planned"


def test_milestone_consolidate_marks_rejects_and_closes(capsys, monkeypatch):
    client = ConsolidateClient()
    monkeypatch.setattr("issuesmith.milestone.get_forge", lambda: client)
    assert _cli_main(["consolidate", "101", "--into", "102"]) == 0
    assert "milestone consolidate: #101 -> #102" in capsys.readouterr().out
    assert len(client.posted) == 1
    number, body = client.posted[0]
    assert number == 101
    assert "<!-- issuesmith:consolidated-into: #102 -->" in body
    assert "Consolidated into #102." in body
    assert client.updates == [(101, ["issuesmith:rejected"], [])]
    assert client.closed == [101]

    # Second run is idempotent.
    assert _cli_main(["consolidate", "101", "--into", "102"]) == 0
    assert len(client.posted) == 1
    assert len(client.updates) == 1
    assert client.closed == [101]


def test_milestone_consolidate_bad_args(capsys, monkeypatch):
    client = ConsolidateClient()
    monkeypatch.setattr("issuesmith.milestone.get_forge", lambda: client)
    for argv in (
        ["consolidate", "101", "--into", "101"],
        ["consolidate", "101"],
        ["consolidate", "101", "--into"],
        ["consolidate", "abc", "--into", "102"],
        ["consolidate", "101", "--into", "x"],
    ):
        with pytest.raises(SystemExit) as exc_info:
            from issuesmith import cli
            cli.main(["milestone", *argv])
        assert int(exc_info.value.code) == 2
    assert client.posted == []
    assert client.closed == []


def test_milestone_consolidate_fetch_failure(monkeypatch):
    client = ConsolidateClient(fail_get=True)
    monkeypatch.setattr("issuesmith.milestone.get_forge", lambda: client)
    assert _cli_main(["consolidate", "101", "--into", "102"]) == 1
    assert client.posted == []


def test_milestone_resume_not_halted_uses_fail_contract(capsys, monkeypatch) -> None:
    """Refs #8129 — milestone resume failures use error[FAILED] on stderr."""
    store = FakeStore()
    monkeypatch.setattr("issuesmith.milestone.QueueStore", lambda: store)
    rc = milestone_resume(100)
    assert rc == 1
    err = capsys.readouterr().err
    assert err.splitlines()[0].startswith("error[FAILED]: chain for #100 is not halted")


def test_status_json_has_stage_and_children(capsys, monkeypatch) -> None:
    """refs #6779: milestone status --json includes stage and child numbers."""
    monkeypatch.setattr("issuesmith.milestone.get_forge", lambda: FakeClient())
    monkeypatch.setattr("issuesmith.milestone.QueueStore", lambda: FakeStore())

    from issuesmith import cli

    with pytest.raises(SystemExit) as exc_info:
        cli.main(["milestone", "status", "100", "--json"])
    assert int(exc_info.value.code) == 0
    payload = json.loads(capsys.readouterr().out)
    assert "stage" in payload
    numbers = [child["number"] for child in payload["children"]]
    assert numbers == [101, 102]
