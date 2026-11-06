"""Queue dependency merge and scope coupling dispatch."""

from __future__ import annotations

from datetime import datetime

import pytest

from tests import test_queue as _base
from tests.test_queue import _NOW, _VALID_BODY, _ProdShapeClient, _store

nexus_config = _base.nexus_config

pytestmark = pytest.mark.usefixtures("nexus_config")


# ---------------------------------------------------------------------------
# scope_coupling deletion reference re-check (nexus #3953)
# ---------------------------------------------------------------------------

_DEL_REFS = {"scripts/git-sync.py": ["tests/scripts/test_vcs_untrack_migration.py"]}


def _develop_client(body: str = _VALID_BODY) -> _ProdShapeClient:
    return _ProdShapeClient(
        issues={
            50: {
                "state": "OPEN",
                "title": "t",
                "body": body,
                "labels": [{"name": "issuesmith:draft-done"}],
            }
        },
        open_issue_rows=[],
    )


def _dispatch(store, client, monkeypatch):
    from zoneinfo import ZoneInfo

    from issuesmith import queue as qmod

    monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda idle, now: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    return qmod.dispatch_one(
        now=datetime(2026, 9, 3, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
        client=client,
        store=store,
        skip_seed=True,
        call_llm=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no llm")),
    )


class TestScopeCouplingDispatch:
    def test_develop_dispatch_blocked_on_uncovered_deletion_reference(self, tmp_path, monkeypatch):
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="develop", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        client = _develop_client()
        monkeypatch.setattr(qmod, "deletion_references_for_body", lambda body: dict(_DEL_REFS))
        result = _dispatch(store, client, monkeypatch)
        assert result.dispatched is False
        assert store.snapshot().active_order  # request stays queued for the next tick
        assert not any("issuesmith:develop-ready" in (u[1] or []) for u in client.updates)
        [(num, body)] = client.comments
        assert num == 50
        assert "scripts/git-sync.py" in body
        assert "tests/scripts/test_vcs_untrack_migration.py" in body

    def test_develop_dispatch_proceeds_without_deletion_reference(self, tmp_path, monkeypatch):
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="develop", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        seen: list[str] = []

        def _refs(body):
            seen.append(body)
            return {}

        monkeypatch.setattr(qmod, "deletion_references_for_body", _refs)
        result = _dispatch(store, _develop_client(), monkeypatch)
        assert result.dispatched is True
        assert seen == [_VALID_BODY]

    def test_draft_dispatch_does_not_run_deletion_check(self, tmp_path, monkeypatch):
        from issuesmith import queue as qmod

        store = _store(tmp_path)
        store.enqueue(
            issue=50, phase="draft", source="skill", actor_kind="human",
            priority="normal", requested_by=["alice"], requested_at=_NOW,
        )
        client = _ProdShapeClient(
            issues={50: {"state": "OPEN", "title": "t", "body": _VALID_BODY, "labels": []}},
            open_issue_rows=[],
        )
        monkeypatch.setattr(qmod, "deletion_references_for_body", lambda body: dict(_DEL_REFS))
        result = _dispatch(store, client, monkeypatch)
        assert result.dispatched is True


_DEPENDENT_BODY = (
    "```yaml\n"
    "target_repo: sumipan/issuesmith\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "scripts/git-sync.py"\n'
    "```\n\n"
    "## Dependencies\n\n"
    "- #40\n\n"
    "## Changed Files\n\n"
    "| Repo | File | Change | Note |\n"
    "| sumipan/issuesmith | scripts/git-sync.py | delete | retire |\n"
)


class TestScopeCouplingAfterDepMerge:
    def _client(self):
        return _ProdShapeClient(
            issues={
                40: {
                    "state": "CLOSED",
                    "title": "dep",
                    "body": _VALID_BODY,
                    "labels": [{"name": "issuesmith:merge-done"}],
                },
                60: {"state": "OPEN", "title": "later", "body": _DEPENDENT_BODY, "labels": []},
                61: {"state": "OPEN", "title": "other", "body": _VALID_BODY, "labels": []},
            },
            open_issue_rows=[
                {"number": 60, "title": "later", "state": "open", "body": _DEPENDENT_BODY,
                 "labels": []},
                {"number": 61, "title": "other", "state": "open", "body": _VALID_BODY,
                 "labels": []},
            ],
        )

    def test_merge_done_release_rechecks_dependents(self, tmp_path, monkeypatch):
        from issuesmith.gates import dep as dep_mod

        store = _store(tmp_path)
        store.add_in_flight(40, "claude", role="implementation",
                            allow_paths=("tools/**",), target_repo="sumipan/nexus")
        checked: list[str] = []

        def _refs(body):
            checked.append(body)
            return dict(_DEL_REFS)

        monkeypatch.setattr(dep_mod, "deletion_references_for_body", _refs)
        client = self._client()
        _dispatch(store, client, monkeypatch)
        assert not store.snapshot().in_flight
        assert checked == [_DEPENDENT_BODY]  # only the Issue that depends on #40
        [(num, body)] = client.comments
        assert num == 60
        assert "#40" in body
        assert "tests/scripts/test_vcs_untrack_migration.py" in body

    def test_release_without_merge_done_skips_recheck(self, tmp_path, monkeypatch):
        from issuesmith.gates import dep as dep_mod

        store = _store(tmp_path)
        store.add_in_flight(40, "claude", role="implementation",
                            allow_paths=("tools/**",), target_repo="sumipan/nexus")
        client = self._client()
        client.issues[40]["labels"] = [{"name": "issuesmith:rejected"}]
        monkeypatch.setattr(
            dep_mod, "deletion_references_for_body",
            lambda body: (_ for _ in ()).throw(AssertionError("must not run")),
        )
        _dispatch(store, client, monkeypatch)
        assert client.comments == []


class TestOnDepMergeDone:
    def test_comment_posted_once_per_dependency(self, monkeypatch):
        from issuesmith.gates import dep as dep_mod

        monkeypatch.setattr(dep_mod, "deletion_references_for_body", lambda body: dict(_DEL_REFS))

        class _C:
            def __init__(self):
                self.posted: list[tuple[int, str]] = []

            def get_issue_comments(self, number):
                return [{"body": b} for n, b in self.posted if n == number]

            def issue_comment(self, number, body):
                self.posted.append((number, body))

        client = _C()
        dependents = [{"number": 60, "body": _DEPENDENT_BODY}]
        assert dep_mod.on_dep_merge_done(40, dependents, client=client) == [60]
        assert dep_mod.on_dep_merge_done(40, dependents, client=client) == []
        [(num, body)] = client.posted
        assert body.startswith("## ")
        assert "#40" in body.splitlines()[0]
        assert "`scripts/git-sync.py`" in body
        assert "- `tests/scripts/test_vcs_untrack_migration.py`" in body

    def test_dependents_of_uses_dependency_section(self):
        from issuesmith.gates.dep import dependents_of

        issues = [{"number": 60, "body": _DEPENDENT_BODY}, {"number": 61, "body": _VALID_BODY}]
        assert [i["number"] for i in dependents_of(40, issues)] == [60]
        assert dependents_of(41, issues) == []

    def test_real_git_base_detects_referrer(self, tmp_path, monkeypatch):
        """End-to-end on a real git checkout: the dependency added a referrer."""
        import subprocess

        from issuesmith.gate_rules import scope_coupling
        from issuesmith.gates.dep import on_dep_merge_done

        root = tmp_path / "repo"
        (root / "scripts").mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "scripts" / "git-sync.py").write_text("x = 1\n", encoding="utf-8")
        (root / "tests" / "test_untrack.py").write_text(
            'p = "scripts/git-sync.py"\n', encoding="utf-8"
        )
        for args in (("init", "-q"), ("add", "-A"),
                     ("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i")):
            subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
        monkeypatch.setattr(scope_coupling, "resolve_scope_root", lambda meta, cfg: root)

        class _C:
            posted: list = []

            def get_issue_comments(self, number):
                return []

            def issue_comment(self, number, body):
                self.posted.append((number, body))

        client = _C()
        assert on_dep_merge_done(
            40, [{"number": 60, "body": _DEPENDENT_BODY}], client=client
        ) == [60]
        assert "`tests/test_untrack.py`" in client.posted[0][1]
