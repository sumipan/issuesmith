"""issuesmith.observe.main_health -- base-branch health check (#3664).

``check()`` runs the configured command in a detached worktree of the base branch and
persists the result to a state file; ``observe()`` only reads that file (no test run per
watchdog tick). Infrastructure failures raise :class:`MainHealthError` and leave the state
untouched so they are never mistaken for "main is red".
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Literal

if TYPE_CHECKING:
    from issuesmith.config import IssuesmithConfig, MainHealthConfig

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

STATE_FILENAME = "issuesmith-main-health.json"


class MainHealthError(RuntimeError):
    """The health check could not run (git / worktree / timeout); state is not updated."""


@dataclass(frozen=True)
class MainHealthState:
    sha: str
    status: Literal["green", "red"]
    reason: str
    failing: tuple[str, ...]
    checked_at: str


def state_path(config: "IssuesmithConfig") -> Path:
    return config.paths.queue_state.parent / STATE_FILENAME


def load_state(path: Path) -> MainHealthState | None:
    """Return the persisted state, or None when missing, corrupt, or mistyped."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    sha = data.get("sha")
    status = data.get("status")
    reason = data.get("reason", "")
    failing = data.get("failing", [])
    checked_at = data.get("checked_at", "")
    if not isinstance(sha, str) or not sha or status not in ("green", "red"):
        return None
    if not isinstance(reason, str) or not isinstance(checked_at, str):
        return None
    if not isinstance(failing, list) or not all(isinstance(x, str) for x in failing):
        return None
    return MainHealthState(
        sha=sha,
        status=status,
        reason=reason,
        failing=tuple(failing),
        checked_at=checked_at,
    )


def _write_state(path: Path, state: MainHealthState) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = asdict(state)
    payload["failing"] = list(state.failing)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _failed_ids(output: str) -> tuple[str, ...]:
    """Unique test IDs from pytest ``FAILED`` / ``ERROR`` summary lines, in order."""
    ids: dict[str, None] = {}
    for line in output.splitlines():
        if line.startswith(("FAILED ", "ERROR ")):
            ids.setdefault(line.split(" ", 1)[1].split(" - ")[0], None)
    return tuple(ids)


def _git(run: Runner, worktree: Path, *args: str) -> str:
    cmd = ["git", "-C", str(worktree), *args]
    try:
        proc = run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise MainHealthError(f"{' '.join(cmd)}: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise MainHealthError(f"{' '.join(cmd)} failed (exit {proc.returncode}): {detail}")
    return (proc.stdout or "").strip()


def check(
    cfg: "MainHealthConfig", path: Path, *, run: Runner = subprocess.run,
) -> MainHealthState:
    """Run the health command on the latest ``origin/<base_branch>`` and persist the result.

    The command is skipped when the fetched SHA equals the persisted one (SHA cache).
    """
    worktree = cfg.worktree
    if not worktree.is_dir():
        raise MainHealthError(f"main_health worktree not found: {worktree}")

    _git(run, worktree, "fetch", "origin", cfg.base_branch)
    sha = _git(run, worktree, "rev-parse", "FETCH_HEAD")
    if not sha:
        raise MainHealthError("git rev-parse FETCH_HEAD returned no SHA")

    cached = load_state(path)
    if cached is not None and cached.sha == sha:
        return cached

    _git(run, worktree, "checkout", "--detach", "--force", sha)
    try:
        proc = run(
            list(cfg.command),
            cwd=str(worktree),
            capture_output=True,
            text=True,
            check=False,
            timeout=cfg.timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise MainHealthError(
            f"main_health command timed out after {cfg.timeout_seconds}s"
        ) from exc
    except OSError as exc:
        raise MainHealthError(f"main_health command failed to start: {exc}") from exc

    checked_at = datetime.now(timezone.utc).isoformat()
    if proc.returncode == 0:
        state = MainHealthState(
            sha=sha, status="green", reason="", failing=(), checked_at=checked_at,
        )
    else:
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        state = MainHealthState(
            sha=sha,
            status="red",
            reason=f"exit {proc.returncode}",
            failing=_failed_ids(output),
            checked_at=checked_at,
        )
    _write_state(path, state)
    return state
