"""convert-to-milestone — 誤って develop 経路に入った Issue を milestone 経路へ復旧する。"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from ghdag.github_client import GitHubClient

from issuesmith.config import get_config
from issuesmith.queue_store import QueueStore
from issuesmith.recovery import cmd_redispatch

_DEVELOP_LABEL_RE = re.compile(r"^issuesmith:develop-")
_SUB_LABEL_RE = re.compile(r"^issuesmith:sub-")


def _cfg():
    return get_config()


def _repo_root() -> Path:
    return _cfg().root


def _jobs_dir() -> Path:
    return _cfg().paths.done_dir.parent


def _exec_path() -> Path:
    return _cfg().paths.exec_jsonl


def _github_client() -> GitHubClient:
    return GitHubClient(repo=_cfg().repo)


def _queue_store() -> QueueStore:
    return QueueStore()


def _today_yyyymmdd() -> str:
    return datetime.now(ZoneInfo(_cfg().timezone)).strftime("%Y%m%d")


def _issue_from_idempotency_key(key: str) -> int | None:
    parts = str(key).split(":")
    if len(parts) < 3 or parts[0] != "issuesmith":
        return None
    return int(parts[2]) if parts[2].isdigit() else None


def _uuid_issue_map() -> dict[str, int]:
    path = _exec_path()
    if not path.exists():
        return {}
    mapping: dict[str, int] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        uuid = row.get("uuid")
        if not isinstance(uuid, str) or not uuid:
            continue
        issue: int | None = None
        if isinstance(row.get("issue"), int):
            issue = row["issue"]
        else:
            issue = _issue_from_idempotency_key(str(row.get("idempotency_key", "")))
        if issue is not None:
            mapping[uuid] = issue
    return mapping


def _running_uuids_for_issue(issue: int) -> list[str]:
    running_dir = _jobs_dir() / "running"
    if not running_dir.is_dir():
        return []
    uuid_map = _uuid_issue_map()
    found: list[str] = []
    for path in sorted(running_dir.glob("*.json")):
        uuid = path.stem
        matched = False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        if isinstance(payload, dict) and payload.get("issue") == issue:
            matched = True
        elif uuid_map.get(uuid) == issue:
            matched = True
        elif str(issue) in uuid:
            matched = True
        if matched:
            found.append(uuid)
    return found


def _request_dag_cancel(issue: int, *, dry_run: bool) -> list[str]:
    uuids = _running_uuids_for_issue(issue)
    cancel_dir = _jobs_dir() / "cancel"
    for uuid in uuids:
        if dry_run:
            print(f"[dry-run] cancel {uuid}")
            continue
        cancel_dir.mkdir(parents=True, exist_ok=True)
        (cancel_dir / uuid).write_text("", encoding="utf-8")
    return uuids


def _sync_labels(client: GitHubClient, issue: int, *, dry_run: bool) -> None:
    data = client.issue_get(issue, fields=["labels"])
    current = {
        lab.get("name")
        for lab in (data.get("labels") or [])
        if isinstance(lab, dict) and lab.get("name")
    }
    to_remove = sorted(
        lab
        for lab in current
        if isinstance(lab, str)
        and (_DEVELOP_LABEL_RE.match(lab) or _SUB_LABEL_RE.match(lab))
    )
    to_add = sorted(
        lab for lab in ("scope:milestone", "issuesmith:draft-done") if lab not in current
    )
    if dry_run:
        print(f"[dry-run] labels_add={to_add} labels_remove={to_remove}")
        return
    if to_add or to_remove:
        client.issue_update(issue, labels_add=to_add or None, labels_remove=to_remove or None)


def _ensure_milestone(client: GitHubClient, issue: int, *, dry_run: bool) -> str:
    title = f"{issue}-{_today_yyyymmdd()}"
    data = client.issue_get(issue, fields=["milestone"])
    existing = data.get("milestone")
    if isinstance(existing, dict) and existing.get("number") is not None:
        if dry_run:
            print(f"[dry-run] milestone already attached: {existing.get('title') or existing.get('number')}")
        return title

    milestones = client.milestone_list()
    number: int | None = None
    for item in milestones:
        if isinstance(item, dict) and item.get("title") == title:
            number = int(item["number"])
            break

    if number is None:
        if dry_run:
            print(f"[dry-run] create milestone {title}")
            return title
        number = client.milestone_create(title)
    elif dry_run:
        print(f"[dry-run] attach existing milestone {title} (#{number})")
        return title

    client.issue_update(issue, milestone=number)
    return title


def convert_to_milestone(issue: int, *, dry_run: bool = False) -> int:
    """誤 develop 経路の Issue を milestone 経路へ復旧する（各段冪等）。"""
    cancelled = _request_dag_cancel(issue, dry_run=dry_run)
    if not dry_run and cancelled:
        print(f"cancel requested: {', '.join(cancelled)}")

    client = _github_client()
    _sync_labels(client, issue, dry_run=dry_run)
    _ensure_milestone(client, issue, dry_run=dry_run)

    if dry_run:
        print(f"[dry-run] remove_in_flight({issue})")
        print(f"[dry-run] redispatch {issue} --phase draft")
        return 0

    store = _queue_store()
    store.remove_in_flight(issue)
    return int(cmd_redispatch(issue, phase="draft", reason="convert-to-milestone"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="issuesmith convert-to-milestone")
    parser.add_argument("issue", type=int)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return convert_to_milestone(args.issue, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
