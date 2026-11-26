"""Andon (paper lantern) — typed stop-the-line signals posted as Issue comment yaml blocks.

kind is one of: decision / blocked / broken
id format: <workflow>:<issue>:<step>:<gen>
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Collection, Iterator, Mapping, Protocol, runtime_checkable

import yaml

from issuesmith.contract import ANDON_KINDS
from issuesmith.projection import IssueState
from issuesmith.queue_store import QueueStore
from issuesmith.resume import resume

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

_CAUSES = (
    "transient",
    "contention",
    "environment",
    "issue_design",
    "line_defect",
    "review_failed",
    "unknown",
)

_CAUSE_STRENGTH: dict[str, int] = {
    "issue_design": 6,
    "environment": 5,
    "line_defect": 4,
    "review_failed": 3,
    "contention": 2,
    "transient": 1,
    "unknown": 0,
}

_TRANSIENT_FAILURE_CLASSES = frozenset({"TIMEOUT", "ENGINE_ERROR", "QUOTA_EXHAUSTED"})
_ENVIRONMENT_FAILURE_CLASSES = frozenset(
    {"AUTH", "ENGINE_ENVIRONMENT_ERROR", "INTERACTIVE_PROMPT"}
)

_BUILTIN_RULE_PREFIX_CAUSES: tuple[tuple[str, str], ...] = (
    ("tests.interrupted", "transient"),
    ("base_freshness.fetch_transient_exhausted", "transient"),
    ("review.branch_moved", "contention"),
    ("merge.rebase_conflict", "contention"),
    ("merge.push_rejected", "contention"),
    ("p0.base_rebase_conflict", "contention"),
    ("tests.environment", "environment"),
    ("tests.timeout", "environment"),
    ("design_lint", "issue_design"),
    ("ac_format", "issue_design"),
    ("ac_contract", "issue_design"),
    ("scope_breadth", "issue_design"),
    ("scope_coupling", "issue_design"),
    ("pr_scope", "issue_design"),
    ("deps", "issue_design"),
    ("migration_plan", "issue_design"),
    ("milestone_consistency", "issue_design"),
    ("allow_paths_forbidden", "issue_design"),
    ("merge.redo_failed", "line_defect"),
    ("review.", "review_failed"),
)


def _normalize_facts(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    return dict(raw)


def _rule_ids_from_andon(andon: Andon) -> list[str]:
    facts = andon.facts or {}
    rule_ids = facts.get("rule_ids")
    if isinstance(rule_ids, list):
        return sorted({str(r) for r in rule_ids if r})
    if andon.rule_id:
        return [andon.rule_id]
    return []


def _longest_prefix_cause(rule_id: str, table: list[tuple[str, str]]) -> str | None:
    best_len = -1
    best_cause: str | None = None
    for prefix, cause in table:
        matches = rule_id.startswith(prefix) if prefix.endswith(".") else (
            rule_id == prefix or rule_id.startswith(prefix + ".")
        )
        if matches:
            if len(prefix) > best_len:
                best_len = len(prefix)
                best_cause = cause
            elif len(prefix) == best_len:
                best_cause = cause
    return best_cause


def _cause_table() -> list[tuple[str, str]]:
    from issuesmith.config import get_config

    cfg = get_config().andon
    rows = list(_BUILTIN_RULE_PREFIX_CAUSES)
    for prefix, cause in cfg.causes.items():
        rows.append((str(prefix), str(cause)))
    return rows


def _rule_id_cause(rule_id: str) -> str | None:
    if rule_id.startswith("review.") and rule_id.endswith(".engine_error"):
        return "transient"
    return _longest_prefix_cause(rule_id, _cause_table())


def _stronger_cause(a: str, b: str) -> str:
    return a if _CAUSE_STRENGTH.get(a, 0) >= _CAUSE_STRENGTH.get(b, 0) else b


def classify_cause(andon: Andon) -> str:
    facts = andon.facts or {}
    if facts.get("trigger") == "gate_error":
        return "line_defect"
    failure_class = facts.get("failure_class")
    if isinstance(failure_class, str):
        if failure_class in _TRANSIENT_FAILURE_CLASSES:
            return "transient"
        if failure_class in _ENVIRONMENT_FAILURE_CLASSES:
            return "environment"
    rule_ids = _rule_ids_from_andon(andon)
    if rule_ids:
        agg = "unknown"
        for rid in rule_ids:
            mapped = _rule_id_cause(rid)
            if mapped is not None:
                agg = _stronger_cause(agg, mapped)
        if agg != "unknown":
            return agg
    return "unknown"


def _signature_rule_ids(data: Andon | Mapping[str, Any]) -> str:
    if isinstance(data, Andon):
        return ",".join(_rule_ids_from_andon(data))
    facts = _normalize_facts(data.get("facts"))
    rule_ids = facts.get("rule_ids")
    if isinstance(rule_ids, list):
        return ",".join(sorted({str(r) for r in rule_ids if r}))
    rule_id = data.get("rule_id")
    if rule_id:
        return str(rule_id)
    return ""


def diagnosis_signature(andon: Andon | Mapping[str, Any]) -> str:
    """Deterministic 12-char hex signature for bundling diagnose candidates."""
    if isinstance(andon, Andon):
        cause = classify_cause(andon)
        facts = andon.facts or {}
    else:
        cause = str(andon.get("cause") or "unknown")
        facts = _normalize_facts(andon.get("facts"))
    failed = facts.get("failed_step")
    failed_s = failed if isinstance(failed, str) else ""
    failure_class = facts.get("failure_class")
    fc_s = failure_class if isinstance(failure_class, str) else ""
    rule_part = _signature_rule_ids(andon)
    raw = f"{cause}|{failed_s}|{rule_part}|{fc_s}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def failed_step(andon: Andon) -> str | None:
    facts = andon.facts or {}
    failed = facts.get("failed_step")
    if isinstance(failed, str) and failed:
        return failed
    if not facts and andon.step not in {"observe", "repair"}:
        return andon.step if andon.step else None
    return None


def _parse_action_string(action: str, andon: Andon) -> dict[str, Any]:
    entry: dict[str, Any] = {"action": action, "source": "options"}
    if action.startswith("resume_from_"):
        from_step = action[len("resume_from_") :]
        entry.update(op="resume", from_step=from_step, force=True)
    elif action == "resume":
        entry.update(op="resume", from_step=andon.step, force=False)
    elif action.startswith("widen:"):
        paths = [p.strip() for p in action[len("widen:") :].split(",") if p.strip()]
        entry.update(op="widen", paths=paths)
    elif action == "reject":
        entry.update(op="record")
    else:
        entry.update(op="record")
    return entry


def answer_actions(andon: Andon, *, prior_reruns: int = 0) -> list[dict[str, Any]]:
    from issuesmith.config import get_config

    auto_max = get_config().andon.auto_rerun_max
    step_s = failed_step(andon)
    cause = classify_cause(andon)
    actions: list[dict[str, Any]] = []
    if step_s:
        resume_default = f"resume_from_{step_s}"
        if resume_default not in andon.options:
            actions.append({**_parse_action_string(resume_default, andon), "source": "default"})
    for opt in andon.options:
        actions.append(_parse_action_string(opt, andon))
    if not andon.options:
        actions.append({**_parse_action_string("reject", andon), "source": "default"})

    for entry in actions:
        op = entry.get("op")
        from_step = entry.get("from_step")
        may = (
            op == "resume"
            and from_step == step_s
            and cause in ("transient", "contention")
            and prior_reruns < auto_max
        )
        entry["agent_may_answer"] = bool(may) if step_s else False
    return actions


def fix_kinds_for(andon: Andon, actions: list[dict[str, Any]], cause: str | None = None) -> list[str]:
    if any(a.get("agent_may_answer") for a in actions):
        return []
    cause = cause if cause is not None else classify_cause(andon)
    mapping: dict[str, list[str]] = {
        "issue_design": ["edit_issue"],
        "environment": ["clean_env"],
        "line_defect": ["fix_line"],
        "review_failed": ["edit_issue", "review_override"],
        "unknown": ["diagnose"],
        "transient": ["diagnose"],
        "contention": ["diagnose"],
    }
    return list(mapping.get(cause, ["diagnose"]))


@dataclass
class Andon:
    id: str
    kind: str  # "decision" | "blocked" | "broken"
    issue: int
    step: str
    summary: str
    evidence: str = ""
    options: list[str] = field(default_factory=list)
    default: str = ""
    mention: str = ""
    rule_id: str = ""
    namespace: str = ""
    facts: dict[str, Any] = field(default_factory=dict)


@dataclass
class AutoAnswerPlan:
    answers: list[dict[str, Any]] = field(default_factory=list)
    escalations: list[dict[str, Any]] = field(default_factory=list)


@runtime_checkable
class AndonSink(Protocol):
    def emit(self, andon: Andon) -> None: ...


# ---------------------------------------------------------------------------
# Serialize / parse
# ---------------------------------------------------------------------------

_FENCE_OPEN = "```andon"
_FENCE_CLOSE = "```"
_FENCE_CLOSE_LINE = re.compile(r"^```[ \t]*$", re.MULTILINE)


def _find_fence_close(text: str, inner_start: int) -> int:
    match = _FENCE_CLOSE_LINE.search(text, inner_start)
    if match is None:
        return -1
    return match.start()


def andon_namespace(andon: Andon) -> str:
    """Effective label namespace for an andon (empty means configured issuesmith namespace)."""
    return andon.namespace or _ns()


def to_comment(andon: Andon, *, prior_reruns: int = 0) -> str:
    data = asdict(andon)
    if not data.get("namespace"):
        data.pop("namespace", None)
    if not data.get("facts"):
        data.pop("facts", None)
    if data.get("facts"):
        cause = classify_cause(andon)
        actions = answer_actions(andon, prior_reruns=prior_reruns)
        data["cause"] = cause
        data["fix_kinds"] = fix_kinds_for(andon, actions, cause=cause)
        data["prior_reruns"] = prior_reruns
        data["actions"] = actions
    body = yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"{_FENCE_OPEN}\n{body}{_FENCE_CLOSE}\n"


def from_comment(text: str) -> Andon | None:
    """Parse first andon block from a comment body; return None if absent."""
    start = text.find(_FENCE_OPEN)
    if start == -1:
        return None
    inner_start = start + len(_FENCE_OPEN)
    # skip newline after opening fence
    if inner_start < len(text) and text[inner_start] == "\n":
        inner_start += 1
    end = _find_fence_close(text, inner_start)
    if end == -1:
        return None
    raw = text[inner_start:end]
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return Andon(
            id=str(data["id"]),
            kind=str(data["kind"]),
            issue=int(data["issue"]),
            step=str(data["step"]),
            summary=str(data["summary"]),
            evidence=str(data.get("evidence") or ""),
            options=list(data.get("options") or []),
            default=str(data.get("default") or ""),
            mention=str(data.get("mention") or ""),
            rule_id=str(data.get("rule_id") or ""),
            namespace=str(data.get("namespace") or ""),
            facts=_normalize_facts(data.get("facts")),
        )
    except (KeyError, TypeError, ValueError):
        return None


_NOTE_MARKER = "<!-- andon-note -->"
_NOTE_FENCE_OPEN = "```andon-note"
_ANSWER_MARKER = "<!-- andon-answer -->"
# The id marker keeps answers parseable whatever the language pack's wording is;
# the second alternative matches answers posted before the id marker existed.
_ANSWER_RE = re.compile(
    r"<!-- andon-answer -->\s*\n"
    r"(?:<!-- andon-answer-id: (\S+) -->|andon `([^`]+)` answered:)"
)
_ANSWER_ACTION_RE = re.compile(r"<!-- andon-answer-action: (.+?) -->")
_ANSWERED_BY_TAG_RE = re.compile(r"<!-- andon-answered-by: (.+?) -->")
_ANSWERED_BY_RE = re.compile(r"^(?:human|agent|rule:.+|observe)$")


def _answer_comment(andon_id: str, action: str, answered_by: str) -> str:
    from issuesmith.config import get_config

    text = get_config().language.message("andon.answered", andon_id=andon_id, action=action)
    return (
        f"{_ANSWER_MARKER}\n"
        f"<!-- andon-answer-id: {andon_id} -->\n"
        f"<!-- andon-answer-action: {action} -->\n"
        f"<!-- andon-answered-by: {answered_by} -->\n"
        f"{text}\n"
    )


def _parse_answer_comment(body: str) -> tuple[str, str | None] | None:
    m = _ANSWER_RE.search(body)
    if m is None:
        return None
    andon_id = m.group(1) or m.group(2)
    if not andon_id:
        return None
    action_m = _ANSWER_ACTION_RE.search(body)
    action = action_m.group(1).strip() if action_m else None
    return andon_id, action


def _prior_reruns_before(
    comments: list[dict],
    *,
    failed_step: str,
    stop_before_index: int | None = None,
) -> int:
    target_action = f"resume_from_{failed_step}"
    count = 0
    for index, comment in enumerate(comments):
        if stop_before_index is not None and index >= stop_before_index:
            break
        body = str(comment.get("body") or "")
        ans = _parse_answer_comment(body)
        if ans is not None:
            _, action = ans
            if action == target_action:
                count += 1
    return count


def _note_comment(andon_id: str, key: str, value: str) -> str:
    data = {"id": andon_id, "key": key, "value": value}
    body = yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)
    return f"{_NOTE_MARKER}\n{_NOTE_FENCE_OPEN}\n{body}{_FENCE_CLOSE}\n"


def _parse_note(text: str) -> tuple[str, str, str] | None:
    """Return (id, key, value) from a note comment body; None if it is not one."""
    if _NOTE_MARKER not in text:
        return None
    start = text.find(_NOTE_FENCE_OPEN + "\n")
    if start == -1:
        return None
    inner_start = start + len(_NOTE_FENCE_OPEN) + 1
    end = _find_fence_close(text, inner_start)
    if end == -1:
        return None
    try:
        # BaseLoader keeps every scalar a string (timestamps stay verbatim).
        data = yaml.load(text[inner_start:end], Loader=yaml.BaseLoader)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    andon_id, key, value = data.get("id"), data.get("key"), data.get("value")
    if not isinstance(andon_id, str) or not isinstance(key, str) or not key:
        return None
    return andon_id, key, value if isinstance(value, str) else ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ns() -> str:
    from issuesmith.config import get_config
    return get_config().label_namespace


def _andon_label_kinds() -> list[str]:
    return list(ANDON_KINDS)


def project_issue(
    client: Any, issue_number: int, change: Callable[[IssueState], IssueState]
) -> tuple[list[str], list[str]]:
    """Apply a state change to an Issue's labels through ``ops/labels.project_issue``."""
    from issuesmith.ops import labels as labels_mod

    return labels_mod.project_issue(client, issue_number, change)


