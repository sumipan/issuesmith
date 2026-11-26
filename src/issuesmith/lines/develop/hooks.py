"""ghdag runner hooks for the reference develop line (failure record on the Issue)."""

from __future__ import annotations

import sys
from collections.abc import Collection
from typing import TYPE_CHECKING

from ghdag.dag import AuditHooks

from issuesmith.config import get_config
from issuesmith.forge import get_forge
from issuesmith.task_failure import COMMENT_MARKER, format_record

if TYPE_CHECKING:
    from ghdag.dag import Task
    from ghdag.metrics import TaskMetrics

_FENCE = "`" * 3
_STDERR_TAIL_CHARS = 1500
_STDERR_TAIL_LINES = 30
_RECOVERY_TIMEOUT = (
    "RECOVERY: the step hit the task time limit; split the work or reuse the "
    "existing worktree branch before re-running"
)
_RECOVERY_INTERACTIVE = (
    "RECOVERY: the engine stopped on a question; fix the non-interactive "
    "constraints in the order before re-running, and do not switch engines"
)


def parse_line_key(key: object, namespaces: Collection[str]) -> tuple[str, int] | None:
    """``<workflow>:<handler>:<issue>[:<generation>]`` -> (handler, issue)."""
    if not isinstance(key, str):
        return None
    parts = key.split(":")
    if len(parts) not in (3, 4):
        return None
    if parts[0] not in namespaces:
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


def _recovery_suffix(failure_class: str | None) -> str:
    if failure_class == "TIMEOUT":
        return f"\n\n{_RECOVERY_TIMEOUT}"
    if failure_class == "INTERACTIVE_PROMPT":
        return f"\n\n{_RECOVERY_INTERACTIVE}"
    return ""


def format_failure_comment(
    *,
    issue: int,
    handler: str,
    step: str,
    task_uuid: str,
    returncode: int,
    failure_class: str | None,
    stderr_text: str,
    engine: str | None,
    stderr_path: str | None,
) -> str:
    fields = [
        f"- step: `{step}`",
        f"- task: `{task_uuid}`",
        f"- engine: `{engine or '-'}`",
        f"- exit code: `{returncode}`",
        f"- failure class: `{failure_class or '-'}`",
    ]
    if stderr_path:
        fields.append(f"- stderr path: `{stderr_path}`")
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
    body += _recovery_suffix(failure_class)
    body += "\n\n" + format_record(
        task_uuid=task_uuid,
        step=step,
        returncode=returncode,
        failure_class=failure_class or "",
        stderr_tail=tail,
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
            config = get_config()
        except Exception as exc:  # noqa: BLE001 — runner must not crash on config errors
            print(f"[develop_hooks] config load failed: {exc}", file=sys.stderr)
            return
        namespaces = {config.label_namespace}
        namespaces.update(wf.name for wf in config.observe.workflows)
        parsed = parse_line_key(task.idempotency_key, namespaces)
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
            engine=metrics.engine,
            stderr_path=metrics.stderr_path,
        )
        try:
            get_forge().issue_comment(issue, body)
        except Exception as exc:  # noqa: BLE001 — runner must not crash on forge errors
            print(f"[develop_hooks] failure comment failed for #{issue}: {exc}", file=sys.stderr)


HOOKS_CLASS = DevelopLineHooks
