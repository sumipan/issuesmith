"""ghdag runner hooks for the reference develop line (failure record on the Issue)."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from ghdag.dag import AuditHooks

from issuesmith.config import get_config
from issuesmith.forge import get_forge

if TYPE_CHECKING:
    from ghdag.core.models.metrics import TaskMetrics
    from ghdag.dag.models import Task

COMMENT_MARKER = "<!-- issuesmith-task-failure -->"
_FENCE = "`" * 3
_STDERR_TAIL_CHARS = 1500
_STDERR_TAIL_LINES = 30


def parse_line_key(key: object, namespace: str) -> tuple[str, int] | None:
    """``<namespace>:<handler>:<issue>[:<generation>]`` -> (handler, issue)."""
    if not isinstance(key, str):
        return None
    parts = key.split(":")
    if len(parts) not in (3, 4):
        return None
    if parts[0] != namespace:
        return None
    if not (parts[2].isascii() and parts[2].isdigit()):
        return None
    return parts[1], int(parts[2])


def _stderr_tail(text: str) -> str:
    stripped = (text or "").strip()
    if not stripped:
        return ""
    tail = "\n".join(stripped.splitlines()[-_STDERR_TAIL_LINES :])
    if len(tail) > _STDERR_TAIL_CHARS:
        tail = "..." + tail[-_STDERR_TAIL_CHARS :]
    return tail


def format_failure_comment(
    *,
    issue: int,
    handler: str,
    step: str,
    task_uuid: str,
    returncode: int,
    failure_class: str | None,
    stderr_text: str,
) -> str:
    fields = [
        f"- step: `{step}`",
        f"- task: `{task_uuid}`",
        f"- exit code: `{returncode}`",
        f"- failure class: `{failure_class or '-'}`",
    ]
    body = f"{COMMENT_MARKER}\n## Task failed ({step})\n\n" + "\n".join(fields)
    tail = _stderr_tail(stderr_text)
    if tail:
        body += (
            f"\n\n{_FENCE}text\n"
            + tail.replace(_FENCE, "'''")
            + f"\n{_FENCE}"
        )
    body += (
        f"\n\n`python3 -m issuesmith resume {issue} --from {step} --handler {handler}`"
    )
    return body


class DevelopLineHooks(AuditHooks):
    """Record develop-line task failures as a single Issue comment."""

    def on_task_failure(
        self,
        uuid: str,
        task: Task,
        returncode: int,
        stderr_text: str,
        metrics: TaskMetrics,
    ) -> None:
        super().on_task_failure(uuid, task, returncode, stderr_text, metrics)
        try:
            namespace = get_config().label_namespace
        except Exception as exc:  # noqa: BLE001 — runner must not crash on config errors
            print(f"[develop_hooks] config load failed: {exc}", file=sys.stderr)
            return
        parsed = parse_line_key(task.idempotency_key, namespace)
        if parsed is None:
            return
        handler, issue = parsed
        step = str((task.annotations or {}).get("step_name") or "-")
        fc = metrics.failure_class
        failure_class = fc.value if fc is not None else None
        body = format_failure_comment(
            issue=issue,
            handler=handler,
            step=step,
            task_uuid=uuid,
            returncode=returncode,
            failure_class=failure_class,
            stderr_text=stderr_text,
        )
        try:
            get_forge().issue_comment(issue, body)
        except Exception as exc:  # noqa: BLE001 — runner must not crash on forge errors
            print(f"[develop_hooks] failure comment failed for #{issue}: {exc}", file=sys.stderr)
