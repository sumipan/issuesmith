"""issuesmith queue CLI — enqueue, triage, dispatch, migrate."""

from __future__ import annotations

import argparse
import fnmatch
import json
import logging
import os
import re
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from ghdag.core.exceptions import GitHubApiError
from ghdag.forge import ForgePort, get_forge
from ghdag.quota import QuotaGate

from issuesmith.config import get_config
from issuesmith.dep_extractor import check_dependencies, extract_dependencies
from issuesmith.milestone import advance_milestone_chains, milestone_last_issue_terminal_ok
from issuesmith.queue_store import (
    DEFAULT_NIGHT_STATE_PATH,
    DEFAULT_SEED_PATH,
    DEFAULT_TRIAGE_LOG_PATH,
    QueueSnapshot,
    QueueStore,
    QueueValidationError,
    in_flight_by_engine,
)
from issuesmith.queue_triage import (
    DONE_LABEL,
    READY_LABEL,
    RUNNING_LABEL,
    TERMINAL_WITHOUT_MERGE,
    append_cas_conflict_log,
    append_circuit_open_log,
    apply_deterministic_order_constraints,
    apply_priority_bucket_order,
    comment_marker,
    deterministic_decision,
    deterministic_order,
    has_marker_comment,
    is_circuit_open,
    label_names,
    load_seed_entries,
    parse_frontmatter_fields,
    seed_window,
    triage,
)
from issuesmith.targets import _normalize_allow_paths

logger = logging.getLogger(__name__)

_cfg = get_config()
TZ = ZoneInfo(_cfg.timezone)
REPO = _cfg.repo

REPO_ROOT = _cfg.root
JOBS_DIR = _cfg.paths.exec_jsonl.parent
DONE_DIR = _cfg.paths.done_dir
EXEC_PATH = _cfg.paths.exec_jsonl
QUOTA_STATE_PATH = _cfg.paths.quota_state


def _configured_phases():
    """Return config phases, falling back to defaults for partial test doubles."""
    from issuesmith.config import _DEFAULT_PHASES

    return getattr(get_config(), "phases", _DEFAULT_PHASES)


def _phase_role_map() -> dict[str, str]:
    return {p.name: p.role for p in _configured_phases()}


def __getattr__(name: str) -> Any:
    if name == "PHASE_ROLE":
        return _phase_role_map()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


@dataclass
class DispatchResult:
    dispatched: bool
    issue: int | None = None
    label: str | None = None
    request_id: str | None = None
    reason: str = ""


def _now_jst() -> datetime:
    return datetime.now(TZ)


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(hour=int(hour), minute=int(minute))


def _in_window(now: datetime, start_str: str, end_str: str) -> bool:
    start = _parse_hhmm(start_str)
    end = _parse_hhmm(end_str)
    current = now.timetz().replace(tzinfo=None)
    if start <= end:
        return start <= current <= end
    return current >= start or current <= end


def _iter_issuesmith_exec_uuids() -> list[str]:
    return [uuid for uuid, _issue in _iter_issuesmith_exec_records()]


def _issue_from_idempotency_key(key: str) -> int | None:
    """``issuesmith:<handler>:<issue>[:<generation>]`` から issue 番号を取り出す。

    2026-09-09 まで末尾要素を issue とみなしていたため、世代付きキー
    （``issuesmith:impl:2959:1``、redispatch で ghdag が付与）が issue=1 と誤解釈され、
    in_flight に無い「孤児」として ``_dispatch_pipeline_ready`` を塞いでいた。
    """
    parts = key.split(":")
    if len(parts) < 3 or parts[0] != WORKFLOW_NAME:
        return None
    issue_part = parts[2]
    return int(issue_part) if issue_part.isdigit() else None


def _iter_issuesmith_exec_records() -> list[tuple[str, int | None]]:
    """Return (uuid, issue_number) for every issuesmith-prefixed exec.jsonl row.

    ``idempotency_key`` は ``issuesmith:<phase>:<issue_number>`` 形式（例:
    ``issuesmith:impl:2969``）。末尾が数字でなければ issue_number は None
    （現状 issuesmith プレフィックスの key は常に末尾が issue 番号）。
    """
    if not EXEC_PATH.exists():
        return []
    records: list[tuple[str, int | None]] = []
    for raw in EXEC_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        idempotency_key = str(row.get("idempotency_key", ""))
        uuid = row.get("uuid")
        if idempotency_key.startswith("issuesmith:") and isinstance(uuid, str) and uuid:
            records.append((uuid, _issue_from_idempotency_key(idempotency_key)))
    return records


def _issue_has_incomplete_exec(issue_number: int) -> bool:
    """True when any issuesmith exec UUID for ``issue_number`` lacks a DONE marker.

    Used by design-slot in_flight release so a brief ``draft-done`` window
    (before ``develop-ready`` / watcher impl dispatch) does not drop tracking
    while brushup or impl DAG rows are still pending (#3092).
    """
    for uuid, issue in _iter_issuesmith_exec_records():
        if issue != issue_number:
            continue
        if not (DONE_DIR / uuid).exists():
            return True
    return False


def _latest_done_mtime() -> float | None:
    if not DONE_DIR.exists():
        return None
    mtimes = [p.stat().st_mtime for p in DONE_DIR.iterdir() if p.is_file()]
    if not mtimes:
        return None
    return max(mtimes)


def _issuesmith_latest_done_mtime() -> float | None:
    uuids = _iter_issuesmith_exec_uuids()
    if not uuids:
        return None
    mtimes: list[float] = []
    for uuid in uuids:
        done_path = DONE_DIR / uuid
        if done_path.exists():
            mtimes.append(done_path.stat().st_mtime)
    if not mtimes:
        return None
    return max(mtimes)


def _pipeline_idle_enough(idle_minutes: int, now: datetime) -> bool:
    """Legacy idle helper (no in-flight awareness). Prefer _pipeline_idle_enough_v2."""
    uuids = _iter_issuesmith_exec_uuids()
    for uuid in uuids:
        if not (DONE_DIR / uuid).exists():
            return False
    latest = _latest_done_mtime()
    if latest is None:
        return False
    elapsed = now.timestamp() - latest
    return elapsed >= idle_minutes * 60


def _pipeline_idle_enough_v2(
    snap: QueueSnapshot, idle_minutes: int, now: datetime
) -> bool:
    if snap.in_flight:
        return False
    uuids = _iter_issuesmith_exec_uuids()
    for uuid in uuids:
        if not (DONE_DIR / uuid).exists():
            return False
    latest = _issuesmith_latest_done_mtime()
    if latest is None:
        return True
    elapsed = now.timestamp() - latest
    return elapsed >= idle_minutes * 60


def _dispatch_pipeline_ready(
    snap: QueueSnapshot, idle_minutes: int, now: datetime
) -> bool:
    """Admission idle gate.

    「mid-flight のステップが無いこと」を要求するが、その issue が
    ``snap.in_flight`` で追跡済みなら未完了で当然なので許容する
    （#2867 の per-engine concurrency 導入後、他 issue の並行実行時に
    実行中の issue 自身の後続ステップ未完了が誤って「pipeline not idle」
    と判定され、別 engine の新規ディスパッチまで巻き添えでブロックされて
    いた）。in_flight に無い issue の未完了ステップは、in_flight リークや
    クラッシュ後の孤児タスクを示すため、引き続きブロック対象とする。
    """
    in_flight_issues = {
        entry.get("issue") for entry in snap.in_flight if isinstance(entry, dict)
    }
    for uuid, issue_number in _iter_issuesmith_exec_records():
        if issue_number in in_flight_issues:
            continue
        if not (DONE_DIR / uuid).exists():
            return False
    if snap.in_flight:
        return True
    latest = _issuesmith_latest_done_mtime()
    if latest is None:
        return True
    elapsed = now.timestamp() - latest
    return elapsed >= idle_minutes * 60


