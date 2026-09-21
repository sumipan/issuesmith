"""Andon (行燈) — typed stop-the-line signals posted as Issue comment yaml blocks.

kind is one of: decision / blocked / broken
id format: <workflow>:<issue>:<step>:<gen>
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable

import yaml

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


def _write_metrics(path: Path, event: str, andon_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event": event, "andon_id": andon_id}) + "\n")


def _call_resume_hook(client: Any, andon_id: str, action: str) -> None:
    try:
        client.dispatch_event(
            "andon-answered",
            {"andon_id": andon_id, "action": action},
        )
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
    result: list[Andon] = []
    for issue in _iter_open_andon_issues(client):
        for comment in client.get_issue_comments(issue["number"]):
            parsed = from_comment(comment.get("body", ""))
            if parsed is not None:
                result.append(parsed)
    return result


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
