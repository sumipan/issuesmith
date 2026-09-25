"""Andon (行燈) — typed stop-the-line signals posted as Issue comment yaml blocks.

kind is one of: decision / blocked / broken
id format: <workflow>:<issue>:<step>:<gen>
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

import yaml

from issuesmith.resume import resume

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

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


@runtime_checkable
class AndonSink(Protocol):
    def emit(self, andon: Andon) -> None: ...


# ---------------------------------------------------------------------------
# Serialize / parse
# ---------------------------------------------------------------------------

_FENCE_OPEN = "```andon"
_FENCE_CLOSE = "```"


def to_comment(andon: Andon) -> str:
    data = asdict(andon)
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
    end = text.find(_FENCE_CLOSE, inner_start)
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
        )
    except (KeyError, TypeError, ValueError):
        return None


_NOTE_MARKER = "<!-- andon-note -->"
_NOTE_FENCE_OPEN = "```andon-note"
_ANSWER_RE = re.compile(r"<!-- andon-answer -->\s*\nandon `([^`]+)` answered:")


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
    end = text.find(_FENCE_CLOSE, inner_start)
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
    return ["decision", "blocked", "broken"]


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
    notes: dict[str, str] = field(default_factory=dict)


def _iter_open_entries(client: Any) -> Iterator[_OpenEntry]:
    """Yield unanswered andons of open andon Issues, fetching each Issue's comments once.

    Comments are folded in posting order: an andon comment opens an entry, an
    ``<!-- andon-answer -->`` comment closes every open entry with that id, and a note
    comment sets ``notes[key]`` on the open entries with that id (last write wins).
    """
    for issue in _iter_open_andon_issues(client):
        entries: list[_OpenEntry] = []
        for comment in client.get_issue_comments(issue["number"]):
            body = str(comment.get("body") or "")
            parsed = from_comment(body)
            if parsed is not None:
                entries.append(_OpenEntry(parsed, str(comment.get("created_at") or "")))
                continue
            m = _ANSWER_RE.search(body)
            if m is not None:
                entries = [e for e in entries if e.andon.id != m.group(1)]
                continue
            note_data = _parse_note(body)
            if note_data is not None:
                note_id, key, value = note_data
                for e in entries:
                    if e.andon.id == note_id:
                        e.notes[key] = value
        yield from entries


def _write_metrics(path: Path, event: str, andon_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": event, "andon_id": andon_id}) + "\n")


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
                f"<!-- andon-widen-failed -->\n"
                f"Cannot widen allow_paths: no yaml metadata block found in Issue body.\n"
                f"Requested files: {new_files}",
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

def raise_andon(
    client: Any,
    andon: Andon,
    *,
    sinks: list[AndonSink] | None = None,
    metrics_path: Path | None = None,
) -> None:
    """Post andon as Issue comment, add attention label, emit to sinks, record metrics."""
    ns = _ns()
    label = f"{ns}:andon-{andon.kind}"

    client.issue_comment(andon.issue, to_comment(andon))
    client.issue_update(andon.issue, labels_add=[label])

    for sink in sinks or []:
        sink.emit(andon)

    path = metrics_path if metrics_path is not None else _default_metrics_path()
    _write_metrics(path, "andon_raised", andon.id)


def list_open(client: Any) -> list[Andon]:
    """Return all unanswered Andons from open Issues' comments."""
    return [e.andon for e in _iter_open_entries(client)]


def list_open_records(client: Any) -> list[dict[str, Any]]:
    """Return list_open() as dicts with ``raised_at`` (andon comment created_at) and ``notes``."""
    return [
        {**asdict(e.andon), "raised_at": e.raised_at, "notes": dict(e.notes)}
        for e in _iter_open_entries(client)
    ]


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
) -> None:
    """Post answer comment, remove attention label, call resume hook."""
    ns = _ns()

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

    label = f"{ns}:andon-{target.kind}"
    reply = f"<!-- andon-answer -->\nandon `{andon_id}` answered: **{action}**\n"
    client.issue_comment(target.issue, reply)
    client.issue_update(target.issue, labels_remove=[label])

    path = metrics_path if metrics_path is not None else _default_metrics_path()
    _write_metrics(path, "andon_answered", andon_id)
    _call_resume_hook(client, andon_id, action)


def answer_if_open(
    client: Any,
    andon_id: str,
    action: str,
    *,
    metrics_path: Path | None = None,
) -> None:
    """Post answer comment and remove label if the andon is open; silently skip if not found.

    Unlike answer(), does not call _call_resume_hook and does not raise KeyError
    when the andon is not found (e.g. already closed). Skips entirely when client is None.
    """
    if client is None:
        return

    ns = _ns()

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
        logger.debug("answer_if_open: andon not found (already closed?): %s", andon_id)
        return

    label = f"{ns}:andon-{target.kind}"
    reply = f"<!-- andon-answer -->\nandon `{andon_id}` answered: **{action}**\n"
    client.issue_comment(target.issue, reply)
    client.issue_update(target.issue, labels_remove=[label])

    path = metrics_path if metrics_path is not None else _default_metrics_path()
    _write_metrics(path, "andon_answered", andon_id)