def _with_andon(kind: str) -> Callable[[IssueState], IssueState]:
    return lambda st: replace(st, andon_kinds=st.andon_kinds | {kind})


def _without_andon(kind: str) -> Callable[[IssueState], IssueState]:
    return lambda st: replace(st, andon_kinds=st.andon_kinds - {kind})


def _iter_open_andon_issues(client: Any) -> Iterator[dict]:
    """Yield open Issues that carry at least one andon-* attention label."""
    ns = _ns()
    seen: set[int] = set()
    for kind in _andon_label_kinds():
        label = f"{ns}:andon-{kind}"
        for issue in client.list_issues(label=label, state="open"):
            num = issue["number"]
            if num not in seen:
                seen.add(num)
                yield issue


@dataclass
class _OpenEntry:
    andon: Andon
    raised_at: str
    comment_index: int = -1
    notes: dict[str, str] = field(default_factory=dict)


def _fold_open_entries(comments: list[dict]) -> list[_OpenEntry]:
    entries: list[_OpenEntry] = []
    for comment_index, comment in enumerate(comments):
        body = str(comment.get("body") or "")
        parsed = from_comment(body)
        if parsed is not None:
            entries.append(
                _OpenEntry(
                    parsed,
                    str(comment.get("created_at") or ""),
                    comment_index=comment_index,
                )
            )
            continue
        m = _ANSWER_RE.search(body)
        if m is not None:
            answered_id = m.group(1) or m.group(2)
            entries = [e for e in entries if e.andon.id != answered_id]
            continue
        note_data = _parse_note(body)
        if note_data is not None:
            note_id, key, value = note_data
            for e in entries:
                if e.andon.id == note_id:
                    e.notes[key] = value
    return entries


