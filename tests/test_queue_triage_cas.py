"""AC-1 / AC-2 / AC-5 / AC-6: deterministic-first triage, CAS retry, orphan purge (#3159)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.queue_store import QueueStore

_JST = ZoneInfo("Asia/Tokyo")
_FIXED_NOW = datetime(2026, 9, 11, 6, 0, tzinfo=_JST)
_NOW = _FIXED_NOW.isoformat()
_VALID_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "src/**"\n'
    "```\n"
)


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def _write_minimal_config(tmp_path: Path, monkeypatch) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "supported_repos": ["sumipan/nexus"],
                "concurrency": {"default": 1},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


class _Client:
    def __init__(self, issues: dict[int, dict]):
        self.issues = issues

    def issue_get(self, number, fields=None):
        data = dict(self.issues[number])
        data.setdefault("number", number)
        return data

    def issue_update(self, number, labels_add=None, labels_remove=None):
        pass

    def issue_comment(self, number, body):
        pass

    def get_issue_comments(self, number):
        return []

    def list_issues(self, label, state="open"):
        return []

    def list_open_issues_for_queue(self):
        return [
            dict(v, number=n)
            for n, v in self.issues.items()
            if str(v.get("state", "OPEN")).upper() == "OPEN"
        ]

    def api_request(self, path, **kwargs):
        return []


def test_cas_retry_keeps_triage_order_and_appends_new_request(
    tmp_path, monkeypatch,
):
    """AC-1: if enqueue advances revision during LLM, adopt on one retry."""
    _write_minimal_config(tmp_path, monkeypatch)
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    r_low = store.enqueue(
        issue=1,
        phase="draft",
        source="s",
        actor_kind="automation",
        priority="low",
        requested_by=["a"],
        requested_at=_NOW,
    )
    r_high = store.enqueue(
        issue=2,
        phase="draft",
        source="s",
        actor_kind="automation",
        priority="high",
        requested_by=["a"],
        requested_at=_NOW,
    )
    issues = {
        1: {"state": "OPEN", "labels": [], "title": "low", "body": _VALID_BODY},
        2: {"state": "OPEN", "labels": [], "title": "high", "body": _VALID_BODY},
        99: {"state": "OPEN", "labels": [], "title": "new", "body": _VALID_BODY},
    }
    new_rid_holder: dict[str, str] = {}

    def llm_with_concurrent_enqueue(prompt, **kwargs):
        # Bump revision while triage LLM is "in flight".
        added = store.enqueue(
            issue=99,
            phase="draft",
            source="scheduler",
            actor_kind="automation",
            priority="normal",
            requested_by=["scheduler"],
            requested_at=_NOW,
        )
        new_rid_holder["rid"] = added.request_id
        # Prefer low before high within... but priority bucket will keep high first.
        order = [r_low.request_id, r_high.request_id]

        class R:
            text = json.dumps(
                {
                    "order": order,
                    "decisions": [
                        {
                            "request_id": rid,
                            "decision": "keep",
                            "reason": "ok",
                            "uncertain_flag": False,
                        }
                        for rid in order
                    ],
                }
            )
            usage = None

        return R()

    monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: ["claude"])
    now = _FIXED_NOW
    qmod.dispatch_one(
        now=now,
        client=_Client(issues),
        store=store,
        skip_seed=True,
        call_llm=llm_with_concurrent_enqueue,
    )

    snap = store.snapshot()
    assert snap.active_order[0] == r_high.request_id
    assert new_rid_holder["rid"] in snap.active_order
    assert snap.active_order[-1] == new_rid_holder["rid"]


def test_cas_conflict_logged_when_retry_fails(tmp_path, monkeypatch):
    """AC-1: if retry also fails, log fallback_reason=cas_conflict."""
    _write_minimal_config(tmp_path, monkeypatch)
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    r = store.enqueue(
        issue=1,
        phase="draft",
        source="s",
        actor_kind="automation",
        priority="high",
        requested_by=["a"],
        requested_at=_NOW,
    )
    issues = {
        1: {"state": "OPEN", "labels": [], "title": "h", "body": _VALID_BODY},
    }
    monkeypatch.setenv("ISSUESMITH_QUEUE_DIR", str(tmp_path))
    # Point DEFAULT via env: queue uses ISSUESMITH_QUEUE_DIR / issuesmith-triage.jsonl
    (tmp_path / "issuesmith-triage.jsonl").touch()

    calls = {"n": 0}
    real_replace = store.replace_order

    def flaky_replace(expected_revision, request_ids):
        calls["n"] += 1
        # 1st = deterministic fallback (succeed). Subsequent LLM applies always fail.
        if calls["n"] == 1:
            return real_replace(expected_revision, request_ids)
        return False

    store.replace_order = flaky_replace  # type: ignore[method-assign]

    def fake_llm(prompt, **kwargs):
        class R:
            text = json.dumps(
                {
                    "order": [r.request_id],
                    "decisions": [
                        {
                            "request_id": r.request_id,
                            "decision": "keep",
                            "reason": "ok",
                            "uncertain_flag": False,
                        }
                    ],
                }
            )
            usage = None

        return R()

    monkeypatch.setattr(qmod, "_pipeline_idle_enough", lambda *a, **k: True)
    monkeypatch.setattr(qmod, "_required_engines_paused", lambda *a, **k: [])
    now = _FIXED_NOW
    qmod.dispatch_one(
        now=now,
        client=_Client(issues),
        store=store,
        skip_seed=True,
        call_llm=fake_llm,
    )

    log_path = tmp_path / "issuesmith-triage.jsonl"
    assert log_path.exists()
    entries = [
        json.loads(ln)
        for ln in log_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    cas_entries = [e for e in entries if e.get("fallback_reason") == "cas_conflict"]
    assert cas_entries, f"expected cas_conflict in {entries!r}"
    assert cas_entries[-1].get("adopted_order") is None


def test_purge_orphan_ids_and_doctor_audit(tmp_path, monkeypatch, capsys):
    """AC-5: doctor detects orphan IDs and audit purges them."""
    _write_minimal_config(tmp_path, monkeypatch)
    from issuesmith import queue as qmod

    store = _store(tmp_path)
    r = store.enqueue(
        issue=1,
        phase="draft",
        source="s",
        actor_kind="automation",
        priority="normal",
        requested_by=["a"],
        requested_at=_NOW,
    )
    orphan = str(uuid.uuid4())
    # Inject orphan into raw state (snapshot() filters these out).
    state = json.loads(store.state_path.read_text(encoding="utf-8"))
    state["active_order"] = [orphan, r.request_id]
    store.state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")

    orphans = store.purge_orphan_ids()
    assert orphans == [orphan]
    state2 = json.loads(store.state_path.read_text(encoding="utf-8"))
    assert orphan not in state2["active_order"]
    assert r.request_id in state2["active_order"]

    # Re-inject for doctor detection
    state2["active_order"] = [orphan, r.request_id]
    store.state_path.write_text(json.dumps(state2, indent=2) + "\n", encoding="utf-8")

    ns = type("NS", (), {})()
    ns.queue_path = str(store.queue_path)
    ns.state_path = str(store.state_path)
    ns.lock_path = str(store.lock_path)
    code = qmod._cmd_doctor(ns)
    assert code == 1
    out = capsys.readouterr().out
    assert orphan in out

    # audit should purge
    ns.offline = True
    code = qmod._cmd_audit(ns)
    assert code == 0
    state3 = json.loads(store.state_path.read_text(encoding="utf-8"))
    assert orphan not in state3["active_order"]
    out2 = capsys.readouterr().out
    assert orphan in out2 or "orphan" in out2.lower() or "purged" in out2.lower()


