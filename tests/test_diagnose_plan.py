"""Deterministic diagnose planning and apply (issue #8077)."""
from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone

import pytest
import yaml

from issuesmith.andon import Andon, diagnosis_signature, list_open_records, raise_andon
from issuesmith.config import AndonDiagnoseConfig, reset_config_cache
from issuesmith.diagnose import apply_diagnose_plan, plan_diagnoses

_FIXED_NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def _settings(**kwargs) -> AndonDiagnoseConfig:
    defaults = {"enabled": True, "step": "diagnose", "handler": "diagnose", "reuse_days": 7}
    defaults.update(kwargs)
    return AndonDiagnoseConfig(**defaults)


def _record(
    *,
    andon_id: str = "wf:1:p1:0",
    issue: int = 1,
    step: str = "p1",
    cause: str = "unknown",
    facts: dict | None = None,
    actions: list | None = None,
    notes: dict | None = None,
    fix_kinds: list[str] | None = None,
    raised_at: str = "2026-11-26T10:00:00+00:00",
    namespace: str = "",
) -> dict:
    return {
        "id": andon_id,
        "issue": issue,
        "step": step,
        "kind": "blocked",
        "cause": cause,
        "facts": facts
        if facts is not None
        else {"failed_step": "p1", "trigger": "step", "rule_ids": ["repair.gave_up"]},
        "actions": actions if actions is not None else [{"agent_may_answer": False}],
        "notes": notes or {},
        "fix_kinds": fix_kinds if fix_kinds is not None else ["diagnose"],
        "raised_at": raised_at,
        "namespace": namespace,
    }


class IssueListingClient:
    def __init__(self, open_issues: list[dict] | None = None) -> None:
        self.open_issues = open_issues or []
        self.list_all_calls = 0

    def list_all_issues(self, state: str) -> list[dict]:
        assert state == "open"
        self.list_all_calls += 1
        return list(self.open_issues)


@pytest.fixture
def local_client(tmp_path, monkeypatch):
    from ghdag.forge import get_forge

    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "example/repo",
                "andon": {"diagnose": {"enabled": True}},
                "paths": {"exec_jsonl": str(tmp_path / "exec.jsonl")},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
    reset_config_cache()
    yield get_forge()
    reset_config_cache()


def _raise_unknown(local_client, tmp_path, number: int | None = None) -> Andon:
    if number is None:
        number = local_client.issue_create("target", "body")
    andon = Andon(
        id=f"wf:{number}:p1:0",
        kind="blocked",
        issue=number,
        step="p1",
        summary="tests",
        facts={"failed_step": "p1", "trigger": "step", "rule_ids": ["repair.gave_up"]},
        options=[],
    )
    raise_andon(local_client, andon, metrics_path=tmp_path / "m.jsonl")
    return andon


def test_diagnosis_signature_uses_only_normalized_cause_fields():
    """Refs #8077 AC1."""
    a = Andon(
        id="wf:1:p1:0",
        kind="blocked",
        issue=1,
        step="p1",
        summary="one",
        facts={
            "failed_step": "p1",
            "rule_ids": ["b.rule", "a.rule"],
            "failure_class": "ENGINE_ERROR",
        },
    )
    b = Andon(
        id="wf:99:p1:0",
        kind="blocked",
        issue=99,
        step="p1",
        summary="two",
        evidence="stderr",
        facts={
            "failed_step": "p1",
            "rule_ids": ["a.rule", "b.rule"],
            "failure_class": "ENGINE_ERROR",
        },
    )
    assert diagnosis_signature(a) == diagnosis_signature(b)
    different_step = Andon(
        id="wf:1:p2:0",
        kind="blocked",
        issue=1,
        step="p2",
        summary="",
        facts={
            "failed_step": "p2",
            "rule_ids": ["a.rule", "b.rule"],
            "failure_class": "ENGINE_ERROR",
        },
    )
    assert diagnosis_signature(a) != diagnosis_signature(different_step)


