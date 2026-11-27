"""Deterministic queue intake and ordering (#8161)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import yaml

import issuesmith.queue_triage as queue_triage
from issuesmith.config import reset_config_cache
from issuesmith.contract import validate_frontmatter
from issuesmith.queue_store import QueueRequest, QueueStore
from issuesmith.queue_triage import deterministic_decision, missing_yaml_fields

_FIXED_NOW = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
_NOW = _FIXED_NOW.isoformat()

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
_UNSUPPORTED_REPO_BODY = (
    "```yaml\n"
    "target_repo: example/other\n"
    "base_branch: main\n"
    "allow_paths: []\n"
    "```\n"
)
_SUPPORTED_REPO_BODY = (
    "```yaml\n"
    "target_repo: example/app\n"
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


def _example_app_config(tmp_path: Path) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "example/app",
                "supported_repos": ["example/app"],
                "concurrency": {"default": 1},
            }
        ),
        encoding="utf-8",
    )


def _store(tmp_path: Path) -> QueueStore:
    return QueueStore(
        queue_path=tmp_path / "queue.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )


def test_missing_yaml_fields_matches_validate_frontmatter():
    body = "no yaml"
    assert missing_yaml_fields(body) == validate_frontmatter(body)


def test_queue_triage_exposes_only_deterministic_api():
    """Refs sumipan/issuesmith#8161 — LLM triage symbols must not remain on the module."""
    removed = (
        "triage",
        "TriageDecision",
        "TriageResult",
        "_parse_llm_response",
        "is_circuit_open",
        "append_triage_log",
        "append_cas_conflict_log",
        "append_circuit_open_log",
        "is_deps_waiting_reject_reason",
        "is_yaml_reject_reason",
    )
    for name in removed:
        assert not hasattr(queue_triage, name), name
    assert "ghdag.llm" not in Path(queue_triage.__file__).read_text(encoding="utf-8")


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


def test_draft_rejects_unsupported_target_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """#7985: unsupported target_repo is rejected at queue intake."""
    _example_app_config(tmp_path)
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(tmp_path / "issuesmith.yaml"))
    reset_config_cache()
    req = QueueRequest(
        "11111111-1111-1111-1111-111111111111",
        1,
        "draft",
        "skill",
        "human",
        "normal",
        _NOW,
        ("alice",),
    )
    decision = deterministic_decision(
        req,
        {"state": "OPEN", "title": "t", "body": _UNSUPPORTED_REPO_BODY, "labels": []},
    )
    assert decision.kind == "rejected"
    assert decision.comment is True
    assert decision.add_rejected_label is True
    assert "example/other" in decision.reason
    assert "example/app" in decision.reason
    assert "issuesmith" in decision.reason


def test_develop_rejects_unsupported_target_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """#7985: develop uses the same unsupported target_repo rejection as draft."""
    _example_app_config(tmp_path)
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(tmp_path / "issuesmith.yaml"))
    reset_config_cache()
    req = QueueRequest(
        "22222222-2222-2222-2222-222222222222",
        2,
        "develop",
        "skill",
        "human",
        "normal",
        _NOW,
        ("alice",),
    )
    decision = deterministic_decision(
        req,
        {
            "state": "OPEN",
            "title": "t",
            "body": _UNSUPPORTED_REPO_BODY,
            "labels": [{"name": "issuesmith:draft-done"}],
        },
    )
    assert decision.kind == "rejected"
    assert decision.comment is True
    assert decision.add_rejected_label is True
    assert "example/other" in decision.reason
    assert "example/app" in decision.reason
    assert "issuesmith" in decision.reason


def test_supported_target_repo_is_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """#7985: supported target_repo passes deterministic intake."""
    _example_app_config(tmp_path)
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(tmp_path / "issuesmith.yaml"))
    reset_config_cache()
    req = QueueRequest(
        "33333333-3333-3333-3333-333333333333",
        3,
        "draft",
        "skill",
        "human",
        "normal",
        _NOW,
        ("alice",),
    )
    decision = deterministic_decision(
        req,
        {"state": "OPEN", "title": "t", "body": _SUPPORTED_REPO_BODY, "labels": []},
    )
    assert decision.kind == "keep"


def test_draft_without_target_repo_is_kept_for_b1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """#7985: missing target_repo stays keep so B1 can fill frontmatter."""
    _example_app_config(tmp_path)
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(tmp_path / "issuesmith.yaml"))
    reset_config_cache()
    req = QueueRequest(
        "44444444-4444-4444-4444-444444444444",
        4,
        "draft",
        "skill",
        "human",
        "normal",
        _NOW,
        ("alice",),
    )
    decision = deterministic_decision(
        req,
        {"state": "OPEN", "title": "t", "body": "design text only", "labels": []},
    )
    assert decision.kind == "keep"


def test_dispatch_one_rejects_unsupported_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """#7985: dispatch_one rejects unsupported target_repo without stage dispatch."""
    from ghdag.forge import LocalForge

    from issuesmith import queue as qmod

    _example_app_config(tmp_path)
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(tmp_path / "issuesmith.yaml"))
    monkeypatch.setenv("ISSUESMITH_QUEUE_DIR", str(tmp_path))
    reset_config_cache()

    store = _store(tmp_path)
    client = LocalForge(tmp_path / "forge")
    number = client.issue_create("unsupported repo", _UNSUPPORTED_REPO_BODY)
    req = store.enqueue(
        issue=number,
        phase="draft",
        source="skill",
        actor_kind="human",
        priority="normal",
        requested_by=["alice"],
        requested_at=_NOW,
    )

    pipeline_called: list[int] = []

    def no_pipeline(*_args, **_kwargs):
        pipeline_called.append(1)
        return True

    monkeypatch.setattr(qmod, "advance_milestone_chains", lambda *a, **k: None)
    monkeypatch.setattr(qmod, "_dispatch_pipeline_ready", no_pipeline)
    monkeypatch.setattr(
        qmod,
        "apply",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("apply must not run")),
    )

    qmod.dispatch_one(
        now=datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo("Asia/Tokyo")),
        client=client,
        store=store,
        skip_seed=True,
    )

    assert not pipeline_called
    meta = store.snapshot().request_meta.get(req.request_id) or {}
    assert meta.get("outcome") == "rejected"
    issue = client.issue_get(number, fields=["labels"])
    label_names = [
        lab.get("name") for lab in (issue.get("labels") or []) if isinstance(lab, dict)
    ]
    assert "issuesmith:rejected" in label_names
    comments = client.get_issue_comments(number)
    assert len(comments) == 1
    assert "example/other" in str(comments[0].get("body", ""))
