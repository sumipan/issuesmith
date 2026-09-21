#!/usr/bin/env python3
"""issuesmith-label-hygiene.py — stale フェーズラベルの決定論除去（#2556）.

前フェーズのラベルが後フェーズのラベルと共存すると、
`ghdag.workflow.state_machine.get_current_phase` がラベル配列の順序に依存して
前フェーズを現在相と誤判定し、finalizer の正当な遷移を拒否する
（#2546: `draft-done` + `develop-running` 共存で `develop-done` 遷移が拒否された）。

finalizer の遷移前に呼び出し、**明確に stale と判定できるラベルのみ**を除去する。
判定は下表の決定論ルールのみ。`issuesmith:reset` / `issuesmith:sub-*` /
issuesmith 以外のラベルには一切触れない。
`merge-ready` は `migrate-running` からの正規の戻り遷移（MG1 再マージ）で共存し得るため
migrate 系を根拠にしない。

Usage:
    python3 scripts/issuesmith-label-hygiene.py <issue_number> [--dry-run]

Exit codes:
    0 = success / no-op
    1 = issue not found
    2 = bad arguments
    3 = API error
"""

from __future__ import annotations

import json
import sys
from typing import Any


def compute_stale_labels(labels: set[str]) -> list[str]:
    """現存ラベル集合から、除去すべき stale ラベルの一覧を返す（決定論・副作用なし）。

    共存表は project() の排他制約に委譲する (#3484)。
    管理対象ラベルのうち desired に含まれないものが stale。
    """
    from issuesmith.config import get_config
    from issuesmith.ops.labels import (
        _exec_records_from_labels,
        _is_managed_label,
        project,
    )

    ns = get_config().label_namespace
    exec_recs = _exec_records_from_labels(labels, ns)
    desired = project(0, queue_state=None, exec_records=exec_recs, andon_inbox=[])
    managed_current = {lbl for lbl in labels if _is_managed_label(lbl, ns)}
    return sorted(managed_current - desired)


def _make_client() -> Any:
    from ghdag.forge import get_forge

    return get_forge()


def run(issue_number: int, dry_run: bool, client: Any | None = None) -> tuple[int, dict]:
    from ghdag.exceptions import GitHubApiError

    client = client or _make_client()
    try:
        data = client.issue_get(issue_number, fields=["labels"])
    except GitHubApiError as exc:
        if getattr(exc, "status_code", None) == 404:
            return 1, {"error": "issue not found"}
        return 3, {"error": str(exc)}

    labels = {lbl["name"] for lbl in data.get("labels", [])}
    stale = compute_stale_labels(labels)

    if stale and not dry_run:
        try:
            client.issue_update(issue_number, labels_remove=stale)
        except GitHubApiError as exc:
            return 3, {"error": str(exc), "stale": stale}

    return 0, {"removed": [] if dry_run else stale, "stale": stale, "dry_run": dry_run}


def main(argv: list[str]) -> int:
    args = [a for a in argv if a != "--dry-run"]
    dry_run = "--dry-run" in argv
    if len(args) != 1 or not args[0].isdigit():
        print(__doc__, file=sys.stderr)
        return 2
    code, result = run(int(args[0]), dry_run)
    print(json.dumps(result, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
