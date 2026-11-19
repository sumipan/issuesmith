"""Resolve a worktree-specific Python bin directory via ``tests.python_bin_command``."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from issuesmith.config import get_config

_PYTHON_BIN_TIMEOUT_SEC = 600.0
_STDERR_TAIL = 2000


class WorktreePythonError(RuntimeError):
    """``python_bin_command`` failed or returned an unusable path."""


def _stderr_tail(proc: subprocess.CompletedProcess[str]) -> str:
    text = (proc.stderr or "") + (proc.stdout or "")
    if len(text) <= _STDERR_TAIL:
        return text
    return text[-_STDERR_TAIL:]


def python_bin_for(worktree: Path) -> Path | None:
    """Return the bin directory for ``worktree``, or ``None`` when not configured."""
    cfg = get_config()
    command = cfg.tests.python_bin_command
    if not command:
        return None
    worktree_abs = str(worktree.resolve())
    cmd = [*command, worktree_abs]
    cmd_display = " ".join(cmd)
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cfg.root),
            capture_output=True,
            text=True,
            timeout=_PYTHON_BIN_TIMEOUT_SEC,
            check=False,
        )
    except OSError as exc:
        raise WorktreePythonError(
            f"python_bin_command could not be started: {cmd_display} "
            f"(worktree={worktree_abs}): {exc}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        partial = (exc.stderr or exc.stdout or b"")
        if isinstance(partial, bytes):
            tail = partial.decode(errors="replace")[-_STDERR_TAIL:]
        else:
            tail = str(partial)[-_STDERR_TAIL:]
        raise WorktreePythonError(
            f"python_bin_command timed out after {_PYTHON_BIN_TIMEOUT_SEC:.0f}s: "
            f"{cmd_display} (worktree={worktree_abs}): {tail}"
        ) from exc
    if proc.returncode != 0:
        raise WorktreePythonError(
            f"python_bin_command exited {proc.returncode}: {cmd_display} "
            f"(worktree={worktree_abs}): {_stderr_tail(proc)}"
        )
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    if not lines:
        raise WorktreePythonError(
            f"python_bin_command produced no output: {cmd_display} "
            f"(worktree={worktree_abs})"
        )
    bin_dir = Path(lines[-1])
    if not bin_dir.is_absolute():
        raise WorktreePythonError(
            f"python_bin_command output is not an absolute path: {bin_dir!s} "
            f"({cmd_display}, worktree={worktree_abs})"
        )
    python_exe = bin_dir / "python"
    if not python_exe.is_file() or not os.access(python_exe, os.X_OK):
        raise WorktreePythonError(
            f"python_bin_command path has no executable python: {python_exe} "
            f"({cmd_display}, worktree={worktree_abs})"
        )
    return bin_dir.resolve()
