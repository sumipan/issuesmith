"""CLI tests for milestone status / resume."""

from __future__ import annotations

from issuesmith.milestone import milestone_resume, milestone_status


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
        }

    def issue_get(self, number, fields=None):
        return dict(self.issues[number])

    def api_request(self, path, paginate=False):
        if path.startswith("issues?state=all&milestone="):
            return [self.issues[101]]
        return []


class FakeStore:
    def __init__(self, chains=None):
        self.chains = chains or {"100": {"stage": "children_validated"}}

    def get_milestone_chain(self, parent):
        return dict(self.chains.get(str(parent), {}))

    def resume_milestone_chain(self, parent):
        entry = self.chains.get(str(parent))
        if not entry or entry.get("stage") != "halted":
            return False
        entry = dict(entry)
        entry.pop("halted_reason", None)
        entry["stage"] = "active"
        self.chains[str(parent)] = entry
        return True


def test_milestone_status_prints_table(capsys):
    code = milestone_status(100, client=FakeClient(), store=FakeStore())
    assert code == 0
    out = capsys.readouterr().out
    assert "milestone chain #100" in out
    assert "101" in out
    assert "child one" in out


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