def _iter_open_entries(client: Any) -> Iterator[_OpenEntry]:
    """Yield unanswered andons of open andon Issues, fetching each Issue's comments once."""
    for issue in _iter_open_andon_issues(client):
        comments = list(client.get_issue_comments(issue["number"]))
        yield from _fold_open_entries(comments)


def _enrich_andon_record(
    andon: Andon,
    comments: list[dict],
    *,
    comment_index: int,
) -> dict[str, Any]:
    step_s = failed_step(andon)
    prior = 0
    if step_s:
        prior = _prior_reruns_before(
            comments,
            failed_step=step_s,
            stop_before_index=comment_index,
        )
    cause = classify_cause(andon)
    actions = answer_actions(andon, prior_reruns=prior)
    return {
        **asdict(andon),
        "cause": cause,
        "fix_kinds": fix_kinds_for(andon, actions, cause=cause),
        "prior_reruns": prior,
        "actions": actions,
    }


def _validate_answered_by(value: str) -> str:
    if not _ANSWERED_BY_RE.fullmatch(value):
        raise ValueError(f"invalid answered_by: {value!r}")
    return value


def _write_metrics(
    path: Path,
    event: str,
    andon: Andon,
    extra: dict[str, str] | None = None,
) -> None:
    from issuesmith.metrics_events import append_event

    row: dict[str, str | int] = {
        "event": event,
        "andon_id": andon.id,
        "issue": andon.issue,
        "step": andon.step,
        "kind": andon.kind,
    }
    if extra:
        row.update(extra)
    append_event(path, row)


