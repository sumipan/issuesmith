"""AC-12: triage must not reject for deps-waiting reasons (#3130)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from issuesmith.contract import validate_frontmatter
from issuesmith.queue_store import QueueRequest, QueueStore
from issuesmith.queue_triage import (
    deterministic_decision,
    is_deps_waiting_reject_reason,
    is_yaml_reject_reason,
    missing_yaml_fields,
    triage,
)

_NOW = datetime.now(timezone.utc).isoformat()

_VALID_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths:\n"
    '  - "tools/**"\n'
    "```\n"
)
_EMPTY_ALLOW_PATHS_BODY = (
    "```yaml\n"
    "target_repo: sumipan/nexus\n"
    "base_branch: main\n"
    "allow_paths: []\n"
    "```\n"
)


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def test_validate_frontmatter_reports_missing_fields():
    assert validate_frontmatter(_EMPTY_ALLOW_PATHS_BODY) == ["allow_paths"]
    assert validate_frontmatter("no yaml") == [
        "target_repo",
        "base_branch",
        "allow_paths",
    ]
    assert validate_frontmatter(_VALID_BODY) == []


def test_missing_yaml_fields_matches_validate_frontmatter():
    body = "no yaml"
    assert missing_yaml_fields(body) == validate_frontmatter(body)


def test_is_yaml_reject_reason_matches():
    assert is_yaml_reject_reason("missing YAML fields") is True
    assert is_yaml_reject_reason("missing YAML: allow_paths") is True
    assert is_yaml_reject_reason("duplicate of #1") is False


def test_deterministic_decision_repair_for_sub_with_empty_allow_paths():
    req = QueueRequest(
        "11111111-1111-1111-1111-111111111111",
        1,
        "sub",
        "s",
        "automation",
        "low",
        _NOW,
        ("bot",),
    )
    decision = deterministic_decision(
        req,
        {
            "state": "OPEN",
            "title": "x",
            "body": _EMPTY_ALLOW_PATHS_BODY,
            "labels": [
                {"name": "issuesmith:draft-done"},
                {"name": "scope:milestone"},
            ],
        },
    )
    assert decision.kind == "repair"
    assert "missing YAML fields: allow_paths" in decision.reason
    assert decision.add_rejected_label is False


def test_deterministic_decision_draft_missing_yaml_keeps():
    req = QueueRequest(
        "11111111-1111-1111-1111-111111111111",
        1,
        "draft",
        "s",
        "automation",
        "low",
        _NOW,
        ("bot",),
    )
    decision = deterministic_decision(
        req,
        {"state": "OPEN", "title": "x", "body": "no yaml", "labels": []},
    )
    assert decision.kind == "keep"


def test_deterministic_decision_develop_without_draft_done_rejects():
    req = QueueRequest(
        "11111111-1111-1111-1111-111111111111",
        1,
        "develop",
        "s",
        "automation",
        "low",
        _NOW,
        ("bot",),
    )
    decision = deterministic_decision(
        req,
        {"state": "OPEN", "title": "x", "body": "no yaml", "labels": []},
    )
    assert decision.kind == "rejected"
    assert "draft-done" in decision.reason


def test_deterministic_decision_develop_rejects_scope_milestone():
    req = QueueRequest(
        "11111111-1111-1111-1111-111111111111",
        2972,
        "develop",
        "lane-check",
        "automation",
        "normal",
        _NOW,
        ("lane-check",),
    )
    decision = deterministic_decision(
        req,
        {
            "state": "OPEN",
            "title": "milestone parent",
            "body": _VALID_BODY,
            "labels": [
                {"name": "issuesmith:draft-done"},
                {"name": "scope:milestone"},
            ],
        },
    )
    assert decision.kind == "rejected"
    assert decision.add_rejected_label is True
    assert "scope:milestone" in decision.reason
    assert "develop" in decision.reason or "MILESTONE_BLOCKED" in decision.reason


def test_deterministic_decision_develop_without_scope_milestone_keeps():
    req = QueueRequest(
        "11111111-1111-1111-1111-111111111111",
        1,
        "develop",
        "lane-check",
        "automation",
        "normal",
        _NOW,
        ("lane-check",),
    )
    decision = deterministic_decision(
        req,
        {
            "state": "OPEN",
            "title": "normal issue",
            "body": _VALID_BODY,
            "labels": [{"name": "issuesmith:draft-done"}],
        },
    )
    assert decision.kind == "keep"


def test_is_deps_waiting_reject_reason_matches():
    assert is_deps_waiting_reject_reason("deps_blocked: waiting for #3162") is True
    assert is_deps_waiting_reject_reason("dependencies not resolved") is True
    assert is_deps_waiting_reject_reason("waiting for deps #3162") is True
    assert is_deps_waiting_reject_reason("duplicate of #1") is False
    assert is_deps_waiting_reject_reason("obsolete bump") is False


def test_triage_downgrades_deps_reject_to_keep(tmp_path: Path):
    store = _store(tmp_path)
    rid = store.enqueue(
        issue=3166,
        phase="develop",
        source="milestone-chain",
        actor_kind="automation",
        priority="normal",
        requested_by=["milestone-chain"],
        requested_at=_NOW,
    ).request_id
    snap = store.snapshot()

    def fake_llm(prompt: str, **kwargs):
        payload = {
            "order": [rid],
            "decisions": [
                {
                    "request_id": rid,
                    "decision": "reject",
                    "reason": "deps_not_resolved waiting for #3162",
                    "uncertain_flag": False,
                }
            ],
        }
        return type("R", (), {"text": json.dumps(payload), "usage": None})()

    result = triage(
        snap,
        issues={
            3166: {
                "number": 3166,
                "title": "child",
                "body": "```yaml\ntarget_repo: sumipan/nexus\nbase_branch: main\nallow_paths:\n  - src/**\n```\n",
                "labels": [{"name": "issuesmith:draft-done"}],
                "state": "OPEN",
            }
        },
        engine="cursor",
        model="auto",
        call_llm=fake_llm,
        store=store,
        triage_log_path=tmp_path / "triage.jsonl",
    )
    assert result.adopted is True
    assert len(result.decisions) == 1
    assert result.decisions[0].decision == "keep"
    assert "deps reject ignored" in result.decisions[0].reason


def test_triage_downgrades_yaml_reject_to_keep(tmp_path: Path):
    store = _store(tmp_path)
    rid = store.enqueue(
        issue=3167,
        phase="develop",
        source="milestone-chain",
        actor_kind="automation",
        priority="normal",
        requested_by=["milestone-chain"],
        requested_at=_NOW,
    ).request_id
    snap = store.snapshot()

    def fake_llm(prompt: str, **kwargs):
        payload = {
            "order": [rid],
            "decisions": [
                {
                    "request_id": rid,
                    "decision": "reject",
                    "reason": "missing YAML: allow_paths",
                    "uncertain_flag": False,
                }
            ],
        }
        return type("R", (), {"text": json.dumps(payload), "usage": None})()

    result = triage(
        snap,
        issues={
            3167: {
                "number": 3167,
                "title": "child",
                "body": _VALID_BODY,
                "labels": [{"name": "issuesmith:draft-done"}],
                "state": "OPEN",
            }
        },
        engine="cursor",
        model="auto",
        call_llm=fake_llm,
        store=store,
        triage_log_path=tmp_path / "triage.jsonl",
    )
    assert result.adopted is True
    assert len(result.decisions) == 1
    assert result.decisions[0].decision == "keep"
    assert "yaml reject ignored" in result.decisions[0].reason