ENGINE_STATE_PATH = _cfg.paths.engine_state
_DEFAULT_ROLE_ENGINES = {"design": "claude", "implementation": "claude"}


def _required_engines(engine_state_path: Path | None = None) -> dict[str, str]:
    """role → engine を .pipeline-state/issuesmith-engine.yml から読む。

    読めない場合は既定（design/implementation とも claude）に倒す。
    """
    path = engine_state_path or ENGINE_STATE_PATH
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return dict(_DEFAULT_ROLE_ENGINES)
    result: dict[str, str] = {}
    for role, default in _DEFAULT_ROLE_ENGINES.items():
        value = data.get(role) if isinstance(data, dict) else None
        engine = value.get("engine") if isinstance(value, dict) else None
        result[role] = str(engine) if engine else default
    return result


def _resolve_engine(phase: str, engine_state_path: Path | None = None) -> str:
    role = _phase_role_map()[phase]
    return _required_engines(engine_state_path)[role]


def _serial_concurrency() -> bool:
    concurrency = get_config().concurrency
    if concurrency.per_engine:
        return False
    return concurrency.default <= 1


def _issue_is_terminal(client: ForgePort, issue_number: int) -> bool:
    try:
        issue = client.issue_get(issue_number, fields=["state", "labels"])
    except Exception:
        return False
    labels = label_names(issue)
    state = str(issue.get("state", "")).upper()
    return state == "CLOSED" and (
        "issuesmith:merge-done" in labels or bool(labels & TERMINAL_WITHOUT_MERGE)
    )