def _call_resume_hook(client: Any, andon_id: str, action: str) -> None:
    """Handle post-answer hooks for 'resume' and 'widen:<files>' actions.

    - resume: call resume() directly
    - widen:<files>: update allow_paths in issue body and call resume()
    - other actions: no-op
    """
    try:
        parts = andon_id.split(":")
        # format: <workflow>:<issue>:<step>:<gen>
        if len(parts) < 3:
            return
        issue_num = int(parts[1])
        step = parts[2]
    except Exception:
        return

    if action == "resume":
        try:
            resume(issue_num, from_step=step)
        except Exception:
            pass
        return

    if action.startswith("resume_from_"):
        from_step = action[len("resume_from_") :]
        if from_step:
            try:
                resume(issue_num, from_step=from_step, force=True)
            except Exception:
                pass
        return

    if action.startswith("widen:"):
        files_str = action[len("widen:"):]
        new_files = [f.strip() for f in files_str.split(",") if f.strip()]
        _handle_widen_action(client, issue_num, step, new_files)
        return


def _handle_widen_action(
    client: Any,
    issue_num: int,
    step: str,
    new_files: list[str],
) -> None:
    """Update allow_paths in issue body with new_files and call resume()."""
    try:
        data = client.issue_get(issue_num, fields=["body"])
        body = str(data.get("body") or "") if isinstance(data, dict) else ""
    except Exception:
        return

    if not body:
        return

    from issuesmith.body_editor import replace_allow_paths
    from issuesmith.config import get_config

    # Parse existing allow_paths
    existing: list[str] = []
    try:
        import re as _re

        import yaml as _yaml
        m = _re.search(r"^```yaml\n(.*?)\n```", body, _re.DOTALL | _re.MULTILINE)
        if m:
            data_yaml = _yaml.safe_load(m.group(1))
            if isinstance(data_yaml, dict) and "allow_paths" in data_yaml:
                existing = list(data_yaml["allow_paths"]) if isinstance(data_yaml["allow_paths"], list) else []
    except Exception:
        pass

    # Merge without duplicates
    merged = list(existing)
    for f in new_files:
        if f not in merged:
            merged.append(f)

    new_body = replace_allow_paths(body, merged)
    if new_body is None:
        # No yaml block — post comment only, do not resume
        try:
            client.issue_comment(
                issue_num,
                "<!-- andon-widen-failed -->\n"
                + get_config().language.message("andon.widen_failed", files=new_files),
            )
        except Exception:
            pass
        return

    # Update issue body
    try:
        client.issue_update(issue_num, body=new_body)
    except Exception:
        return

    # Call resume
    try:
        resume(issue_num, from_step=step)
    except Exception:
        pass


