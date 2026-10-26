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
LOG_FILENAME = "issuesmith-main-health.log"
LOG_MAX_BYTES = 1_048_576

_LOG_HEADER_PREFIX = "# main_health sha="
_TRUNCATION_LINE = "# (truncated: kept last 1048576 bytes)"
_OUTPUT_TAIL_LINES = 80
_OUTPUT_TAIL_LINE_CHARS = 500


class MainHealthError(RuntimeError):
    """The health check could not run (git / worktree / timeout); state is not updated."""


@dataclass(frozen=True)
class MainHealthState:
    sha: str
    status: Literal["green", "red"]
    reason: str
    failing: tuple[str, ...]
    checked_at: str
    output_tail: str = ""


def state_path(config: "IssuesmithConfig") -> Path:
    return config.paths.queue_state.parent / STATE_FILENAME


def log_path(config: "IssuesmithConfig") -> Path:
    return state_path(config).parent / LOG_FILENAME


def _parse_log_header(line: str) -> tuple[str, str] | None:
    if not line.startswith(_LOG_HEADER_PREFIX):
        return None
    rest = line[len(_LOG_HEADER_PREFIX) :]
    exit_marker = " exit="
    exit_at = rest.find(exit_marker)
    if exit_at < 0:
        return None
    sha = rest[:exit_at]
    if not sha:
        return None
    after_exit = rest[exit_at + len(exit_marker) :]
    checked_marker = " checked_at="
    checked_at = after_exit.find(checked_marker)
    if checked_at < 0:
        return None
    return sha, after_exit[checked_at + len(checked_marker) :]


def _truncate_payload(payload: str) -> tuple[str, bool]:
    data = payload.encode("utf-8")
    if len(data) <= LOG_MAX_BYTES:
        return payload, False
    tail = data[-LOG_MAX_BYTES:]
    return tail.decode("utf-8", errors="replace"), True


def _write_log(
    path: Path,
    *,
    state: MainHealthState,
    returncode: int,
    stdout: str,
    stderr: str,
) -> None:
    payload, truncated = _truncate_payload((stdout or "") + "\n\n" + (stderr or ""))
    header = (
        f"{_LOG_HEADER_PREFIX}{state.sha} exit={returncode} "
        f"checked_at={state.checked_at}"
    )
    parts = [header]
    if truncated:
        parts.append(_TRUNCATION_LINE)
    parts.append(payload)
    content = "\n".join(parts)

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_log_tail(
    path: Path,
    state: MainHealthState,
    *,
    max_lines: int = 20,
) -> tuple[str, ...]:
    if max_lines <= 0:
        return ()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return ()
    lines = text.splitlines()
    if not lines:
        return ()
    parsed = _parse_log_header(lines[0])
    if parsed is None:
        return ()
    log_sha, log_checked_at = parsed
    if log_sha != state.sha or log_checked_at != state.checked_at:
        return ()
    return tuple(lines[-max_lines:])


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
    output_tail = data.get("output_tail", "")
    if not isinstance(output_tail, str):
        output_tail = ""
    return MainHealthState(
        sha=sha,
        status=status,
        reason=reason,
        failing=tuple(failing),
        checked_at=checked_at,
        output_tail=output_tail,
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


def _output_tail(output: str) -> str:
    if not output:
        return ""
    lines = output.splitlines()
    tail_lines = lines[-_OUTPUT_TAIL_LINES:]
    trimmed = [line[:_OUTPUT_TAIL_LINE_CHARS] for line in tail_lines]
    return "\n".join(trimmed)


def _failed_ids(output: str) -> tuple[str, ...]:
    """Unique test IDs from pytest ``FAILED`` / ``ERROR`` summary lines, in order."""
    ids: dict[str, None] = {}
    for line in output.splitlines():
        if line.startswith(("FAILED ", "ERROR ")):
            ids.setdefault(line.split(" ", 1)[1].split(" - ")[0], None)
    return tuple(ids)


def _git_cwd(run: Runner, cwd: Path, *args: str) -> str:
    cmd = ["git", "-C", str(cwd), *args]
    try:
        proc = run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise MainHealthError(f"{' '.join(cmd)}: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise MainHealthError(f"{' '.join(cmd)} failed (exit {proc.returncode}): {detail}")
    return (proc.stdout or "").strip()


def _git(run: Runner, worktree: Path, *args: str) -> str:
    return _git_cwd(run, worktree, *args)


def _ensure_worktree(
    cfg: "MainHealthConfig", *, repo_root: Path | None, run: Runner,
) -> None:
    worktree = cfg.worktree
    if worktree.is_dir():
        return
    if worktree.exists():
        raise MainHealthError(f"main_health worktree is not a directory: {worktree}")
    if repo_root is None:
        raise MainHealthError(f"main_health worktree not found: {worktree}")

    try:
        worktree.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise MainHealthError(
            f"main_health worktree parent creation failed: {worktree.parent}: {exc}"
        ) from exc
    _git_cwd(run, repo_root, "worktree", "prune")
    _git_cwd(run, repo_root, "fetch", "origin", cfg.base_branch)
    _git_cwd(
        run,
        repo_root,
        "worktree",
        "add",
        "--detach",
        str(worktree),
        "FETCH_HEAD",
    )


def check(
    cfg: "MainHealthConfig",
    path: Path,
    *,
    repo_root: Path | None = None,
    run: Runner = subprocess.run,
) -> MainHealthState:
    """Run the health command on the latest ``origin/<base_branch>`` and persist the result.

    The command is skipped when the fetched SHA equals the persisted one (SHA cache).
    """
    worktree = cfg.worktree
    _ensure_worktree(cfg, repo_root=repo_root, run=run)

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
            sha=sha,
            status="green",
            reason="",
            failing=(),
            checked_at=checked_at,
            output_tail="",
        )
    else:
        output = (proc.stdout or "") + "\n" + (proc.stderr or "")
        failing = _failed_ids(output)
        reason = f"exit {proc.returncode}"
        if not failing:
            reason = f"exit {proc.returncode} (no FAILED/ERROR lines)"
        state = MainHealthState(
            sha=sha,
            status="red",
            reason=reason,
            failing=failing,
            checked_at=checked_at,
            output_tail=_output_tail(output),
        )
    _write_state(path, state)
    log_file = path.parent / LOG_FILENAME
    try:
        _write_log(
            log_file,
            state=state,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
    except OSError:
        pass
    return state