def _issue_target_meta(issue: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """Return (target_repo, allow_paths) from issue body YAML."""
    meta = parse_frontmatter_fields(str(issue.get("body") or ""))
    repo = str(meta.get("target_repo") or "").strip()
    paths = _normalize_allow_paths(meta.get("allow_paths", ()))
    return repo, paths


def _allow_paths_conflict(
    candidate_repo: str,
    candidate_paths: tuple[str, ...],
    in_flight: list[dict[str, Any]],
) -> int | None:
    """Return conflicting in_flight issue number, or None if no conflict.

    Legacy entries without ``target_repo`` / ``allow_paths`` are treated as
    conflicts (fail closed) until they leave in_flight naturally.
    """
    for entry in in_flight:
        entry_repo = entry.get("target_repo")
        if not isinstance(entry_repo, str) or not entry_repo.strip():
            issue = entry.get("issue")
            return int(issue) if isinstance(issue, int) else None
        if entry_repo != candidate_repo:
            continue
        raw_paths = entry.get("allow_paths")
        if not raw_paths:
            issue = entry.get("issue")
            return int(issue) if isinstance(issue, int) else None
        entry_paths = _normalize_allow_paths(raw_paths)
        for p1 in candidate_paths:
            for p2 in entry_paths:
                if fnmatch.fnmatch(p1, p2) or fnmatch.fnmatch(p2, p1):
                    issue = entry.get("issue")
                    return int(issue) if isinstance(issue, int) else None
    return None


def _conflict_overlap_path(
    candidate_paths: tuple[str, ...],
    entry: dict[str, Any],
) -> str:
    raw_paths = entry.get("allow_paths")
    if not raw_paths:
        return "(legacy in_flight)"
    entry_paths = _normalize_allow_paths(raw_paths)
    for p1 in candidate_paths:
        for p2 in entry_paths:
            if fnmatch.fnmatch(p1, p2) or fnmatch.fnmatch(p2, p1):
                return p2 if fnmatch.fnmatch(p1, p2) else p1
    return entry_paths[0] if entry_paths else "?"


def _in_flight_should_release(client: ForgePort, entry: dict[str, Any]) -> bool:
    """True when an in_flight entry should be dropped.

    Terminal issues always release. Design-slot entries also release after
    ``draft-done`` once develop has not yet started (absorbs former milestone C0),
    but only when the issue has no incomplete issuesmith exec records (#3092).
    """
    issue_num = entry.get("issue")
    if not isinstance(issue_num, int):
        return False
    try:
        issue = client.issue_get(issue_num, fields=["state", "labels"])
    except Exception:
        return False
    labels = label_names(issue)
    state = str(issue.get("state", "")).upper()
    if state == "CLOSED" and (
        "issuesmith:merge-done" in labels or bool(labels & TERMINAL_WITHOUT_MERGE)
    ):
        return True
    if DONE_LABEL["sub"] in labels and not (
        labels & {READY_LABEL["sub"], RUNNING_LABEL["sub"]}
    ):
        # milestone 親は sub-done で自分の DAG を終え、実装は子 Issue が担う。親は
        # 子が全部マージされるまで CLOSE されないので、ここで解放しないと親と
        # allow_paths が重なる子の develop が競合ゲートで永久に待つ
        # （2026-09-10、#2934 → #2999 / #3000 で実測）。
        return True
    if DONE_LABEL["draft"] not in labels:
        return False
    busy = {
        READY_LABEL["develop"],
        RUNNING_LABEL["develop"],
    }
    if labels & busy:
        return False
    if _issue_has_incomplete_exec(issue_num):
        return False
    role = entry.get("role")
    if role is not None and role != "design":
        return False
    return True


def _find_untracked_running(client: ForgePort, snap: QueueSnapshot) -> list[int]:
    """Return develop-running issue numbers absent from ``snap.in_flight``."""
    try:
        issues = client.list_issues(RUNNING_LABEL["develop"], state="open")
    except Exception:
        return []
    if not isinstance(issues, list):
        return []
    tracked = {
        entry.get("issue") for entry in snap.in_flight if isinstance(entry, dict)
    }
    untracked: list[int] = []
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        num = issue.get("number")
        if isinstance(num, int) and num not in tracked:
            untracked.append(num)
    return sorted(untracked)


def _recover_untracked_in_flight(
    client: ForgePort, store: QueueStore, snap: QueueSnapshot
) -> int:
    """Re-register develop-running Issues missing from in_flight (#3092 AC-2).

    Watcher dispatches impl DAGs without queue participation; if a design slot
    was released in the draft-done gap, those Issues vanish from tracking and
    orphan-gate the whole queue. Recover before ``_dispatch_pipeline_ready``.
    """
    untracked = _find_untracked_running(client, snap)
    recovered = 0
    for issue_num in untracked:
        try:
            issue = client.issue_get(
                issue_num, fields=["state", "labels", "body", "number"]
            )
        except Exception as exc:
            logger.warning(
                "in_flight_recovered skipped issue=#%s: issue_get failed: %s",
                issue_num,
                exc,
            )
            continue
        target_repo, allow_paths = _issue_target_meta(issue)
        engine = _resolve_engine("develop")
        store.add_in_flight(
            issue_num,
            engine,
            role="implementation",
            allow_paths=allow_paths,
            target_repo=target_repo or None,
        )
        logger.info("in_flight_recovered issue=#%s", issue_num)
        recovered += 1
    return recovered


def _halt_resolved(snap: QueueSnapshot, client: ForgePort) -> bool:
    reason = snap.halt_reason or ""
    if "is still OPEN" in reason:
        return len(snap.in_flight) == 0
    if "without terminal label" in reason:
        match = re.search(r"#(\d+)", reason)
        if not match:
            return False
        issue_number = int(match.group(1))
        return _issue_is_terminal(client, issue_number)
    return False


def _required_engines_paused(
    quota_path: Path | None = None,
    engine_state_path: Path | None = None,
    role: str | None = None,
) -> list[str]:
    """投入に必要なロールの engine のうち paused なものを返す。

    ``role`` 指定時はそのロールの engine のみ。未指定時は design / implementation
    両ロールの和集合（後方互換）。フェーズ別判定は ``dispatch_one`` が
    ``role=_phase_role_map()[phase]`` で呼ぶ（#3091）。
    """
    snapshot = QuotaGate(state_path=quota_path or QUOTA_STATE_PATH).snapshot()
    if not snapshot.engines:
        return []
    role_map = _required_engines(engine_state_path)
    if role is not None:
        engine_name = role_map.get(role)
        required = {engine_name} if engine_name else set()
    else:
        required = set(role_map.values())
    return sorted(
        name for name, engine in snapshot.engines.items()
        if name in required and engine.status == "paused"
    )


def _all_engines_paused(quota_path: Path | None = None) -> bool:
    """後方互換ラッパ。必要ロールの engine が 1 つでも paused なら True。"""
    return bool(_required_engines_paused(quota_path))


def _source_in_window(source: str, actor_kind: str, now: datetime, start: str, end: str) -> bool:
    if actor_kind == "human":
        return True
    if source in ("release-watcher", "night-queue-seed", "night-queue"):
        return _in_window(now, start, end)
    # Other automation: allow always unless configured otherwise.
    return True


def _format_in_flight_status(snap: QueueSnapshot) -> str:
    concurrency = get_config().concurrency
    role_map = _required_engines()
    counts = in_flight_by_engine(snap.in_flight, role_engine_map=role_map)
    engines = sorted(set(counts) | set(concurrency.per_engine))
    if not engines:
        engines = sorted(role_map.values())
    parts: list[str] = []
    for engine in engines:
        limit = concurrency.limit(engine)
        parts.append(f"{engine}: {counts.get(engine, 0)}/{limit}")
    return "{" + ", ".join(parts) + "}"


def _closes_issue_marker(issue_number: int) -> re.Pattern[str]:
    # Digit boundary: avoid Closes #12 matching #123.
    # publish.py はもう "Closes" を発行しない（GitHub auto-close の副作用を避けるため。
    # #2852 / #2873 で premature close が実測された）。ここは "Closes" と "Refs" の
    # 両方にマッチさせ、旧世代（Closes）の PR が残っていても・新世代（Refs）の PR でも
    # 「この issue に紐づく PR を発見する」役目を引き続き果たす。
    return re.compile(rf"(?i)\b(?:closes|refs)\s+#{issue_number}(?!\d)")


def _find_open_prs_closing_issue(client: ForgePort, issue_number: int) -> list[dict[str, Any]]:
    """Find open PRs whose title/body contain ``Closes #N``.

    Production ``pr_list(search=...)`` only filters title/head, and
    ``_normalize_prs`` strips ``body``. Mirror loops_host: list open PRs, then
    ``pr_get`` for each candidate to read the body.
    """
    marker = _closes_issue_marker(issue_number)
    try:
        prs = client.pr_list(state="open", limit=100)
    except Exception:
        return []
    if not isinstance(prs, list):
        return []
    matched: list[dict[str, Any]] = []
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        title = str(pr.get("title") or "")
        body = str(pr.get("body") or "")
        if marker.search(title) or marker.search(body):
            matched.append(pr)
            continue
        number = pr.get("number")
        if not isinstance(number, int):
            continue
        try:
            detail = client.pr_get(number)
        except Exception:
            continue
        if not isinstance(detail, dict):
            continue
        d_title = str(detail.get("title") or "")
        d_body = str(detail.get("body") or "")
        if marker.search(d_title) or marker.search(d_body):
            matched.append(detail)
    return matched


def _pr_is_merged(pr: dict[str, Any]) -> bool:
    """True if PR dict looks merged (GraphQL MERGED, REST merged_at, or merged flag)."""
    if pr.get("merged") is True:
        return True
    for key in ("mergedAt", "merged_at"):
        val = pr.get(key)
        if isinstance(val, str) and val.strip():
            return True
    return str(pr.get("state") or "").upper() == "MERGED"


def _find_merged_prs_closing_issue(client: ForgePort, issue_number: int) -> list[dict[str, Any]]:
    """Find merged PRs whose title/body contain ``Closes #N``.

    ``pr_list(state="closed")`` strips ``body`` and often omits merge metadata.
    Use ``pr_get`` for body and ``api_request("pulls/{n}")`` when merge status
    is missing from the normalized list (production GitHubClient via get_forge).
    """
    marker = _closes_issue_marker(issue_number)
    try:
        prs = client.pr_list(state="closed", limit=100)
    except Exception:
        return []
    if not isinstance(prs, list):
        return []
    matched: list[dict[str, Any]] = []
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        number = pr.get("number")
        if not isinstance(number, int):
            continue

        detail: dict[str, Any] = dict(pr)
        if not _pr_is_merged(detail):
            try:
                raw = client.api_request(f"pulls/{number}")
            except Exception:
                raw = None
            if isinstance(raw, dict) and _pr_is_merged(raw):
                detail = {**detail, **raw}
            else:
                continue

        title = str(detail.get("title") or "")
        body = str(detail.get("body") or "")
        if marker.search(title) or marker.search(body):
            matched.append(detail)
            continue
        try:
            fetched = client.pr_get(number)
        except Exception:
            continue
        if not isinstance(fetched, dict):
            continue
        d_title = str(fetched.get("title") or "")
        d_body = str(fetched.get("body") or "")
        if marker.search(d_title) or marker.search(d_body):
            matched.append({**detail, **fetched})
    return matched


def _list_open_issues(client: ForgePort) -> list[dict[str, Any]]:
    """List all open Issues (excluding PRs) via the production forge client contract.

    ``list_issues`` requires a label and cannot scan the whole repo for supersede.
    Use ``api_request("issues?state=open&...")`` which returns raw GitHub JSON.
    """
    raw: Any = None
    try:
        raw = client.api_request("issues?state=open&per_page=100", paginate=True)
    except Exception:
        try:
            raw = client.api_request("issues?state=open&per_page=100")
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        # GitHub /issues includes pull requests; skip them for supersede.
        if item.get("pull_request") is not None:
            continue
        state = str(item.get("state") or "OPEN").upper()
        out.append(
            {
                "number": item.get("number"),
                "title": item.get("title") or "",
                "state": state,
                "body": item.get("body") or "",
                "labels": item.get("labels") or [],
            }
        )
    return out


_BRUSHUP_STEPS = frozenset({"b1", "cp1_gate", "cp1"})
_IMPL_STEPS = frozenset({"p0", "p1", "p2", "p2r", "p3", "cp2", "m1", "m1r", "m2"})


WORKFLOW_NAME = "issuesmith"
_PHASE_HANDLER: dict[str, str] = {
    "draft": "brushup",
    "develop": "impl",
    "merge": "merge",
    "sub": "subissue",
}
_MAX_GENERATION_SCAN = 16


def _handler_key_consumed(handler: str, issue: int) -> bool:
    """このハンドラーの冪等キー（世代付きを含む）が exec.jsonl で消費済みか。

    消費済みなら ready ラベルを付け直しても ghdag watcher は
    「dispatch skipped (already dispatched)」で起動しない（2026-09-09、#2980 の
    CP2 FAIL 復旧で実測）。その場合は世代を上げて起動し直す必要がある。
    """
    if not EXEC_PATH.exists():
        return False
    from ghdag.io import exec_jsonl

    base = f"{WORKFLOW_NAME}:{handler}:{issue}"
    keys = [base] + [f"{base}:{gen}" for gen in range(1, _MAX_GENERATION_SCAN)]
    return any(not exec_jsonl.check_idempotency(EXEC_PATH, key) for key in keys)


def _trigger_ghdag_redispatch(issue: int, handler: str, reason: str) -> int:
    """ghdag の世代を上げてハンドラーを起動する（`ghdag trigger --redispatch` と同じ）。"""
    cmd = [
        sys.executable,
        "-m",
        "ghdag",
        "trigger",
        str(issue),
        "--handler",
        handler,
        "--workflow",
        WORKFLOW_NAME,
        "--exec-md",
        str(EXEC_PATH),
        "--redispatch",
        "--reason",
        reason,
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        print(
            f"[issuesmith-queue] ghdag redispatch failed for #{issue} handler={handler} "
            f"rc={proc.returncode}: {(proc.stderr or proc.stdout).strip()[-500:]}",
            file=sys.stderr,
        )
    return int(proc.returncode)


def handler_for_failed_step(failed_step: str, labels: set[str]) -> str:
    if failed_step in _BRUSHUP_STEPS:
        return "brushup"
    if failed_step in {"m1", "m1r", "m2"} and (
        RUNNING_LABEL["merge"] in labels or READY_LABEL["merge"] in labels
    ):
        return "merge"
    return "impl"


def infer_redispatch_phase(failed_step: str, labels: set[str]) -> str:
    if failed_step in _BRUSHUP_STEPS or DONE_LABEL["draft"] not in labels and (
        READY_LABEL["draft"] in labels or RUNNING_LABEL["draft"] in labels
    ):
        return "draft"
    if RUNNING_LABEL["merge"] in labels or READY_LABEL["merge"] in labels:
        return "merge"
    return "develop"


def redispatch_label_plan(phase: str) -> tuple[frozenset[str], frozenset[str]]:
    """Return (labels that must be present, labels to remove) for redispatch."""
    if phase == "draft":
        return frozenset(), frozenset(
            {READY_LABEL["draft"], RUNNING_LABEL["draft"], DONE_LABEL["draft"]}
        )
    if phase == "develop":
        return frozenset({DONE_LABEL["draft"]}), frozenset(
            {
                READY_LABEL["develop"],
                RUNNING_LABEL["develop"],
                DONE_LABEL["develop"],
            }
        )
    if phase == "sub":
        return frozenset({DONE_LABEL["draft"], "scope:milestone"}), frozenset(
            {
                READY_LABEL["sub"],
                RUNNING_LABEL["sub"],
                DONE_LABEL["sub"],
            }
        )
    if phase == "merge":
        return frozenset(), frozenset(
            {READY_LABEL["merge"], RUNNING_LABEL["merge"], DONE_LABEL["merge"]}
        )
    raise ValueError(f"unknown phase {phase}")


def apply_redispatch_labels(
    client: ForgePort,
    issue_number: int,
    phase: str,
    current_labels: set[str],
) -> bool:
    required, to_remove = redispatch_label_plan(phase)
    labels_add = sorted(lab for lab in required if lab not in current_labels)
    labels_remove = sorted(lab for lab in to_remove if lab in current_labels)
    if not labels_add and not labels_remove:
        return False
    client.issue_update(issue_number, labels_add=labels_add, labels_remove=labels_remove)
    return True


def phase_preconditions(
    phase: str, issue: dict[str, Any], client: ForgePort, issue_number: int
) -> tuple[bool, str]:
    return _phase_preconditions(phase, issue, client, issue_number)


def _phase_preconditions(phase: str, issue: dict[str, Any], client: ForgePort, issue_number: int) -> tuple[bool, str]:
    state = str(issue.get("state", "")).upper()
    if state != "OPEN":
        return False, "issue not OPEN"
    labels = label_names(issue)

    def _deps_ok() -> tuple[bool, str]:
        deps = extract_dependencies(str(issue.get("body") or ""))
        if deps:
            result = check_dependencies(deps, client=client)
            if result.decision == "BLOCK":
                return False, "dependencies not satisfied"
        return True, "ok"

    if phase == "draft":
        for lab in (READY_LABEL["draft"], RUNNING_LABEL["draft"], DONE_LABEL["draft"]):
            if lab in labels:
                return False, f"{lab} present"
        return _deps_ok()
    if phase == "develop":
        if DONE_LABEL["draft"] not in labels:
            return False, "draft-done required"
        for lab in (READY_LABEL["develop"], RUNNING_LABEL["develop"], DONE_LABEL["develop"]):
            if lab in labels:
                return False, f"{lab} present"
        return _deps_ok()
    if phase == "sub":
        if DONE_LABEL["draft"] not in labels:
            return False, "draft-done required"
        for lab in (READY_LABEL["sub"], RUNNING_LABEL["sub"], DONE_LABEL["sub"]):
            if lab in labels:
                return False, f"{lab} present"
        return _deps_ok()
    if phase == "merge":
        for lab in (READY_LABEL["merge"], RUNNING_LABEL["merge"], DONE_LABEL["merge"]):
            if lab in labels:
                return False, f"{lab} present"
        ok, why = _deps_ok()
        if not ok:
            return ok, why
        # Allow label-less recovery after reset.
        matched = _find_open_prs_closing_issue(client, issue_number)
        if matched:
            return True, "ok"
        merged = _find_merged_prs_closing_issue(client, issue_number)
        if merged and DONE_LABEL["merge"] not in labels:
            return True, "already_merged"
        return False, "no open or merged PR with Closes #N"
    return False, f"unknown phase {phase}"


def _ensure_comment(
    client: ForgePort,
    issue_number: int,
    request_id: str,
    outcome: str,
    body: str,
) -> None:
    try:
        comments = client.get_issue_comments(issue_number)
    except Exception:
        comments = []
    if not isinstance(comments, list):
        comments = []
    if has_marker_comment(comments, request_id, outcome):
        return
    marker = comment_marker(request_id, outcome)
    client.issue_comment(issue_number, f"{body}\n\n{marker}")


def _apply_terminal(
    store: QueueStore,
    client: ForgePort,
    request_id: str,
    issue_number: int,
    decision_kind: str,
    reason: str,
    *,
    close_issue: bool = False,
    add_rejected_label: bool = False,
    comment: bool = True,
) -> None:
    meta = store.snapshot().request_meta.get(request_id) or {}
    if meta.get("outcome") == decision_kind:
        # Already terminal.
        return
    if comment:
        _ensure_comment(client, issue_number, request_id, decision_kind, reason)
    if add_rejected_label:
        try:
            client.issue_update(issue_number, labels_add=["issuesmith:rejected"], labels_remove=[])
        except Exception as exc:
            print(f"warning: failed to add rejected label: {exc}", file=sys.stderr)
    if decision_kind == "superseded":
        try:
            client.issue_update(issue_number, labels_add=["issuesmith:superseded"], labels_remove=[])
        except Exception as exc:
            print(f"warning: failed to add superseded label: {exc}", file=sys.stderr)
    if close_issue:
        try:
            client.issue_close(issue_number)
        except Exception as exc:
            print(f"warning: failed to close issue: {exc}", file=sys.stderr)
    store.complete(request_id, decision_kind, extra_meta={"reason": reason})


def ensure_seeds_enqueued(store: QueueStore, *, seed_path: Path | None = None, now: datetime | None = None) -> int:
    now = now or _now_jst()
    count = 0
    for entry in load_seed_entries(seed_path):
        result = store.enqueue(
            issue=int(entry["issue"]),
            phase=str(entry["phase"]),
            source="night-queue-seed",
            actor_kind="automation",
            priority="low",
            requested_by=["night-queue"],
            requested_at=now.isoformat(),
            seed_key=str(entry["seed_key"]),
        )
        if result.created:
            count += 1
    return count


def dispatch_one(
    now: datetime | None = None,
    client: ForgePort | None = None,
    *,
    store: QueueStore | None = None,
    seed_path: Path | None = None,
    call_llm: Any = None,
    skip_seed: bool = False,
) -> DispatchResult:
    now = now or _now_jst()
    store = store or QueueStore()
    client = client or get_forge(repo=REPO)
    start, end, idle_minutes = seed_window(seed_path)

    if not skip_seed:
        ensure_seeds_enqueued(store, seed_path=seed_path, now=now)

    advance_milestone_chains(store, client, get_config())

    # Fetch issues for active requests.
    snap = store.snapshot()
    issues: dict[int, dict[str, Any]] = {}
    for rid in list(snap.active_order):
        req = store.effective_request(snap, rid)
        if req is None:
            continue
        if req.issue not in issues:
            try:
                data = client.issue_get(
                    req.issue, fields=["state", "labels", "title", "body", "number"]
                )
                data.setdefault("number", req.issue)
                issues[req.issue] = data
            except GitHubApiError as exc:
                if exc.status_code == 404:
                    store.complete(
                        rid,
                        "not_found",
                        extra_meta={"reason": f"issue #{req.issue} does not exist (404)"},
                    )
                    print(f"not_found: #{req.issue} (404)", file=sys.stderr)
                    continue
                print(f"warning: issue_get #{req.issue} failed: {exc}", file=sys.stderr)
                continue
            except Exception as exc:
                print(f"warning: issue_get #{req.issue} failed: {exc}", file=sys.stderr)
                continue

    # Supersede must scan ALL open Issues in the repo, not only those in the queue.
    open_issues = _list_open_issues(client)
    if not open_issues:
        open_issues = [
            iss for iss in issues.values() if str(iss.get("state", "")).upper() == "OPEN"
        ]
    else:
        seen = {i.get("number") for i in open_issues}
        for iss in issues.values():
            if str(iss.get("state", "")).upper() != "OPEN":
                continue
            num = iss.get("number")
            if num not in seen:
                open_issues.append(iss)

    # Deterministic decisions first.
    snap = store.snapshot()
    for rid in list(snap.active_order):
        req = store.effective_request(snap, rid)
        if req is None:
            continue
        issue = issues.get(req.issue)
        if issue is None:
            continue
        decision = deterministic_decision(
            req, issue, open_issues=open_issues, force=store.is_force(snap, rid)
        )
        if decision.kind == "keep":
            continue
        _apply_terminal(
            store,
            client,
            rid,
            req.issue,
            decision.kind,
            decision.reason,
            close_issue=decision.close_issue,
            add_rejected_label=decision.add_rejected_label,
            comment=decision.comment,
        )

    snap = store.snapshot()
    triage_revision = snap.revision
    # Triage if needed (LLM outside lock conceptually — triage() does not hold store lock during LLM).
    if triage_revision > snap.last_triaged_revision and snap.active_order:
        _triage_log = (
            Path(os.environ["ISSUESMITH_QUEUE_DIR"]) / "issuesmith-triage.jsonl"
            if os.environ.get("ISSUESMITH_QUEUE_DIR")
            else DEFAULT_TRIAGE_LOG_PATH
        )
        triage_cfg = get_config().triage
        active_reqs = []
        for rid in snap.active_order:
            req = store.effective_request(snap, rid)
            if req is not None:
                active_reqs.append(req)
        fallback_order = deterministic_order(active_reqs)
        fallback_order = apply_deterministic_order_constraints(
            fallback_order,
            {r.request_id: r for r in active_reqs},
            now=now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc),
        )
        # AC-2: apply priority (deterministic) before waiting on LLM.
        adopted_fallback = store.replace_order(triage_revision, fallback_order)
        if not adopted_fallback:
            store.mark_triaged(triage_revision)
        else:
            now_utc = (
                now.astimezone(timezone.utc)
                if now.tzinfo
                else now.replace(tzinfo=timezone.utc)
            )
            if not triage_cfg.enabled:
                pass  # deterministic order already applied; skip LLM
            elif is_circuit_open(
                _triage_log,
                triage_cfg.circuit_breaker_threshold,
                triage_cfg.circuit_breaker_reset_seconds,
                now=now_utc,
            ):
                append_circuit_open_log(
                    path=_triage_log,
                    order=fallback_order,
                    revision=store.snapshot().revision,
                    now=now_utc,
                )
            else:
                snap2 = store.snapshot()
                # replace_order marked last_triaged==revision; force LLM path.
                llm_snap = replace(snap2, last_triaged_revision=snap2.revision - 1)
                result = triage(
                    llm_snap,
                    issues=issues,
                    store=store,
                    call_llm=call_llm,
                    now=now,
                    triage_log_path=_triage_log,
                    timeout=triage_cfg.timeout,
                    engine=triage_cfg.engine,
                    model=triage_cfg.model,
                    body_chars=triage_cfg.body_chars,
                )
                if result.adopted:
                    active_for_bucket = []
                    for rid in snap2.active_order:
                        req = store.effective_request(snap2, rid)
                        if req is not None:
                            active_for_bucket.append(req)
                    bucket_order = apply_priority_bucket_order(
                        result.order, active_for_bucket
                    )
                    active_set = set(snap2.active_order)
                    new_order = [rid for rid in bucket_order if rid in active_set]
                    for rid in snap2.active_order:
                        if rid not in new_order:
                            new_order.append(rid)
                    adopted_cas = store.replace_order(snap2.revision, new_order)
                    if not adopted_cas:
                        latest = store.snapshot()
                        latest_set = set(latest.active_order)
                        retry_order = [rid for rid in bucket_order if rid in latest_set]
                        for rid in latest.active_order:
                            if rid not in retry_order:
                                retry_order.append(rid)
                        adopted_cas = store.replace_order(latest.revision, retry_order)
                        if not adopted_cas:
                            append_cas_conflict_log(
                                path=_triage_log,
                                order=bucket_order,
                                revision=latest.revision,
                                now=now_utc,
                            )
                            store.mark_triaged(latest.revision)
                # Apply LLM rejects after ordering CAS.
                for d in result.decisions:
                    if d.decision != "reject":
                        continue
                    cur = store.snapshot()
                    req = store.effective_request(cur, d.request_id)
                    if req is None or d.request_id not in cur.active_order:
                        continue
                    _apply_terminal(
                        store,
                        client,
                        d.request_id,
                        req.issue,
                        "rejected",
                        d.reason,
                        close_issue=False,
                        add_rejected_label=True,
                        comment=True,
                    )

    snap = store.snapshot()
    # Release finished in_flight even when the queue is empty (absorbs milestone C0).
    if snap.in_flight:
        with store.dispatch_lock():
            snap = store.snapshot()
            for entry in list(snap.in_flight):
                if _in_flight_should_release(client, entry):
                    issue_num = entry.get("issue")
                    if isinstance(issue_num, int):
                        store.remove_in_flight(issue_num)
            snap = store.snapshot()

    if not snap.active_order:
        return DispatchResult(False, reason="empty queue")

    with store.dispatch_lock():
        snap = store.snapshot()

        for entry in list(snap.in_flight):
            if _in_flight_should_release(client, entry):
                issue_num = entry.get("issue")
                if isinstance(issue_num, int):
                    store.remove_in_flight(issue_num)
        snap = store.snapshot()

        if snap.halt:
            if _halt_resolved(snap, client):
                store.clear_halt()
                snap = store.snapshot()
            else:
                return DispatchResult(False, reason=f"halted: {snap.halt_reason}")

        if snap.last_issue is not None:
            try:
                last = client.issue_get(snap.last_issue, fields=["state", "labels"])
            except Exception as exc:
                return DispatchResult(False, reason=f"last_issue fetch failed: {exc}")
            last_labels = label_names(last)
            last_state = str(last.get("state", "")).upper()
            terminal_ok = last_state == "CLOSED" and (
                "issuesmith:merge-done" in last_labels
                or bool(last_labels & TERMINAL_WITHOUT_MERGE)
            )
            if not terminal_ok and milestone_last_issue_terminal_ok(last_labels):
                terminal_ok = True
            if not terminal_ok:
                if (
                    last_state == "OPEN"
                    and _pipeline_idle_enough_v2(snap, idle_minutes, now)
                ):
                    store.set_halt(
                        True,
                        f"last_issue #{snap.last_issue} is still OPEN while pipeline idle for >= {idle_minutes} minutes",
                    )
                    snap = store.snapshot()
                    return DispatchResult(
                        False,
                        reason=f"halted: {snap.halt_reason}",
                    )
                if last_state == "CLOSED":
                    store.set_halt(
                        True,
                        f"last_issue #{snap.last_issue} is CLOSED without terminal label (labels: {sorted(last_labels)})",
                    )
                    snap = store.snapshot()
                    return DispatchResult(False, reason="previous issue not terminal")
                if _serial_concurrency():
                    return DispatchResult(False, reason="previous issue not terminal")

        # AC-2: re-register develop-running Issues missing from in_flight before
        # orphan detection in _dispatch_pipeline_ready can halt the whole queue.
        recovered = _recover_untracked_in_flight(client, store, snap)
        if recovered:
            snap = store.snapshot()

        if not _dispatch_pipeline_ready(snap, idle_minutes, now):
            return DispatchResult(False, reason="pipeline not idle")

        concurrency = get_config().concurrency
        role_map = _required_engines()
        counts = in_flight_by_engine(snap.in_flight, role_engine_map=role_map)
        last_paused_reason: str | None = None

        for rid in snap.active_order:
            req = store.effective_request(snap, rid)
            if req is None:
                continue
            if not _source_in_window(req.source, req.actor_kind, now, start, end):
                continue
            engine = _resolve_engine(req.phase)
            role = _phase_role_map()[req.phase]
            paused_for_role = _required_engines_paused(role=role)
            if paused_for_role:
                last_paused_reason = (
                    f"required engine paused: {', '.join(paused_for_role)} (role={role})"
                )
                continue
            if counts.get(engine, 0) >= concurrency.limit(engine):
                if concurrency.strict_order:
                    break
                continue
            try:
                issue = client.issue_get(
                    req.issue, fields=["state", "labels", "title", "body", "number"]
                )
            except Exception:
                continue
            decision = deterministic_decision(
                req, issue, open_issues=open_issues, force=store.is_force(snap, rid)
            )
            if decision.kind != "keep":
                _apply_terminal(
                    store,
                    client,
                    rid,
                    req.issue,
                    decision.kind,
                    decision.reason,
                    close_issue=decision.close_issue,
                    add_rejected_label=decision.add_rejected_label,
                    comment=decision.comment,
                )
                continue
            ok, _why = _phase_preconditions(req.phase, issue, client, req.issue)
            if not ok:
                continue

            candidate_repo, candidate_paths = _issue_target_meta(issue)
            conflict = _allow_paths_conflict(
                candidate_repo, candidate_paths, snap.in_flight
            )
            if conflict is not None:
                if concurrency.strict_order:
                    break
                continue

            label = READY_LABEL[req.phase]
            labels = label_names(issue)
            if label not in labels:
                client.issue_update(req.issue, labels_add=[label], labels_remove=[])
            handler = _PHASE_HANDLER.get(req.phase)
            redispatch_note = ""
            if handler and _handler_key_consumed(handler, req.issue):
                # ラベルだけでは watcher が冪等キーで skip する。世代を上げて起動する。
                rc = _trigger_ghdag_redispatch(
                    req.issue, handler, reason=f"queue request {rid} ({req.source})"
                )
                redispatch_note = (
                    " ghdag generation bumped (redispatch)."
                    if rc == 0
                    else f" WARNING: ghdag redispatch failed (rc={rc}); "
                    f"run `ghdag trigger {req.issue} --handler {handler} --redispatch` manually."
                )
            _ensure_comment(
                client,
                req.issue,
                rid,
                "dispatched",
                f"issuesmith queue dispatched `{label}` for request `{rid}`.{redispatch_note}",
            )
            store.add_in_flight(
                req.issue,
                engine,
                role=role,
                allow_paths=candidate_paths,
                target_repo=candidate_repo or None,
            )
            store.complete(rid, "dispatched", extra_meta={"label": label})
            store.set_last_issue(req.issue)
            return DispatchResult(
                True, issue=req.issue, label=label, request_id=rid, reason="dispatched"
            )

        if last_paused_reason:
            return DispatchResult(False, reason=last_paused_reason)

    return DispatchResult(False, reason="no dispatchable request")


def _cmd_enqueue(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if args.queue_path else None,
        state_path=Path(args.state_path) if args.state_path else None,
        lock_path=Path(args.lock_path) if args.lock_path else None,
    )
    try:
        result = store.enqueue(
            issue=args.issue,
            phase=args.phase,
            source=args.source,
            actor_kind=args.actor_kind,
            priority=args.priority,
            requested_by=[args.requested_by],
            force=bool(args.force),
        )
    except QueueValidationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "request_id": result.request_id,
                "created": result.created,
                "merged": result.merged,
                "message": result.message,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_tick(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    result = dispatch_one(store=store, seed_path=Path(args.seed) if getattr(args, "seed", None) else None)
    if result.dispatched:
        print(f"dispatched #{result.issue} {result.label} request={result.request_id}")
    else:
        print(f"no dispatch: {result.reason}")
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    snap = store.snapshot()
    print(
        "issuesmith-queue status: "
        f"revision={snap.revision} "
        f"active={len(snap.active_order)} "
        f"halt={snap.halt} "
        f"last_issue={snap.last_issue}"
    )
    print(f"  in_flight: {_format_in_flight_status(snap)}")
    for entry in snap.in_flight:
        issue_num = entry.get("issue")
        engine = entry.get("engine", "")
        role = entry.get("role")
        role_part = f" role={role}" if role else ""
        print(f"  - in_flight issue=#{issue_num} engine={engine}{role_part}")
    if snap.halt and snap.halt_reason:
        print(f"  halt_reason: {snap.halt_reason}")
    client: ForgePort | None = None
    try:
        client = get_forge(repo=REPO)
    except Exception:
        client = None
    if snap.last_issue is not None and client is not None:
        try:
            last = client.issue_get(snap.last_issue, fields=["state", "labels"])
            last_labels = label_names(last)
            last_state = str(last.get("state", "")).upper()
            terminal_ok = last_state == "CLOSED" and (
                "issuesmith:merge-done" in last_labels
                or bool(last_labels & TERMINAL_WITHOUT_MERGE)
            )
            if not terminal_ok and milestone_last_issue_terminal_ok(last_labels):
                terminal_ok = True
            if last_state == "CLOSED" and not terminal_ok:
                print(
                    f"  warning: last_issue #{snap.last_issue} is CLOSED without terminal label "
                    f"(labels: {sorted(last_labels)})"
                )
        except Exception as exc:
            print(f"  warning: last_issue #{snap.last_issue} fetch failed: {exc}", file=sys.stderr)
    for rid in snap.active_order:
        req = store.effective_request(snap, rid)
        if not req:
            continue
        role = _phase_role_map().get(req.phase, "")
        try:
            engine = _resolve_engine(req.phase)
        except Exception:
            engine = "?"
        print(
            f"  - {rid[:8]}… issue=#{req.issue} phase={req.phase} "
            f"engine={engine} role={role} priority={req.priority} source={req.source}"
        )
        if role:
            paused_for_role = _required_engines_paused(role=role)
            if paused_for_role:
                print(
                    f"    waiting: required engine paused: "
                    f"{', '.join(paused_for_role)} (role={role})"
                )
        if client is None:
            continue
        try:
            issue = client.issue_get(
                req.issue, fields=["state", "labels", "body", "number"]
            )
        except Exception:
            continue
        candidate_repo, candidate_paths = _issue_target_meta(issue)
        conflict = _allow_paths_conflict(
            candidate_repo, candidate_paths, snap.in_flight
        )
        if conflict is None:
            continue
        conflict_entry = next(
            (e for e in snap.in_flight if e.get("issue") == conflict),
            {},
        )
        overlap = _conflict_overlap_path(candidate_paths, conflict_entry)
        print(
            f"    waiting: #{req.issue} (conflict with #{conflict} on {overlap})"
        )
    if client is not None:
        try:
            untracked = _find_untracked_running(client, snap)
            if untracked:
                nums = ", ".join(f"#{n}" for n in sorted(untracked))
                print(f"  warning: untracked running issues: {nums}")
        except Exception as exc:
            print(f"  warning: untracked check failed: {exc}", file=sys.stderr)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Report queue health; flags orphan active_order ids and untracked running Issues."""
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    # Raw state may retain request_ids missing from the JSONL (snapshot filters them).
    state_path = store.state_path
    orphan_ids: list[str] = []
    if state_path.exists():
        try:
            raw = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            with store.lock():
                requests = store._read_requests_unlocked()
            for rid in raw.get("active_order") or []:
                if isinstance(rid, str) and rid not in requests:
                    orphan_ids.append(rid)
    if orphan_ids:
        print(f"orphan active_order ids: {', '.join(orphan_ids)}")
        return 1

    snap = store.snapshot()
    try:
        client = get_forge(repo=REPO)
    except Exception as exc:
        print(f"error: forge client unavailable: {exc}", file=sys.stderr)
        return 1
    try:
        untracked = _find_untracked_running(client, snap)
    except Exception as exc:
        print(f"error: untracked check failed: {exc}", file=sys.stderr)
        return 1
    if untracked:
        nums = ", ".join(f"#{n}" for n in sorted(untracked))
        print(f"untracked running issues: {nums}")
        return 1
    print("doctor ok: no untracked running issues")
    return 0


def _cmd_reset(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    keep_last = bool(getattr(args, "keep_last_issue", False))
    store.clear_halt()
    if keep_last:
        snap = store.snapshot()
        print(f"issuesmith-queue reset: halt=false last_issue={snap.last_issue} (kept)")
    else:
        store.set_last_issue(None)
        print("issuesmith-queue reset: halt=false last_issue=None")
    return 0


def _cmd_skip(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    issue = int(args.issue)
    reason = str(args.reason) if args.reason else ""
    snap = store.snapshot()
    if snap.last_issue is None:
        print("error: no last_issue to skip", file=sys.stderr)
        return 1
    if snap.last_issue != issue:
        print(
            f"error: last_issue is #{snap.last_issue}, not #{issue}",
            file=sys.stderr,
        )
        return 1

    store.set_last_issue(None)
    if snap.halt:
        store.clear_halt()

    client = get_forge(repo=REPO)
    try:
        client.issue_get(issue, fields=["number", "state"])
    except GitHubApiError as exc:
        if exc.status_code == 404:
            print(
                f"skip: skipped comment for #{issue} (404 not found)",
                file=sys.stderr,
            )
        else:
            print(
                f"warning: issue_get #{issue} failed during skip: {exc}",
                file=sys.stderr,
            )
    except Exception as exc:
        print(
            f"warning: issue_get #{issue} failed during skip: {exc}",
            file=sys.stderr,
        )
    else:
        body = f"issuesmith queue: skipped issue #{issue} from last_issue. reason: {reason}"
        try:
            client.issue_comment(issue, body)
        except Exception as exc:
            print(f"warning: failed to comment on #{issue}: {exc}", file=sys.stderr)

    print(json.dumps({"issue": issue, "outcome": "skipped", "reason": reason}, ensure_ascii=False))
    return 0


def _cmd_dequeue(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    request_id = str(args.request_id)
    reason = str(args.reason) if args.reason else ""
    snap = store.snapshot()
    if request_id not in snap.active_order:
        print(f"error: request {request_id} is not in active_order", file=sys.stderr)
        return 1

    req = store.effective_request(snap, request_id)
    store.complete(request_id, "dequeued", extra_meta={"reason": reason})

    if req is not None:
        client = get_forge(repo=REPO)
        try:
            client.issue_get(req.issue, fields=["number", "state"])
        except GitHubApiError as exc:
            if exc.status_code == 404:
                print(
                    f"dequeue: skipped comment for #{req.issue} (404 not found)",
                    file=sys.stderr,
                )
            else:
                print(
                    f"warning: issue_get #{req.issue} failed during dequeue: {exc}",
                    file=sys.stderr,
                )
        except Exception as exc:
            print(
                f"warning: issue_get #{req.issue} failed during dequeue: {exc}",
                file=sys.stderr,
            )
        else:
            body = f"issuesmith queue: dequeued request {request_id}. reason: {reason}"
            try:
                client.issue_comment(req.issue, body)
            except Exception as exc:
                print(f"warning: failed to comment on #{req.issue}: {exc}", file=sys.stderr)

    print(json.dumps({"request_id": request_id, "outcome": "dequeued", "reason": reason}, ensure_ascii=False))
    return 0


def _cmd_audit(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    purged = store.purge_orphan_ids()
    if purged:
        print(f"purged orphan active_order ids: {', '.join(purged)}")

    snap = store.snapshot()
    active_ids = set(snap.active_order)
    completed = set(snap.completed_request_ids)
    errors: list[str] = []
    if active_ids & completed:
        errors.append("active_order intersects completed_request_ids")
    # Same issue+phase uniqueness
    seen: dict[tuple[int, str], str] = {}
    for rid in snap.active_order:
        req = store.effective_request(snap, rid)
        if req is None:
            continue
        key = (req.issue, req.phase)
        if key in seen:
            errors.append(f"duplicate active {key}")
        seen[key] = rid
    if snap.last_triaged_revision > snap.revision:
        errors.append("last_triaged_revision > revision")

    offline = bool(getattr(args, "offline", False))
    if not offline:
        client = get_forge(repo=REPO)
        in_flight: set[int] = set()
        for label in (
            READY_LABEL["draft"],
            RUNNING_LABEL["draft"],
            READY_LABEL["develop"],
            RUNNING_LABEL["develop"],
            READY_LABEL["merge"],
            RUNNING_LABEL["merge"],
        ):
            try:
                issues = client.list_issues(label, state="open")
            except Exception as exc:
                print(f"warning: list_issues({label}) failed: {exc}", file=sys.stderr)
                continue
            if not isinstance(issues, list):
                continue
            for issue in issues:
                if not isinstance(issue, dict):
                    continue
                num = issue.get("number")
                if isinstance(num, int):
                    in_flight.add(num)
        if len(in_flight) >= 2:
            nums = ", ".join(f"#{n}" for n in sorted(in_flight))
            errors.append(f"multiple issues in-flight: {nums}")

    if errors:
        for e in errors:
            print(f"AUDIT FAIL: {e}")
        return 1
    print("AUDIT OK")
    return 0


def _cmd_triage_log(args: argparse.Namespace) -> int:
    path = Path(args.path) if args.path else DEFAULT_TRIAGE_LOG_PATH
    if not path.exists():
        print("no triage log")
        return 0
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    last_n = lines[-args.last :]
    for ln in last_n:
        print(ln)
    return 0


def _cmd_migrate(args: argparse.Namespace) -> int:
    store = QueueStore(
        queue_path=Path(args.queue_path) if getattr(args, "queue_path", None) else None,
        state_path=Path(args.state_path) if getattr(args, "state_path", None) else None,
        lock_path=Path(args.lock_path) if getattr(args, "lock_path", None) else None,
    )
    night_state_path = Path(args.from_night_queue_state)
    seed_path = Path(args.seed)
    dry_run = bool(args.dry_run)

    planned: list[dict[str, Any]] = []
    if night_state_path.exists():
        try:
            night = json.loads(night_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            night = {}
        if isinstance(night, dict):
            planned.append(
                {
                    "action": "import_night_state",
                    "last_issue": night.get("last_issue"),
                    "halt": night.get("halt"),
                    "halt_reason": night.get("halt_reason"),
                }
            )

    for entry in load_seed_entries(seed_path):
        planned.append({"action": "enqueue_seed", **entry})

    if dry_run:
        print(json.dumps({"planned": planned}, ensure_ascii=False, indent=2))
        return 0

    if night_state_path.exists():
        try:
            night = json.loads(night_state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            night = {}
        if isinstance(night, dict):
            if night.get("last_issue") is not None:
                store.set_last_issue(night.get("last_issue"))
            if night.get("halt") is True:
                store.set_halt(True, night.get("halt_reason"))

    created = ensure_seeds_enqueued(store, seed_path=seed_path)
    snap = store.snapshot()
    print(
        json.dumps(
            {
                "created": created,
                "active": len(snap.active_order),
                "last_issue": snap.last_issue,
                "halt": snap.halt,
            },
            ensure_ascii=False,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="issuesmith.queue")
    parser.add_argument("--queue-path", default=None)
    parser.add_argument("--state-path", default=None)
    parser.add_argument("--lock-path", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p_enq = sub.add_parser("enqueue")
    p_enq.add_argument("--issue", type=int, required=True)
    p_enq.add_argument(
        "--phase",
        required=True,
        choices=[p.name for p in _configured_phases()],
    )
    p_enq.add_argument("--source", required=True)
    p_enq.add_argument("--actor-kind", required=True, choices=["human", "automation"])
    p_enq.add_argument("--priority", required=True, choices=["high", "normal", "low"])
    p_enq.add_argument("--requested-by", required=True)
    p_enq.add_argument("--force", action="store_true")
    p_enq.set_defaults(func=_cmd_enqueue)

    p_tick = sub.add_parser("tick")
    p_tick.add_argument("--seed", default=str(DEFAULT_SEED_PATH))
    p_tick.set_defaults(func=_cmd_tick)

    p_status = sub.add_parser("status")
    p_status.set_defaults(func=_cmd_status)

    p_doctor = sub.add_parser(
        "doctor",
        help="Check for untracked develop-running Issues (queue health)",
    )
    p_doctor.set_defaults(func=_cmd_doctor)

    p_reset = sub.add_parser("reset")
    p_reset.add_argument(
        "--keep-last-issue",
        action="store_true",
        help="Clear halt only; keep last_issue (legacy behavior)",
    )
    p_reset.set_defaults(func=_cmd_reset)

    p_skip = sub.add_parser("skip")
    p_skip.add_argument("--issue", type=int, required=True)
    p_skip.add_argument("--reason", default="")
    p_skip.set_defaults(func=_cmd_skip)

    p_deq = sub.add_parser("dequeue")
    p_deq.add_argument("--request-id", required=True)
    p_deq.add_argument("--reason", default="")
    p_deq.set_defaults(func=_cmd_dequeue)

    p_audit = sub.add_parser("audit")
    p_audit.add_argument(
        "--offline",
        action="store_true",
        help="Skip GitHub in-flight checks; local store integrity only",
    )
    p_audit.set_defaults(func=_cmd_audit)

    p_tlog = sub.add_parser("triage-log")
    p_tlog.add_argument("--last", type=int, default=20)
    p_tlog.add_argument("--path", default=None)
    p_tlog.set_defaults(func=_cmd_triage_log)

    p_mig = sub.add_parser("migrate")
    p_mig.add_argument("--from-night-queue-state", default=str(DEFAULT_NIGHT_STATE_PATH))
    p_mig.add_argument("--seed", default=str(DEFAULT_SEED_PATH))
    p_mig.add_argument("--dry-run", action="store_true")
    p_mig.set_defaults(func=_cmd_migrate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