def _default_metrics_path() -> Path:
    from issuesmith.config import get_config
    return get_config().paths.metrics


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _find_andon_in_namespace(client: Any, andon_id: str, namespace: str) -> Andon | None:
    for kind in _andon_label_kinds():
        label = f"{namespace}:andon-{kind}"
        for issue in client.list_issues(label=label, state="open"):
            for comment in client.get_issue_comments(issue["number"]):
                parsed = from_comment(str(comment.get("body") or ""))
                if parsed is not None and parsed.id == andon_id:
                    return parsed
    return None


def raise_andon(
    client: Any,
    andon: Andon,
    *,
    sinks: list[AndonSink] | None = None,
    metrics_path: Path | None = None,
) -> None:
    """Post andon as Issue comment, project the attention label, emit to sinks, record metrics.

    A running phase owning ``andon.step`` stops running: its label is dropped.
    """
    from issuesmith.config import get_config
    from issuesmith.projection import phase_for_step

    fetch = getattr(client, "get_issue_comments", None)
    comments = list(fetch(andon.issue)) if callable(fetch) else []
    step_s = failed_step(andon)
    prior = 0
    if step_s and comments:
        prior = _prior_reruns_before(comments, failed_step=step_s)
    client.issue_comment(andon.issue, to_comment(andon, prior_reruns=prior))
    _release_stopped_issue(andon)
    ns = andon_namespace(andon)
    if ns != _ns():
        client.issue_update(andon.issue, labels_add=[f"{ns}:andon-{andon.kind}"])
        for sink in sinks or []:
            sink.emit(andon)
        path = metrics_path if metrics_path is not None else _default_metrics_path()
        _write_metrics(path, "andon_raised", andon)
        return

    phase = phase_for_step(andon.step, get_config())
    running = "running"

    def change(st: IssueState) -> IssueState:
        st = _with_andon(andon.kind)(st)
        if phase is not None and st.phases.get(phase) == running:
            st = replace(st, phases={k: v for k, v in st.phases.items() if k != phase})
        return st

    project_issue(client, andon.issue, change)

    for sink in sinks or []:
        sink.emit(andon)

    path = metrics_path if metrics_path is not None else _default_metrics_path()
    _write_metrics(path, "andon_raised", andon)


