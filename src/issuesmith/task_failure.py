"""Machine-readable task failure records in Issue comments."""
from __future__ import annotations

import re
from typing import Any

import yaml

COMMENT_MARKER = "<!-- issuesmith-task-failure -->"
_FENCE_OPEN = "```issuesmith-task-failure"
_FENCE_CLOSE_LINE = re.compile(r"^```[ \t]*$", re.MULTILINE)
_TEXT_FENCE = re.compile(r"```text\n(.*?)```", re.DOTALL)


def _find_fence_close(text: str, inner_start: int) -> int:
    match = _FENCE_CLOSE_LINE.search(text, inner_start)
    if match is None:
        return -1
    return match.start()


def _iter_new_format_blocks(body: str) -> list[str]:
    blocks: list[str] = []
    start = 0
    while True:
        idx = body.find(_FENCE_OPEN, start)
        if idx < 0:
            break
        inner_start = idx + len(_FENCE_OPEN)
        if inner_start < len(body) and body[inner_start] == "\n":
            inner_start += 1
        close = _find_fence_close(body, inner_start)
        if close < 0:
            break
        blocks.append(body[inner_start:close])
        start = close
    return blocks


def _normalize_record(raw: dict[str, Any]) -> dict[str, object] | None:
    task = raw.get("task")
    if not isinstance(task, str) or not task:
        return None
    step = raw.get("step")
    failure_class = raw.get("failure_class")
    stderr_tail = raw.get("stderr_tail")
    returncode = raw.get("returncode")
    if not isinstance(step, str):
        step = ""
    if not isinstance(failure_class, str):
        failure_class = ""
    if not isinstance(stderr_tail, str):
        stderr_tail = ""
    if not isinstance(returncode, int):
        returncode = None
    return {
        "task": task,
        "step": step,
        "returncode": returncode,
        "failure_class": failure_class,
        "stderr_tail": stderr_tail,
    }


def _load_new_format_block(block: str) -> dict[str, object] | None:
    try:
        loaded = yaml.safe_load(block)
    except yaml.YAMLError:
        return None
    if not isinstance(loaded, dict):
        return None
    return _normalize_record(loaded)


def _backtick_field(line: str, label: str) -> str:
    prefix = f"- {label}:"
    if not line.startswith(prefix):
        return ""
    rest = line[len(prefix) :].strip()
    if rest.startswith("`") and rest.endswith("`"):
        return rest[1:-1]
    return rest


def _parse_legacy_comment(body: str) -> list[dict[str, object]]:
    if COMMENT_MARKER not in body:
        return []
    task = _backtick_field_from_lines(body, "task")
    if not task:
        return []
    step = _backtick_field_from_lines(body, "step")
    failure_class = _backtick_field_from_lines(body, "failure class")
    if failure_class == "-":
        failure_class = ""
    exit_raw = _backtick_field_from_lines(body, "exit code")
    returncode: int | None
    try:
        returncode = int(exit_raw)
    except (TypeError, ValueError):
        returncode = None
    stderr_tail = ""
    match = _TEXT_FENCE.search(body)
    if match:
        stderr_tail = match.group(1).rstrip("\n")
    return [
        {
            "task": task,
            "step": step,
            "returncode": returncode,
            "failure_class": failure_class,
            "stderr_tail": stderr_tail,
        }
    ]


def _backtick_field_from_lines(body: str, label: str) -> str:
    for line in body.splitlines():
        value = _backtick_field(line, label)
        if value:
            return value
    return ""


def parse_records(comment_body: str) -> list[dict[str, object]]:
    """Return failure records from a comment body in document order."""
    blocks = _iter_new_format_blocks(comment_body)
    if blocks:
        records: list[dict[str, object]] = []
        for block in blocks:
            record = _load_new_format_block(block)
            if record is not None:
                records.append(record)
        return records
    return _parse_legacy_comment(comment_body)


def format_record(
    *,
    task_uuid: str,
    step: str,
    returncode: int,
    failure_class: str,
    stderr_tail: str,
) -> str:
    data = {
        "task": task_uuid,
        "step": step,
        "returncode": returncode,
        "failure_class": failure_class,
        "stderr_tail": stderr_tail,
    }
    body = yaml.safe_dump(
        data,
        allow_unicode=True,
        sort_keys=False,
        default_flow_style=False,
    )
    return f"{_FENCE_OPEN}\n{body}{'```'}\n"


def failure_details_from_comments(
    comments: list[dict],
    task_uuid: str,
) -> tuple[str, str]:
    """Return the latest failure_class and stderr_tail for ``task_uuid``."""
    failure_class = ""
    stderr_tail = ""
    for comment in comments:
        for record in parse_records(str(comment.get("body") or "")):
            if record.get("task") != task_uuid:
                continue
            fc = record.get("failure_class")
            st = record.get("stderr_tail")
            if isinstance(fc, str):
                failure_class = fc
            if isinstance(st, str):
                stderr_tail = st
    return failure_class, stderr_tail
