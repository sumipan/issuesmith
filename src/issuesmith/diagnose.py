"""Deterministic diagnosis planning: bundle open andons or request one diagnose DAG run."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import yaml

from issuesmith.andon import diagnosis_signature, note
from issuesmith.config import AndonDiagnoseConfig

_DIAGNOSIS_MARKER = re.compile(
    r"<!--\s*issuesmith-diagnosis:\s*([0-9a-f]{12})\s*-->", re.IGNORECASE
)
_FENCE_OPEN = "```andon-diagnosis"
_FENCE_CLOSE = "```"


@dataclass
class DiagnosePlan:
    bundled: list[dict[str, Any]] = field(default_factory=list)
    requested: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)


def _failed_step_str(facts: Mapping[str, Any]) -> str | None:
    failed = facts.get("failed_step")
    if isinstance(failed, str) and failed:
        return failed
    return None


def _is_eligible(
    record: Mapping[str, Any],
    *,
    settings: AndonDiagnoseConfig,
    active_issues: set[int],
) -> bool:
    actions = record.get("actions") or []
    if any(a.get("agent_may_answer") is True for a in actions if isinstance(a, dict)):
        return False
    notes = record.get("notes") or {}
    if not isinstance(notes, dict):
        notes = {}
    if "diagnosis" in notes or "diagnosis_requested" in notes:
        return False
    facts = record.get("facts") if isinstance(record.get("facts"), dict) else {}
    failed_step = _failed_step_str(facts)
    step = str(record.get("step") or "")
    if failed_step == settings.step or step == settings.step:
        return False
    issue = record.get("issue")
    if isinstance(issue, int) and issue in active_issues:
        return False
    return True


def _fix_kind_from_record(record: Mapping[str, Any]) -> str:
    fix_kinds = record.get("fix_kinds")
    if isinstance(fix_kinds, list) and len(fix_kinds) == 1 and isinstance(fix_kinds[0], str):
        return fix_kinds[0]
    return ""


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _read_ledger_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        rows.append(data)
    return rows


def _ledger_candidate(
    rows: list[dict[str, Any]],
    *,
    signature: str,
    now: datetime,
    reuse_days: int,
) -> tuple[str, str] | None:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    cutoff = now - timedelta(days=reuse_days)
    best: tuple[datetime, str, str] | None = None
    for row in rows:
        sig = row.get("signature")
        andon_id = row.get("andon_id")
        created_at = row.get("created_at")
        if not isinstance(sig, str) or sig != signature:
            continue
        if not isinstance(andon_id, str) or not andon_id:
            continue
        dt = _parse_iso_datetime(created_at)
        if dt is None:
            continue
        if dt > now or dt < cutoff:
            continue
        fix_kind = row.get("fix_kind") if isinstance(row.get("fix_kind"), str) else ""
        if best is None or dt > best[0]:
            best = (dt, andon_id, fix_kind)
    if best is None:
        return None
    return f"ledger:{best[1]}", best[2]


def _index_open_issues_by_signature(
    open_issues: list[dict[str, Any]],
) -> dict[str, list[int]]:
    out: dict[str, list[int]] = {}
    for issue in open_issues:
        if issue.get("pull_request") is not None:
            continue
        number = issue.get("number")
        body = issue.get("body") or ""
        if not isinstance(number, int) or not isinstance(body, str):
            continue
        for match in _DIAGNOSIS_MARKER.finditer(body):
            sig = match.group(1).lower()
            out.setdefault(sig, []).append(number)
    for sig in out:
        out[sig] = sorted(set(out[sig]))
    return out


def _andon_bundle_candidate(
    records: list[dict[str, Any]],
    *,
    signature: str,
    self_id: str,
) -> tuple[str, str] | None:
    candidates = [
        r
        for r in records
        if r.get("id") != self_id
        and diagnosis_signature(r) == signature
        and isinstance((r.get("notes") or {}).get("diagnosis"), str)
    ]
    if not candidates:
        return None
    def sort_key(record: Mapping[str, Any]) -> tuple[datetime, str]:
        raised_at = _parse_iso_datetime(record.get("raised_at"))
        return (
            raised_at or datetime.max.replace(tzinfo=timezone.utc),
            str(record.get("id") or ""),
        )

    candidates.sort(key=sort_key)
    chosen = candidates[0]
    ref = f"andon:{chosen['id']}"
    return ref, _fix_kind_from_record(chosen)


def _record_namespace(record: Mapping[str, Any]) -> str:
    ns = record.get("namespace")
    if isinstance(ns, str) and ns.strip():
        return ns.strip()
    from issuesmith.config import get_config

    return get_config().workflow_name()


def _bundle_ref(
    *,
    signature: str,
    open_issue_index: dict[str, list[int]],
    records: list[dict[str, Any]],
    self_id: str,
    ledger_rows: list[dict[str, Any]],
    now: datetime,
    settings: AndonDiagnoseConfig,
) -> tuple[str, str] | None:
    issue_nums = open_issue_index.get(signature)
    if issue_nums:
        return f"issue#{issue_nums[0]}", ""
    andon_ref = _andon_bundle_candidate(records, signature=signature, self_id=self_id)
    if andon_ref is not None:
        return andon_ref
    return _ledger_candidate(
        ledger_rows,
        signature=signature,
        now=now,
        reuse_days=settings.reuse_days,
    )


def plan_diagnoses(
    client: Any,
    records: list[dict[str, Any]],
    *,
    now: datetime,
    active_issues: set[int],
    settings: AndonDiagnoseConfig,
    ledger_path: Path,
) -> DiagnosePlan:
    """Build a diagnose plan without mutating forge state."""
    if not settings.enabled:
        return DiagnosePlan()
    eligible = [
        r for r in records if _is_eligible(r, settings=settings, active_issues=active_issues)
    ]
    if not eligible:
        return DiagnosePlan()

    list_all = getattr(client, "list_all_issues", None)
    open_issues: list[dict[str, Any]] = []
    if callable(list_all):
        try:
            open_issues = list(list_all("open"))
        except Exception:
            open_issues = []
    issue_index = _index_open_issues_by_signature(open_issues)
    ledger_rows = _read_ledger_rows(ledger_path)

    bundled: list[dict[str, Any]] = []
    requested: list[dict[str, Any]] = []
    for record in eligible:
        signature = diagnosis_signature(record)
        ref = _bundle_ref(
            signature=signature,
            open_issue_index=issue_index,
            records=records,
            self_id=str(record.get("id") or ""),
            ledger_rows=ledger_rows,
            now=now,
            settings=settings,
        )
        base = {
            "andon_id": record.get("id"),
            "issue": record.get("issue"),
            "signature": signature,
        }
        if ref is not None:
            bundled_with, fix_kind = ref
            bundled.append({**base, "bundled_with": bundled_with, "fix_kind": fix_kind})
        else:
            requested.append(
                {**base, "namespace": _record_namespace(record), "handler": settings.handler}
            )
    return DiagnosePlan(bundled=bundled, requested=requested)


def _diagnosis_comment(
    *,
    andon_id: str,
    signature: str,
    bundled_with: str,
    fix_kind: str,
) -> str:
    data = {
        "andon_id": andon_id,
        "signature": signature,
        "bundled_with": bundled_with,
        "fix_kind": fix_kind,
    }
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"{_FENCE_OPEN}\n{body}{_FENCE_CLOSE}\n"


def _append_failure(
    failures: list[dict[str, Any]],
    *,
    andon_id: str,
    operation: str,
    error: str,
    **extra: Any,
) -> None:
    row: dict[str, Any] = {"andon_id": andon_id, "operation": operation, "error": error}
    row.update(extra)
    failures.append(row)


def apply_diagnose_plan(
    client: Any,
    plan: DiagnosePlan,
    *,
    exec_jsonl: Path,
) -> DiagnosePlan:
    """Apply bundled notes/comments and at-most-once diagnose triggers."""
    failures = list(plan.failures)
    for entry in plan.bundled:
        andon_id = str(entry.get("andon_id") or "")
        issue = entry.get("issue")
        bundled_with = str(entry.get("bundled_with") or "")
        signature = str(entry.get("signature") or "")
        fix_kind = str(entry.get("fix_kind") or "")
        if not andon_id or not isinstance(issue, int):
            continue
        try:
            note(client, andon_id, "diagnosis", f"bundled:{bundled_with}")
        except Exception as exc:
            _append_failure(
                failures, andon_id=andon_id, operation="note", error=str(exc)
            )
            continue
        try:
            client.issue_comment(
                issue,
                _diagnosis_comment(
                    andon_id=andon_id,
                    signature=signature,
                    bundled_with=bundled_with,
                    fix_kind=fix_kind,
                ),
            )
        except Exception as exc:
            _append_failure(
                failures, andon_id=andon_id, operation="comment", error=str(exc)
            )

    for entry in plan.requested:
        andon_id = str(entry.get("andon_id") or "")
        issue = entry.get("issue")
        if not andon_id or not isinstance(issue, int):
            continue
        try:
            note(client, andon_id, "diagnosis_requested", andon_id)
        except Exception as exc:
            _append_failure(
                failures, andon_id=andon_id, operation="note", error=str(exc)
            )
            continue
        handler = str(entry.get("handler") or "diagnose")
        namespace = str(entry.get("namespace") or "issuesmith")
        reason = f"diagnose {andon_id}"
        cmd = [
            sys.executable,
            "-m",
            "ghdag",
            "trigger",
            str(issue),
            "--handler",
            handler,
            "--workflow",
            namespace,
            "--exec-jsonl",
            str(exec_jsonl),
            "--redispatch",
            "--reason",
            reason,
        ]
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
        except Exception as exc:
            _append_failure(
                failures,
                andon_id=andon_id,
                operation="trigger",
                error=str(exc),
            )
            continue
        if proc.returncode != 0:
            stdout_tail = (proc.stdout or "").strip()[-500:]
            stderr_tail = (proc.stderr or "").strip()[-500:]
            _append_failure(
                failures,
                andon_id=andon_id,
                operation="trigger",
                error=stderr_tail or stdout_tail or f"exit {proc.returncode}",
                returncode=proc.returncode,
                stdout_tail=stdout_tail,
                stderr_tail=stderr_tail,
            )
    return replace(plan, failures=failures)


__all__ = ["DiagnosePlan", "plan_diagnoses", "apply_diagnose_plan"]