def _release_stopped_issue(andon: Andon) -> None:
    """Best-effort: remove queue tracking for the stopped issue."""
    try:
        QueueStore().drop_stopped_issue(andon.issue, f"stopped: andon {andon.id}")
    except Exception:
        logger.warning(
            "failed to drop stopped issue #%s (%s)", andon.issue, andon.id, exc_info=True
        )


def list_open(client: Any) -> list[Andon]:
    """Return all unanswered Andons from open Issues' comments."""
    return [e.andon for e in _iter_open_entries(client)]


def list_open_records(client: Any) -> list[dict[str, Any]]:
    """Return list_open() as dicts with enrichment fields and comment metadata."""
    records: list[dict[str, Any]] = []
    for issue in _iter_open_andon_issues(client):
        comments = list(client.get_issue_comments(issue["number"]))
        for e in _fold_open_entries(comments):
            row = _enrich_andon_record(
                e.andon,
                comments,
                comment_index=e.comment_index,
            )
            row["raised_at"] = e.raised_at
            row["notes"] = dict(e.notes)
            records.append(row)
    return records


def andon_record(client: Any, andon_id: str) -> dict[str, Any] | None:
    """Return enriched JSON record for an open andon id."""
    for issue in _iter_open_andon_issues(client):
        comments = list(client.get_issue_comments(issue["number"]))
        for entry in _fold_open_entries(comments):
            if entry.andon.id == andon_id:
                return {
                    **_enrich_andon_record(
                        entry.andon,
                        comments,
                        comment_index=entry.comment_index,
                    ),
                    "raised_at": entry.raised_at,
                    "notes": dict(entry.notes),
                }
    return None


