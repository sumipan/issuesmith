"""Deterministic diagnosis planning: bundle open andons or request one diagnose DAG run."""
from __future__ import annotations

import importlib
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
from issuesmith.config import AndonDiagnoseConfig, IssuesmithConfig
from issuesmith.errors import IssuesmithError

_DIAGNOSIS_MARKER = re.compile(
    r"<!--\s*issuesmith-diagnosis:\s*([0-9a-f]{12})\s*-->", re.IGNORECASE
)
_FENCE_OPEN = "```andon-diagnosis"
_FENCE_CLOSE = "```"
_PROPOSAL_FENCE = re.compile(
    r"```andon-diagnosis\s*\n(.*?)```",
    re.DOTALL | re.IGNORECASE,
)
_ISSUE_MD = "issue.md"
_REL_PATH_BAD = re.compile(r"(^|/)\.\.(/|$)|^\.\.")
_MISSING = object()


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
    facts_value = record.get("facts")
    facts: Mapping[str, Any] = facts_value if isinstance(facts_value, Mapping) else {}
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
        fix_kind_value = row.get("fix_kind")
        fix_kind = fix_kind_value if isinstance(fix_kind_value, str) else ""
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


class ProposalError(IssuesmithError, ValueError):
    """Raised when LLM diagnosis output fails deterministic validation."""

    code = "INVALID_VALUE"

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(
            message,
            hint=hint or f"fix the andon diagnosis proposal: {message}",
        )


@dataclass(frozen=True)
class Proposal:
    fix_kind: str
    summary: str
    payload: dict[str, Any]


_FIX_KIND_KEYS: dict[str, frozenset[str]] = {
    "edit_issue": frozenset({"allow_paths_add", "allow_paths_remove", "body_patch"}),
    "clean_env": frozenset({"steps", "rerun_from"}),
    "fix_line": frozenset({"target_repo", "title", "body"}),
    "review_override": frozenset({"assessment", "resume_from"}),
    "diagnose": frozenset({"unknowns"}),
}


def _last_proposal_fence(text: str) -> Mapping[str, Any]:
    matches = list(_PROPOSAL_FENCE.finditer(text))
    if not matches:
        raise ProposalError(
            "missing andon-diagnosis block",
            hint="wrap the proposal in a ```andon-diagnosis fenced block",
        )
    body = matches[-1].group(1)
    try:
        data = yaml.safe_load(body)
    except yaml.YAMLError as exc:
        raise ProposalError(f"invalid proposal yaml: {exc}") from exc
    if not isinstance(data, Mapping):
        raise ProposalError("proposal must be a mapping")
    return data


def _validate_rel_path(path: str) -> None:
    p = path.strip()
    if not p:
        raise ProposalError("empty path")
    if p.startswith("/") or "\\" in p:
        raise ProposalError("absolute path not allowed")
    if "**" in p or p.endswith("/**") or p.startswith("**/"):
        raise ProposalError("glob path not allowed")
    if _REL_PATH_BAD.search(p):
        raise ProposalError("parent traversal not allowed")


def _validate_path_list(value: Any, *, field: str) -> list[str]:
    if value is _MISSING:
        return []
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        raise ProposalError(f"{field} must be a list of strings")
    out: list[str] = []
    for item in value:
        _validate_rel_path(item)
        out.append(item.strip())
    return out


def _issue_lines(issue_body: str) -> list[str]:
    return issue_body.splitlines()


def _apply_hunk(
    lines: list[str],
    old_start: int,
    old_count: int,
    new_count: int,
    hunk_lines: list[str],
) -> int:
    idx = old_start if old_count == 0 else old_start - 1
    if idx < 0 or idx > len(lines):
        raise ProposalError("body_patch hunk range is invalid")
    old_seen = 0
    new_seen = 0
    for line in hunk_lines:
        if line.startswith(" "):
            if idx >= len(lines) or lines[idx] != line[1:]:
                raise ProposalError("body_patch hunk mismatch")
            idx += 1
            old_seen += 1
            new_seen += 1
        elif line.startswith("-"):
            if idx >= len(lines) or lines[idx] != line[1:]:
                raise ProposalError("body_patch hunk mismatch")
            idx += 1
            old_seen += 1
        elif line.startswith("+"):
            new_seen += 1
        elif line == "\\ No newline at end of file":
            pass
        else:
            raise ProposalError("invalid body_patch hunk line")
    if old_seen != old_count or new_seen != new_count:
        raise ProposalError("body_patch hunk count mismatch")
    return idx