def test_plan_prefers_open_issue_and_lists_issues_once(tmp_path):
    """Refs #8077 AC2."""
    record_a = _record(andon_id="wf:1:p1:0", issue=1)
    record_b = _record(andon_id="wf:2:p1:0", issue=2)
    signature = diagnosis_signature(record_a)
    client = IssueListingClient(
        [
            {"number": 40, "body": f"<!-- issuesmith-diagnosis: {signature} -->", "state": "open"},
            {"number": 10, "body": f"<!-- issuesmith-diagnosis: {signature} -->", "state": "open"},
            {
                "number": 5,
                "body": f"<!-- issuesmith-diagnosis: {signature} -->",
                "state": "open",
                "pull_request": {},
            },
        ]
    )
    plan = plan_diagnoses(
        client,
        [record_a, record_b],
        now=_FIXED_NOW,
        active_issues=set(),
        settings=_settings(),
        ledger_path=tmp_path / "ledger.jsonl",
    )
    assert client.list_all_calls == 1
    assert len(plan.bundled) == 2
    assert all(item["bundled_with"] == "issue#10" for item in plan.bundled)
    assert plan.requested == []


def test_plan_reuses_oldest_diagnosed_open_andon(tmp_path):
    """Refs #8077 AC3."""
    facts = {"failed_step": "p1", "rule_ids": ["repair.gave_up"]}
    older = _record(
        andon_id="wf:9:p1:0",
        issue=9,
        notes={"diagnosis": "bundled:issue#1"},
        raised_at="2026-11-26T08:00:00+00:00",
        facts=facts,
    )
    newer = _record(
        andon_id="wf:9:p1:1",
        issue=9,
        notes={"diagnosis": "bundled:issue#2"},
        raised_at="2026-11-26T11:00:00+00:00",
        facts=facts,
    )
    target = _record(andon_id="wf:10:p1:0", issue=10, facts=facts)
    client = IssueListingClient()
    plan = plan_diagnoses(
        client,
        [older, newer, target],
        now=_FIXED_NOW,
        active_issues=set(),
        settings=_settings(),
        ledger_path=tmp_path / "ledger.jsonl",
    )
    assert len(plan.bundled) == 1
    assert plan.bundled[0]["bundled_with"] == "andon:wf:9:p1:0"


def test_plan_reuses_only_recent_valid_ledger_rows(tmp_path):
    """Refs #8077 AC4."""
    record = _record()
    signature = diagnosis_signature(record)
    ledger = tmp_path / "ledger.jsonl"
    lines = [
        {"signature": signature, "andon_id": "wf:8:p1:0", "created_at": "not-a-date"},
        "not json",
        {
            "signature": signature,
            "andon_id": "wf:old:p1:0",
            "created_at": "2026-11-17T12:00:00+00:00",
            "fix_kind": "diagnose",
        },
        {
            "signature": signature,
            "andon_id": "wf:edge:p1:0",
            "created_at": "2026-11-19T12:00:00+00:00",
            "fix_kind": "diagnose",
        },
        {
            "signature": signature,
            "andon_id": "wf:pick:p1:0",
            "created_at": "2026-11-23T12:00:00+00:00",
            "fix_kind": "diagnose",
        },
    ]
    ledger.write_text("\n".join(json.dumps(x) if isinstance(x, dict) else x for x in lines) + "\n")
    client = IssueListingClient()
    plan = plan_diagnoses(
        client,
        [record],
        now=_FIXED_NOW,
        active_issues=set(),
        settings=_settings(reuse_days=7),
        ledger_path=ledger,
    )
    assert len(plan.bundled) == 1
    assert plan.bundled[0]["bundled_with"] == "ledger:wf:pick:p1:0"