def note(client: Any, andon_id: str, key: str, value: str) -> None:
    """Post a note comment (``key=value``) on the Issue of the open andon ``andon_id``.

    Labels, metrics and resume are left untouched. Raises ValueError for an empty key
    and KeyError when no unanswered andon has that id.
    """
    if not key:
        raise ValueError("note key must not be empty")
    target = next((e.andon for e in _iter_open_entries(client) if e.andon.id == andon_id), None)
    if target is None:
        raise KeyError(f"Andon not found: {andon_id}")
    client.issue_comment(target.issue, _note_comment(andon_id, key, value))


def answer(
    client: Any,
    andon_id: str,
    action: str,
    *,
    metrics_path: Path | None = None,
    answered_by: str = "human",
) -> None:
    """Post answer comment, drop the andon kind from the projected labels, call resume hook."""
    _validate_answered_by(answered_by)

    # Find the andon in open issues
    target: Andon | None = None
    for issue in _iter_open_andon_issues(client):
        for comment in client.get_issue_comments(issue["number"]):
            parsed = from_comment(comment.get("body", ""))
            if parsed is not None and parsed.id == andon_id:
                target = parsed
                break
        if target is not None:
            break

    if target is None:
        raise KeyError(f"Andon not found: {andon_id}")

    reply = _answer_comment(andon_id, action, answered_by)
    client.issue_comment(target.issue, reply)
    project_issue(client, target.issue, _without_andon(target.kind))

    path = metrics_path if metrics_path is not None else _default_metrics_path()
    _write_metrics(
        path,
        "andon_answered",
        target,
        {"action": action, "answered_by": answered_by},
    )
    _call_resume_hook(client, andon_id, action)