def _validate_body_patch(patch: str, issue_body: str) -> None:
    if not isinstance(patch, str) or not patch.strip():
        raise ProposalError("body_patch must be a non-empty string")
    target_files: set[str] = set()
    hunks: list[tuple[int, int, int, list[str]]] = []
    current_hunk: list[str] = []
    old_start = 0
    old_count = 0
    new_count = 0
    in_hunk = False
    old_headers = 0
    new_headers = 0
    for line in patch.splitlines():
        if line.startswith("--- ") or line.startswith("+++ "):
            name = line[4:].strip().split("\t", 1)[0]
            if name not in ("issue.md", "a/issue.md", "b/issue.md"):
                raise ProposalError("body_patch must target issue.md only")
            target_files.add(name.removeprefix("a/").removeprefix("b/"))
            if line.startswith("--- "):
                old_headers += 1
            else:
                new_headers += 1
            continue
        if line.startswith("@@ "):
            if in_hunk:
                hunks.append((old_start, old_count, new_count, current_hunk))
            m = re.fullmatch(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?", line)
            if m is None:
                raise ProposalError("invalid body_patch hunk header")
            old_start = int(m.group(1))
            old_count = int(m.group(2) or "1")
            new_count = int(m.group(4) or "1")
            current_hunk = []
            in_hunk = True
            continue
        if in_hunk:
            current_hunk.append(line)
    if in_hunk:
        hunks.append((old_start, old_count, new_count, current_hunk))
    if (
        "issue.md" not in target_files
        or len(target_files) != 1
        or old_headers != 1
        or new_headers != 1
        or not hunks
    ):
        raise ProposalError("body_patch must target issue.md only")
    lines = _issue_lines(issue_body)
    previous_end = 0
    for start, count, added_count, hunk in hunks:
        position = start if count == 0 else start - 1
        if position < previous_end:
            raise ProposalError("body_patch hunks overlap or are out of order")
        previous_end = _apply_hunk(lines, start, count, added_count, hunk)


def _payload_edit_issue(data: Mapping[str, Any], issue_body: str) -> dict[str, Any]:
    allow_add = _validate_path_list(data.get("allow_paths_add", _MISSING), field="allow_paths_add")
    allow_remove = _validate_path_list(
        data.get("allow_paths_remove", _MISSING), field="allow_paths_remove"
    )
    body_patch = data.get("body_patch", _MISSING)
    if body_patch is not _MISSING and not isinstance(body_patch, str):
        raise ProposalError("body_patch must be a string")
    patch_s = body_patch.strip() if isinstance(body_patch, str) else ""
    if not allow_add and not allow_remove and not patch_s:
        raise ProposalError("edit_issue needs a non-empty change")
    if patch_s:
        _validate_body_patch(patch_s, issue_body)
    return {
        "allow_paths_add": allow_add,
        "allow_paths_remove": allow_remove,
        **({"body_patch": patch_s} if patch_s else {}),
    }


def _payload_clean_env(data: Mapping[str, Any], config: IssuesmithConfig) -> dict[str, Any]:
    steps_raw = data.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw or len(steps_raw) > 10:
        raise ProposalError("steps must contain 1-10 strings")
    steps = [s.strip() for s in steps_raw if isinstance(s, str) and s.strip()]
    if not steps or len(steps) != len(steps_raw):
        raise ProposalError("steps must contain 1-10 strings")
    rerun = data.get("rerun_from")
    if not isinstance(rerun, str) or rerun.strip() not in config.steps:
        raise ProposalError("rerun_from must be a configured step id")
    return {"steps": steps, "rerun_from": rerun.strip()}


def _payload_fix_line(
    data: Mapping[str, Any],
    *,
    signature: str,
    config: IssuesmithConfig,
) -> dict[str, Any]:
    target = data.get("target_repo")
    title = data.get("title")
    body = data.get("body")
    if not isinstance(target, str) or target.strip() not in config.supported_repos:
        raise ProposalError("target_repo must be in supported_repos")
    if not isinstance(title, str) or not title.strip():
        raise ProposalError("title must be a non-empty string")
    if not isinstance(body, str) or not body.strip():
        raise ProposalError("body must be a non-empty string")
    marker = f"<!-- issuesmith-diagnosis: {signature} -->"
    text = body.replace(marker, "").rstrip()
    text = f"{text}\n{marker}"
    return {"target_repo": target.strip(), "title": title.strip(), "body": text}


def _payload_review_override(data: Mapping[str, Any], config: IssuesmithConfig) -> dict[str, Any]:
    assessment = data.get("assessment")
    resume = data.get("resume_from")
    if not isinstance(assessment, str) or not assessment.strip():
        raise ProposalError("assessment must be a non-empty string")
    if not isinstance(resume, str) or resume.strip() not in config.steps:
        raise ProposalError("resume_from must be a configured step id")
    return {"assessment": assessment.strip(), "resume_from": resume.strip()}


def _payload_diagnose(data: Mapping[str, Any]) -> dict[str, Any]:
    unknowns = data.get("unknowns")
    if not isinstance(unknowns, list) or not unknowns:
        raise ProposalError("unknowns must be a non-empty list")
    items = [u.strip() for u in unknowns if isinstance(u, str) and u.strip()]
    if len(items) != len(unknowns):
        raise ProposalError("unknowns must be a non-empty list")
    return {"unknowns": items}


def parse_proposal(
    text: str,
    *,
    andon_record: Mapping[str, Any],
    issue_body: str,
    config: IssuesmithConfig,
    allowed_fix_kinds: list[str] | None = None,
) -> Proposal:
    """Parse and validate the last andon-diagnosis block in LLM output."""
    parsed = _last_proposal_fence(text)
    if not all(isinstance(key, str) for key in parsed):
        raise ProposalError("proposal keys must be strings")
    data = dict(parsed)
    summary_raw = data.pop("summary", None)
    fix_kind_raw = data.pop("fix_kind", None)
    if not isinstance(fix_kind_raw, str) or not fix_kind_raw.strip():
        raise ProposalError("fix_kind is required")
    fix_kind = fix_kind_raw.strip()
    allowed = allowed_fix_kinds
    if allowed is None:
        fix_kinds = andon_record.get("fix_kinds")
        allowed = list(fix_kinds) if isinstance(fix_kinds, list) else []
        if "diagnose" not in allowed:
            allowed = [*allowed, "diagnose"]
    if fix_kind not in allowed:
        raise ProposalError("fix_kind not allowed")
    if not isinstance(summary_raw, str):
        raise ProposalError("summary is required")
    summary = summary_raw.strip()
    if not summary or len(summary) > 1000:
        raise ProposalError("summary must be 1-1000 characters")
    permitted = _FIX_KIND_KEYS.get(fix_kind)
    if permitted is None:
        raise ProposalError("unknown fix_kind")
    extra = set(data) - permitted
    if extra:
        raise ProposalError(f"unexpected keys: {', '.join(sorted(extra))}")
    signature = diagnosis_signature(andon_record)
    if fix_kind == "edit_issue":
        payload = _payload_edit_issue(data, issue_body)
    elif fix_kind == "clean_env":
        payload = _payload_clean_env(data, config)
    elif fix_kind == "fix_line":
        payload = _payload_fix_line(data, signature=signature, config=config)
    elif fix_kind == "review_override":
        payload = _payload_review_override(data, config)
    else:
        payload = _payload_diagnose(data)
    return Proposal(fix_kind=fix_kind, summary=summary, payload=payload)


def proposal_comment_body(
    proposal: Proposal,
    *,
    andon_id: str,
    signature: str,
    diagnosed_at: datetime,
) -> str:
    data: dict[str, Any] = {
        "andon_id": andon_id,
        "signature": signature,
        "fix_kind": proposal.fix_kind,
        "summary": proposal.summary,
        "diagnosed_at": diagnosed_at.astimezone(timezone.utc).isoformat(),
    }
    data.update(proposal.payload)
    yaml_body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"{_FENCE_OPEN}\n{yaml_body}{_FENCE_CLOSE}\n"


def append_diagnosis_ledger(
    path: Path,
    *,
    andon_id: str,
    issue: int,
    signature: str,
    fix_kind: str,
    summary: str,
    diagnosed_at: datetime,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "andon_id": andon_id,
        "issue": issue,
        "signature": signature,
        "fix_kind": fix_kind,
        "summary": summary,
        "ts": diagnosed_at.astimezone(timezone.utc).isoformat(),
    }
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def proposal_to_dict(
    proposal: Proposal,
    *,
    andon_id: str,
    signature: str,
    diagnosed_at: datetime,
) -> dict[str, Any]:
    return {
        "andon_id": andon_id,
        "signature": signature,
        "fix_kind": proposal.fix_kind,
        "summary": proposal.summary,
        "diagnosed_at": diagnosed_at.astimezone(timezone.utc).isoformat(),
        **proposal.payload,
    }


def invoke_on_proposal_hook(spec: str, payload: Mapping[str, Any]) -> None:
    module_name, func_name = spec.split(":", 1)
    mod = importlib.import_module(module_name.strip())
    func = getattr(mod, func_name.strip(), None)
    if not callable(func):
        raise TypeError(f"{spec} is not callable")
    func(dict(payload))


def persist_diagnosis_success(
    client: Any,
    proposal: Proposal,
    *,
    andon_record: Mapping[str, Any],
    ledger_path: Path,
    on_proposal: str | None,
) -> None:
    andon_id = str(andon_record.get("id") or "")
    issue = andon_record.get("issue")
    if not andon_id or not isinstance(issue, int):
        return
    signature = diagnosis_signature(andon_record)
    now = datetime.now(timezone.utc)
    note(client, andon_id, "diagnosis", proposal.fix_kind)
    client.issue_comment(
        issue,
        proposal_comment_body(proposal, andon_id=andon_id, signature=signature, diagnosed_at=now),
    )
    append_diagnosis_ledger(
        ledger_path,
        andon_id=andon_id,
        issue=issue,
        signature=signature,
        fix_kind=proposal.fix_kind,
        summary=proposal.summary,
        diagnosed_at=now,
    )
    if on_proposal:
        try:
            invoke_on_proposal_hook(
                on_proposal,
                proposal_to_dict(proposal, andon_id=andon_id, signature=signature, diagnosed_at=now),
            )
        except Exception as exc:
            print(f"[diagnose] on_proposal hook failed: {exc}", file=sys.stderr)


def _truncate_reason(text: str, limit: int = 1000) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def persist_diagnosis_failure(
    client: Any,
    *,
    andon_record: Mapping[str, Any],
    reason: str,
) -> None:
    andon_id = str(andon_record.get("id") or "")
    issue = andon_record.get("issue")
    if not andon_id or not isinstance(issue, int):
        return
    try:
        note(client, andon_id, "diagnosis", "failed")
    except Exception as exc:
        print(f"[diagnose] failure note failed: {exc}", file=sys.stderr)
    msg = _truncate_reason(reason)
    client.issue_comment(issue, f"Diagnosis failed: {msg}\n")


__all__ = [
    "DiagnosePlan",
    "Proposal",
    "ProposalError",
    "append_diagnosis_ledger",
    "apply_diagnose_plan",
    "invoke_on_proposal_hook",
    "parse_proposal",
    "persist_diagnosis_failure",
    "persist_diagnosis_success",
    "plan_diagnoses",
    "proposal_comment_body",
    "proposal_to_dict",
]