def test_apply_bundled_writes_note_then_machine_comment(local_client, tmp_path):
    """Refs #8077 AC5."""
    andon = _raise_unknown(local_client, tmp_path)
    records = list_open_records(local_client)
    signature = diagnosis_signature(records[0])
    plan = DiagnosePlanShim.bundled(
        andon_id=andon.id,
        issue=andon.issue,
        signature=signature,
        bundled_with="issue#99",
        fix_kind="",
    )
    exec_path = tmp_path / "exec.jsonl"
    exec_path.write_text("", encoding="utf-8")
    before = len(local_client.get_issue_comments(andon.issue))
    apply_diagnose_plan(local_client, plan, exec_jsonl=exec_path)
    comments = local_client.get_issue_comments(andon.issue)
    assert len(comments) == before + 2
    body = comments[-1]["body"]
    assert body.startswith("```andon-diagnosis")
    parsed = yaml.safe_load(body.split("```andon-diagnosis\n", 1)[1].rsplit("```", 1)[0])
    assert set(parsed) == {"andon_id", "signature", "bundled_with", "fix_kind"}
    refreshed = list_open_records(local_client)[0]
    assert refreshed["notes"]["diagnosis"] == "bundled:issue#99"
    assert exec_path.read_text(encoding="utf-8") == ""


def test_apply_request_is_at_most_once_when_trigger_fails(local_client, tmp_path, monkeypatch):
    """Refs #8077 AC6."""
    andon = _raise_unknown(local_client, tmp_path)
    records = list_open_records(local_client)
    signature = diagnosis_signature(records[0])
    plan = DiagnosePlanShim.requested(
        andon_id=andon.id,
        issue=andon.issue,
        signature=signature,
    )
    exec_path = tmp_path / "exec.jsonl"
    calls: list[list[str]] = []

    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "boom"

    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: calls.append(list(cmd)) or _Proc(),
    )
    applied = apply_diagnose_plan(local_client, plan, exec_jsonl=exec_path)
    assert applied.failures and applied.failures[0]["returncode"] == 1
    refreshed = list_open_records(local_client)[0]
    assert refreshed["notes"]["diagnosis_requested"] == andon.id
    assert len(calls) == 1
    replan = plan_diagnoses(
        local_client,
        list_open_records(local_client),
        now=_FIXED_NOW,
        active_issues=set(),
        settings=_settings(),
        ledger_path=tmp_path / "ledger.jsonl",
    )
    assert replan.bundled == []
    assert replan.requested == []


def test_plan_excludes_auto_answer_diagnose_step_and_active_issue(tmp_path):
    """Refs #8077 AC7."""
    auto = _record(
        andon_id="wf:1:p1:0",
        actions=[{"agent_may_answer": True}],
    )
    diagnose_step = _record(andon_id="wf:2:diagnose:0", step="diagnose", facts={"failed_step": "diagnose"})
    failed_step = _record(
        andon_id="wf:3:p1:0",
        facts={"failed_step": "diagnose"},
    )
    active = _record(andon_id="wf:4:p1:0", issue=4)
    client = IssueListingClient()
    plan = plan_diagnoses(
        client,
        [auto, diagnose_step, failed_step, active],
        now=_FIXED_NOW,
        active_issues={4},
        settings=_settings(),
        ledger_path=tmp_path / "ledger.jsonl",
    )
    assert plan.bundled == []
    assert plan.requested == []


class DiagnosePlanShim:
    @staticmethod
    def bundled(**entry: object):
        from issuesmith.diagnose import DiagnosePlan

        return DiagnosePlan(
            bundled=[
                {
                    "andon_id": entry["andon_id"],
                    "issue": entry["issue"],
                    "signature": entry["signature"],
                    "bundled_with": entry["bundled_with"],
                    "fix_kind": entry.get("fix_kind", ""),
                }
            ]
        )

    @staticmethod
    def requested(**entry: object):
        from issuesmith.diagnose import DiagnosePlan

        return DiagnosePlan(
            requested=[
                {
                    "andon_id": entry["andon_id"],
                    "issue": entry["issue"],
                    "signature": entry["signature"],
                    "namespace": "issuesmith",
                    "handler": "diagnose",
                }
            ]
        )