def answer_if_open(
    client: Any,
    andon_id: str,
    action: str,
    *,
    metrics_path: Path | None = None,
    namespace: str | None = None,
) -> None:
    """Post answer comment and remove label if the andon is open; silently skip if not found.

    Unlike answer(), does not call _call_resume_hook and does not raise KeyError
    when the andon is not found (e.g. already closed). Skips entirely when client is None.
    """
    if client is None:
        return

    cfg_ns = _ns()
    external_namespace = (
        namespace if namespace is not None and namespace != "" and namespace != cfg_ns else None
    )

    target: Andon | None = None
    if external_namespace is not None:
        target = _find_andon_in_namespace(client, andon_id, external_namespace)
    else:
        for issue in _iter_open_andon_issues(client):
            for comment in client.get_issue_comments(issue["number"]):
                parsed = from_comment(comment.get("body", ""))
                if parsed is not None and parsed.id == andon_id:
                    target = parsed
                    break
            if target is not None:
                break

    if target is None:
        logger.debug("answer_if_open: andon not found (already closed?): %s", andon_id)
        return

    reply = _answer_comment(andon_id, action, "observe")
    client.issue_comment(target.issue, reply)
    if external_namespace is not None:
        client.issue_update(
            target.issue,
            labels_remove=[f"{external_namespace}:andon-{target.kind}"],
        )
    else:
        project_issue(client, target.issue, _without_andon(target.kind))

    path = metrics_path if metrics_path is not None else _default_metrics_path()
    _write_metrics(
        path,
        "andon_answered",
        target,
        {"action": action, "answered_by": "observe"},
    )

def _count_auto_answer_notes(client: Any, issue: int, rule_name: str) -> int:
    count = 0
    for comment in client.get_issue_comments(issue):
        parsed = _parse_note(str(comment.get("body") or ""))
        if parsed is None:
            continue
        _, key, value = parsed
        if key == "auto_answer" and value == rule_name:
            count += 1
    return count


def _andon_matches_rule(andon: Andon, rule: Any) -> bool:
    match = rule.match
    if not match:
        return False
    for key, expected in match.items():
        if key == "step":
            if andon.step != str(expected):
                return False
        elif key == "kind":
            if andon.kind != str(expected):
                return False
        elif key == "rule_id":
            prefix = str(expected)
            rid = andon.rule_id
            if rid != prefix and not rid.startswith(prefix + "."):
                return False
    return True


def plan_auto_answers(
    client: Any,
    rules: tuple[Any, ...],
    *,
    active_issues: Collection[int] = (),
) -> AutoAnswerPlan:
    """Plan automatic andon answers from declarative rules."""
    active = set(active_issues)
    plan = AutoAnswerPlan()
    for andon in list_open(client):
        if andon.issue in active:
            continue
        matched_rule = None
        for rule in rules:
            if _andon_matches_rule(andon, rule):
                matched_rule = rule
                break
        if matched_rule is None:
            if andon.kind == "decision":
                plan.escalations.append(
                    {
                        "kind": "decision",
                        "issue": andon.issue,
                        "andon_id": andon.id,
                        "summary": andon.summary,
                    }
                )
            continue
        used = _count_auto_answer_notes(client, andon.issue, matched_rule.name)
        if used >= matched_rule.max_per_issue:
            plan.escalations.append(
                {
                    "kind": "auto_answer_exhausted",
                    "issue": andon.issue,
                    "andon_id": andon.id,
                    "rule": matched_rule.name,
                    "summary": andon.summary,
                }
            )
            continue
        plan.answers.append(
            {
                "andon_id": andon.id,
                "issue": andon.issue,
                "rule": matched_rule.name,
                "action": matched_rule.action,
            }
        )
    return plan


def apply_auto_answers(client: Any, plan: AutoAnswerPlan) -> AutoAnswerPlan:
    """Apply a plan: note first, then answer (so counts advance even if answer fails)."""
    for entry in plan.answers:
        andon_id = str(entry["andon_id"])
        action = str(entry["action"])
        rule_name = str(entry["rule"])
        issue = int(entry["issue"])
        client.issue_comment(issue, _note_comment(andon_id, "auto_answer", rule_name))
        answer(client, andon_id, action, answered_by=f"rule:{rule_name}")
    return plan


__all__ = [
    "Andon",
    "AndonSink",
    "AutoAnswerPlan",
    "answer_actions",
    "classify_cause",
    "diagnosis_signature",
    "raise_andon",
    "list_open",
    "list_open_records",
    "andon_record",
    "answer",
    "from_comment",
    "to_comment",
    "plan_auto_answers",
    "apply_auto_answers",
]
