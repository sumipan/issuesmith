"""Deterministic diagnosis step for the reference develop line."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

from issuesmith.andon import diagnosis_signature, from_comment, list_open_records
from issuesmith.config import StepConfig, get_config
from issuesmith.contract import StepContext, StepResult
from issuesmith.diagnose import (
    ProposalError,
    parse_proposal,
    persist_diagnosis_failure,
    persist_diagnosis_success,
)
from issuesmith.engine import run_guarded_output
from issuesmith.forge import get_forge

_UUID_RE = re.compile(r"\buuid=([0-9a-f-]{36})\b", re.IGNORECASE)
_RESULT_RE = re.compile(r"\bresult=([^\s]+)")
_ANSWER_MARKER = "<!-- andon-answer -->"


def _issue_number(ctx: StepContext) -> int | None:
    raw = str(ctx.issue_number).strip()
    if raw.isdigit():
        return int(raw)
    return None


def _abs_worktree(ctx: StepContext) -> str | None:
    for candidate in (ctx.worktree_path, ctx.target_worktree_path):
        text = candidate.strip()
        if not text:
            continue
        path = Path(text)
        if not path.is_absolute():
            path = get_config().root / path
        if path.is_dir():
            return str(path.resolve())
    return None


def _readable_file(path: Path) -> str | None:
    try:
        if path.is_file() and os.access(path, os.R_OK):
            return str(path.resolve())
    except OSError:
        return None
    return None


def _parse_evidence(evidence: str) -> tuple[str | None, str | None]:
    uuid_match = _UUID_RE.search(evidence)
    result_match = _RESULT_RE.search(evidence)
    uuid_val = uuid_match.group(1) if uuid_match else None
    result_val = result_match.group(1) if result_match else None
    return uuid_val, result_val


def _issue_from_idempotency(key: str) -> int | None:
    parts = key.split(":")
    if len(parts) >= 3 and parts[2].isdigit():
        return int(parts[2])
    return None


def _latest_generation_rows(
    exec_path: Path,
    *,
    issue: int,
    workflow: str,
) -> list[dict[str, Any]]:
    if not exec_path.is_file():
        return []
    prefix = f"{workflow}:"
    issue_last_key: str | None = None
    rows: list[dict[str, Any]] = []
    try:
        lines = exec_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        key = str(row.get("idempotency_key") or "")
        if not key.startswith(prefix):
            continue
        row_issue = _issue_from_idempotency(key)
        if row_issue != issue:
            continue
        issue_last_key = key
        rows.append(row)
    if issue_last_key is None:
        return []
    return [r for r in rows if str(r.get("idempotency_key") or "") == issue_last_key]


def _result_from_exec(
    exec_path: Path,
    *,
    issue: int,
    failed_step: str,
    workflow: str,
) -> str | None:
    rows = _latest_generation_rows(exec_path, issue=issue, workflow=workflow)
    match: dict[str, Any] | None = None
    for row in rows:
        annotations = row.get("annotations") or {}
        step_name = annotations.get("step_name") if isinstance(annotations, dict) else None
        if step_name == failed_step:
            match = row
    if match is None:
        return None
    result_path = match.get("result_path")
    if not isinstance(result_path, str) or not result_path.strip():
        return None
    return _readable_file(_resolve_job_path(exec_path.parent, result_path))


def _stderr_from_audit(
    audit_path: Path,
    *,
    uuid_val: str,
    jobs_dir: Path,
) -> str | None:
    if not audit_path.is_file():
        return None
    last: str | None = None
    try:
        lines = audit_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        if str(row.get("uuid") or "") != uuid_val:
            continue
        stderr_path = row.get("stderr_path")
        if isinstance(stderr_path, str) and stderr_path.strip():
            last = stderr_path
    if last is None:
        return None
    return _readable_file(_resolve_job_path(jobs_dir, last))


def _resolve_job_path(jobs_dir: Path, path_text: str) -> Path:
    path = Path(path_text.strip())
    if not path.is_absolute():
        return jobs_dir / path
    return path


def _resolve_artifacts(
    record: Mapping[str, Any],
    *,
    issue: int,
    exec_jsonl: Path,
    workflow: str,
) -> tuple[str | None, str | None, str | None]:
    evidence = str(record.get("evidence") or "")
    uuid_val, result_val = _parse_evidence(evidence)
    result_path: str | None = None
    if result_val:
        result_path = _readable_file(_resolve_job_path(exec_jsonl.parent, result_val))
    facts = record.get("facts") or {}
    failed_step = facts.get("failed_step") if isinstance(facts, Mapping) else None
    if result_path is None and isinstance(failed_step, str) and failed_step:
        result_path = _result_from_exec(
            exec_jsonl,
            issue=issue,
            failed_step=failed_step,
            workflow=workflow,
        )
        if uuid_val is None:
            rows = _latest_generation_rows(exec_jsonl, issue=issue, workflow=workflow)
            for row in reversed(rows):
                annotations = row.get("annotations") or {}
                step_name = annotations.get("step_name") if isinstance(annotations, dict) else None
                if step_name == failed_step:
                    row_uuid = row.get("uuid")
                    if isinstance(row_uuid, str):
                        uuid_val = row_uuid
                    break
    stderr_path: str | None = None
    if uuid_val:
        audit_path = exec_jsonl.parent / "audit.jsonl"
        stderr_path = _stderr_from_audit(audit_path, uuid_val=uuid_val, jobs_dir=exec_jsonl.parent)
    return result_path, stderr_path, uuid_val


def _allowed_fix_kinds(record: Mapping[str, Any]) -> list[str]:
    raw = record.get("fix_kinds")
    kinds: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str) and item and item not in kinds:
                kinds.append(item)
    if "diagnose" not in kinds:
        kinds.append("diagnose")
    return kinds


def _pick_record(records: list[dict[str, Any]], issue: int) -> dict[str, Any] | None:
    matches = [
        r
        for r in records
        if r.get("issue") == issue
        and str((r.get("notes") or {}).get("diagnosis_requested") or "") == str(r.get("id") or "")
        and "diagnosis" not in (r.get("notes") or {})
    ]
    if not matches:
        return None

    def sort_key(record: Mapping[str, Any]) -> tuple[str, str]:
        return (str(record.get("raised_at") or ""), str(record.get("id") or ""))

    matches.sort(key=sort_key)
    return matches[0]


def _issue_comments(client: Any, number: int) -> list[dict[str, Any]]:
    comments = list(client.get_issue_comments(number))
    kept: list[dict[str, Any]] = []
    for comment in comments:
        body = str(comment.get("body") or "")
        if from_comment(body) is not None:
            continue
        if _ANSWER_MARKER in body:
            continue
        kept.append(comment)
    kept.sort(key=lambda row: str(row.get("created_at") or row.get("updated_at") or ""))
    return kept[-10:]


def _input_path(done_parent: Path, andon_id: str) -> Path:
    digest = hashlib.sha256(andon_id.encode("utf-8")).hexdigest()[:16]
    out_dir = done_parent / "diagnose"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{digest}.json"


def _write_input(path: Path, payload: Mapping[str, Any]) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(data + "\n")
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def _build_input(
    client: Any,
    record: dict[str, Any],
    *,
    issue: int,
    worktree: str | None,
    exec_jsonl: Path,
) -> dict[str, Any]:
    cfg = get_config()
    workflow = cfg.workflow_name()
    title_body = client.issue_get(
        issue,
        fields=["number", "title", "body", "labels"],
    )
    result_path, stderr_path, _uuid = _resolve_artifacts(
        record,
        issue=issue,
        exec_jsonl=exec_jsonl,
        workflow=workflow,
    )
    labels_raw = title_body.get("labels") or []
    labels: list[str] = []
    if isinstance(labels_raw, list):
        for item in labels_raw:
            if isinstance(item, dict):
                name = item.get("name")
                if isinstance(name, str):
                    labels.append(name)
            elif isinstance(item, str):
                labels.append(item)
    return {
        "andon": record,
        "signature": diagnosis_signature(record),
        "issue": {
            "number": issue,
            "title": str(title_body.get("title") or ""),
            "body": str(title_body.get("body") or ""),
            "labels": labels,
            "comments": _issue_comments(client, issue),
        },
        "paths": {
            "worktree": worktree,
            "result_path": result_path,
            "stderr_path": stderr_path,
            "exec_jsonl": str(exec_jsonl.resolve()),
        },
        "allowed_fix_kinds": _allowed_fix_kinds(record),
        "supported_repos": sorted(cfg.supported_repos),
    }


def _diagnosis_status_present(text: str, marker: str) -> bool:
    for line in text.splitlines():
        if line.strip() == f"PIPELINE_STATUS: {marker}":
            return True
    return False


def run(ctx: StepContext, step: StepConfig | None = None) -> StepResult:
    """Run deterministic diagnosis for one andon on this Issue."""
    issue = _issue_number(ctx)
    if issue is None:
        print("[diagnose] missing issue number", file=sys.stderr)
        return StepResult(status="done", markers=["DIAGNOSIS_DONE"])

    if step is None or not step.template:
        reason = "diagnose step has no template configured" if step is not None else "step config missing"
        print(f"[diagnose] {reason}", file=sys.stderr)
        return StepResult(status="done", markers=["DIAGNOSIS_DONE"])

    cfg = get_config()
    client = get_forge()
    records = list_open_records(client)
    record = _pick_record(records, issue)
    if record is None:
        return StepResult(status="done", markers=["DIAGNOSIS_DONE"])

    exec_jsonl = cfg.paths.exec_jsonl
    worktree = _abs_worktree(ctx)
    andon_id = str(record.get("id") or "")

    try:
        input_path = _input_path(cfg.paths.done_dir.parent, andon_id)
        payload = _build_input(
            client,
            record,
            issue=issue,
            worktree=worktree,
            exec_jsonl=exec_jsonl,
        )
        _write_input(input_path, payload)
    except Exception as exc:
        try:
            persist_diagnosis_failure(client, andon_record=record, reason=str(exc))
        except Exception as inner:
            print(f"[diagnose] failure terminal failed: {inner}", file=sys.stderr)
        return StepResult(status="done", markers=["DIAGNOSIS_DONE"])

    template_path = cfg.paths.template_dir / step.template
    role = cfg.andon.diagnose.role
    variables = [f"diagnosis_input={input_path.resolve()}"]

    try:
        rc, output = run_guarded_output(
            role,
            str(template_path),
            variables,
            ["DIAGNOSIS_DONE"],
            "DIAGNOSIS_FAILED",
            cwd=worktree,
        )
    except Exception as exc:
        try:
            persist_diagnosis_failure(client, andon_record=record, reason=str(exc))
        except Exception as inner:
            print(f"[diagnose] failure terminal failed: {inner}", file=sys.stderr)
        return StepResult(status="done", markers=["DIAGNOSIS_DONE"])
    if not output.strip():
        sidecar = input_path.with_name(input_path.name + ".out")
        if sidecar.is_file():
            try:
                output = sidecar.read_text(encoding="utf-8")
            except OSError:
                output = ""

    def fail(reason: str) -> StepResult:
        try:
            persist_diagnosis_failure(client, andon_record=record, reason=reason)
        except Exception as exc:
            print(f"[diagnose] failure terminal failed: {exc}", file=sys.stderr)
        return StepResult(status="done", markers=["DIAGNOSIS_DONE"])

    has_done = _diagnosis_status_present(output, "DIAGNOSIS_DONE")
    if rc != 0 or not has_done:
        tail = (output or "").strip()[-500:]
        return fail(tail or f"engine exit {rc}")

    issue_body = str((payload.get("issue") or {}).get("body") or "")
    try:
        proposal = parse_proposal(
            output,
            andon_record=record,
            issue_body=issue_body,
            config=cfg,
            allowed_fix_kinds=list(payload.get("allowed_fix_kinds") or []),
        )
    except ProposalError as exc:
        return fail(str(exc))

    try:
        persist_diagnosis_success(
            client,
            proposal,
            andon_record=record,
            ledger_path=cfg.paths.diagnoses,
            on_proposal=cfg.andon.diagnose.on_proposal,
        )
    except Exception as exc:
        return fail(str(exc))

    return StepResult(status="done", markers=["DIAGNOSIS_DONE"])
