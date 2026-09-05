"""issuesmith recovery — plan recover vs redispatch and execute operator commands (#2865)."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from ghdag.github_client import GitHubClient
from ghdag.io import exec_jsonl

from issuesmith.config import get_config
from issuesmith.queue import (
    apply_redispatch_labels,
    handler_for_failed_step,
    infer_redispatch_phase,
    phase_preconditions,
    redispatch_label_plan,
)
from issuesmith.queue_store import QueueStore

WORKFLOW_NAME = "issuesmith"

_STEP_FROM_DISPATCH_RE = re.compile(r"issuesmith dispatch ([\w-]+)")
_ORDER_PATH_RE = re.compile(r"(?:bash -o pipefail )?(\S+jobs/\S+\.md)")
_WORKTREE_RE = re.compile(r'worktree_path=([^\s"]+)')
_TARGET_WORKTREE_RE = re.compile(r'target_worktree_path=([^\s"]+)')


@dataclass(frozen=True)
class Plan:
    action: Literal["recover", "redispatch"]
    reason: str
    command: str
    required_labels: frozenset[str]
    blocked_by: str | None


def _cfg():
    return get_config()


def _repo_root() -> Path:
    return _cfg().root


def _exec_path() -> Path:
    return _cfg().paths.exec_jsonl


def _done_dir() -> Path:
    return _cfg().paths.done_dir


def _generation_keys_available() -> bool:
    if importlib.util.find_spec("ghdag.dag.recover") is not None:
        return True
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "ghdag", "dag", "recover", "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0
    except OSError:
        return False


def _github_client() -> GitHubClient:
    return GitHubClient(repo=_cfg().repo)


def _idempotency_key(handler: str, issue: int, generation: int | None = None) -> str:
    base = f"{WORKFLOW_NAME}:{handler}:{issue}"
    if generation is None or generation == 0:
        return base
    return f"{base}:{generation}"


def _idempotency_consumed(handler: str, issue: int) -> bool:
    key = _idempotency_key(handler, issue)
    if not exec_jsonl.check_idempotency(_exec_path(), key):
        return True
    if _generation_keys_available():
        for gen in range(1, 16):
            if not exec_jsonl.check_idempotency(_exec_path(), _idempotency_key(handler, issue, gen)):
                return True
    return False


def _handler_records(handler: str, issue: int) -> list[dict[str, Any]]:
    path = _exec_path()
    if not path.exists():
        return []
    prefix = f"{WORKFLOW_NAME}:{handler}:{issue}"
    records: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        key = str(rec.get("idempotency_key", ""))
        if key == prefix or key.startswith(f"{prefix}:"):
            records.append(rec)
    return records


def _step_from_record(rec: dict[str, Any]) -> str | None:
    command = str(rec.get("command", ""))
    m = _STEP_FROM_DISPATCH_RE.search(command)
    if m:
        step = m.group(1)
        return step.replace("-conditional", "").replace("-role-dispatch", "")
    order_match = _ORDER_PATH_RE.search(command)
    if order_match:
        order_path = Path(order_match.group(1))
        if not order_path.is_absolute():
            order_path = _repo_root() / order_path
        if order_path.exists():
            text = order_path.read_text(encoding="utf-8")
            m2 = _STEP_FROM_DISPATCH_RE.search(text)
            if m2:
                step = m2.group(1)
                return step.replace("-conditional", "").replace("-role-dispatch", "")
    return None


def _frozen_orders_for_issue(issue: int) -> list[Path]:
    jobs_dir = _repo_root() / "jobs"
    if not jobs_dir.is_dir():
        return []
    marker = f"issue_number={issue}"
    found: list[Path] = []
    for path in sorted(jobs_dir.glob("*-shell-order-*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if marker in text:
            found.append(path)
    return found


def _worktree_path_for_issue(issue: int, records: list[dict[str, Any]]) -> Path | None:
    for rec in records:
        command = str(rec.get("command", ""))
        for pattern in (_WORKTREE_RE, _TARGET_WORKTREE_RE):
            m = pattern.search(command)
            if m:
                raw = m.group(1).strip()
                path = Path(raw)
                if not path.is_absolute():
                    path = _repo_root() / raw
                return path
        order_match = _ORDER_PATH_RE.search(command)
        if order_match:
            order_path = Path(order_match.group(1))
            if not order_path.is_absolute():
                order_path = _repo_root() / order_path
            if order_path.exists():
                text = order_path.read_text(encoding="utf-8")
                for pattern in (_WORKTREE_RE, _TARGET_WORKTREE_RE):
                    m = pattern.search(text)
                    if m:
                        raw = m.group(1).strip()
                        path = Path(raw)
                        if not path.is_absolute():
                            path = _repo_root() / raw
                        return path
    for order in _frozen_orders_for_issue(issue):
        text = order.read_text(encoding="utf-8")
        for pattern in (_WORKTREE_RE, _TARGET_WORKTREE_RE):
            m = pattern.search(text)
            if m:
                raw = m.group(1).strip()
                path = Path(raw)
                if not path.is_absolute():
                    path = _repo_root() / raw
                return path
    return None


def _prerequisites_intact(issue: int, handler: str) -> tuple[bool, str]:
    records = _handler_records(handler, issue)
    frozen = _frozen_orders_for_issue(issue)
    if not frozen and not records:
        return False, "frozen order not found"
    worktree = _worktree_path_for_issue(issue, records)
    if worktree is None:
        return False, "worktree path not found"
    if not worktree.is_dir():
        return False, f"worktree missing: {worktree}"
    for rec in records:
        order_match = _ORDER_PATH_RE.search(str(rec.get("command", "")))
        if order_match:
            order_path = Path(order_match.group(1))
            if not order_path.is_absolute():
                order_path = _repo_root() / order_path
            if order_path.is_file():
                return True, "ok"
    if frozen:
        return True, "ok"
    return False, "frozen order not found"


def _recover_command(issue: int, failed_step: str) -> str:
    return f"python3 -m issuesmith recover {issue} --from {failed_step}"


def _redispatch_command(issue: int, phase: str, reason: str | None = None) -> str:
    cmd = f"python3 -m issuesmith redispatch {issue} --phase {phase}"
    if reason:
        cmd += f' --reason "{reason}"'
    return cmd


def _merge_redispatch_blocked(issue: int, client: GitHubClient) -> str | None:
    issue_data = client.issue_get(issue, fields=["state", "labels"])
    ok, reason = phase_preconditions("merge", issue_data, client, issue)
    if not ok and "PR" in reason:
        return reason
    return None


def plan(
    issue: int,
    failed_step: str,
    labels: set[str],
    *,
    client: GitHubClient | None = None,
) -> Plan:
    handler = handler_for_failed_step(failed_step, labels)
    phase = infer_redispatch_phase(failed_step, labels)
    required, _ = redispatch_label_plan(phase)
    consumed = _idempotency_consumed(handler, issue)
    intact, intact_reason = _prerequisites_intact(issue, handler)

    def _merge_blocked() -> str | None:
        if phase != "merge":
            return None
        gh = client or _github_client()
        return _merge_redispatch_blocked(issue, gh)

    if not consumed:
        if intact:
            command = _recover_command(issue, failed_step)
            return Plan(
                action="recover",
                reason="worktree and frozen order intact; idempotency key not consumed",
                command=command,
                required_labels=frozenset(),
                blocked_by=None,
            )
        blocked = _merge_blocked()
        command = _redispatch_command(issue, phase)
        return Plan(
            action="redispatch",
            reason=f"prerequisites broken: {intact_reason}",
            command=command,
            required_labels=required,
            blocked_by=blocked,
        )

    if _generation_keys_available():
        blocked = _merge_blocked()
        command = _redispatch_command(issue, phase)
        return Plan(
            action="redispatch",
            reason="idempotency key consumed; generation bump required",
            command=command,
            required_labels=required,
            blocked_by=blocked,
        )

    return Plan(
        action="redispatch",
        reason="idempotency key consumed",
        command=_redispatch_command(issue, phase),
        required_labels=required,
        blocked_by="冪等キー消費済み。ghdag の世代付きキーが必要（#2876）",
    )


def plan_to_json(plan_obj: Plan) -> str:
    payload = asdict(plan_obj)
    payload["required_labels"] = sorted(plan_obj.required_labels)
    return json.dumps(payload, ensure_ascii=False)


def list_recover_steps(
    issue: int,
    failed_step: str,
    *,
    from_step: str | None = None,
) -> list[str]:
    labels: set[str] = set()
    handler = handler_for_failed_step(failed_step, labels)
    records = _handler_records(handler, issue)
    done_dir = _done_dir()
    steps: list[str] = []
    for rec in records:
        uuid = rec.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            continue
        if (done_dir / uuid).exists():
            continue
        step = _step_from_record(rec)
        if step:
            steps.append(step)
    if not steps and _frozen_orders_for_issue(issue):
        step = failed_step
        steps = [step]
    if from_step:
        if from_step in steps:
            idx = steps.index(from_step)
            steps = steps[idx:]
        elif steps:
            steps = [from_step] + [s for s in steps if s != from_step]
        else:
            steps = [from_step]
    return steps


def _run_ghdag_recover(issue: int, handler: str, from_step: str | None) -> int:
    cmd = [
        sys.executable,
        "-m",
        "ghdag",
        "dag",
        "recover",
        "--issue",
        str(issue),
        "--handler",
        handler,
    ]
    if from_step:
        cmd.extend(["--from", from_step])
    proc = subprocess.run(cmd, check=False)
    return int(proc.returncode)


def cmd_recover(
    issue: int,
    *,
    from_step: str | None = None,
    dry_run: bool = False,
    as_json: bool = False,
    labels: set[str] | None = None,
) -> int:
    label_set = labels or set()
    failed_step = from_step or "cp2"
    plan_obj = plan(issue, failed_step, label_set)

    if as_json:
        print(plan_to_json(plan_obj))
        if dry_run:
            for step in list_recover_steps(issue, failed_step, from_step=from_step):
                print(step)
        return 0

    if plan_obj.action != "recover":
        print(
            f"recover not applicable: {plan_obj.reason}. try: {plan_obj.command}",
            file=sys.stderr,
        )
        return 1

    steps = list_recover_steps(issue, failed_step, from_step=from_step)
    if dry_run:
        for step in steps:
            print(step)
        return 0

    handler = handler_for_failed_step(failed_step, label_set)
    if _generation_keys_available():
        return _run_ghdag_recover(issue, handler, from_step)

    print(
        "ghdag dag recover is not available (#2876). "
        f"Re-run manually from frozen orders: {steps}",
        file=sys.stderr,
    )
    return 1


def cmd_redispatch(
    issue: int,
    *,
    phase: str,
    reason: str = "",
    dry_run: bool = False,
) -> int:
    labels_data = _github_client().issue_get(issue, fields=["labels"])
    label_names = {
        lab.get("name")
        for lab in (labels_data.get("labels") or [])
        if isinstance(lab, dict) and lab.get("name")
    }
    failed_step = {"draft": "b1", "develop": "cp2", "merge": "m2"}[phase]
    plan_obj = plan(issue, failed_step, label_names)

    if plan_obj.blocked_by:
        print(plan_obj.blocked_by, file=sys.stderr)
        return 1

    if plan_obj.action == "recover" and not dry_run:
        print(
            f"redispatch not needed: {plan_obj.reason}. try: {plan_obj.command}",
            file=sys.stderr,
        )
        return 1

    required, to_remove = redispatch_label_plan(phase)
    if dry_run:
        print(json.dumps({"required": sorted(required), "remove": sorted(to_remove)}, ensure_ascii=False))
        return 0

    client = _github_client()
    changed = apply_redispatch_labels(client, issue, phase, label_names)
    if not changed and plan_obj.action != "redispatch":
        print("no label changes required", file=sys.stderr)

    store = QueueStore()
    result = store.enqueue(
        issue=issue,
        phase=phase,
        source="recovery",
        actor_kind="human",
        priority="high",
        requested_by=[reason or "recovery"],
    )
    print(json.dumps({"request_id": result.request_id, "created": result.created}, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="issuesmith.recovery")
    sub = parser.add_subparsers(dest="command", required=True)

    p_recover = sub.add_parser("recover")
    p_recover.add_argument("issue", type=int)
    p_recover.add_argument("--from", dest="from_step", default=None)
    p_recover.add_argument("--dry-run", action="store_true")
    p_recover.add_argument("--json", action="store_true")

    p_redispatch = sub.add_parser("redispatch")
    p_redispatch.add_argument("issue", type=int)
    p_redispatch.add_argument("--phase", required=True, choices=["draft", "develop", "merge"])
    p_redispatch.add_argument("--reason", default="")
    p_redispatch.add_argument("--dry-run", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "recover":
        return cmd_recover(
            args.issue,
            from_step=args.from_step,
            dry_run=args.dry_run,
            as_json=args.json,
        )
    if args.command == "redispatch":
        return cmd_redispatch(
            args.issue,
            phase=args.phase,
            reason=args.reason,
            dry_run=args.dry_run,
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
