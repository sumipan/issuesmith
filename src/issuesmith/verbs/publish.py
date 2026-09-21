"""publish_branch verb — public extraction from ops.publish."""

from __future__ import annotations

import subprocess
from pathlib import Path

from issuesmith.ops.publish import _push_branch


def publish_branch(repo_cwd: Path, branch: str, remote: str = "origin") -> None:
    """Push *branch* to *remote*, using force-with-lease when already published.

    Delegates to the rebase-aware push logic in ops.publish for the default
    "origin" remote; falls back to a plain push for non-origin remotes.
    """
    repo_cwd = Path(repo_cwd)
    if remote == "origin":
        result = _push_branch(repo_cwd, branch)
        if result is not None:
            raise RuntimeError(f"push failed [{result.status}]: {result.stderr}")
        return

    proc = subprocess.run(
        ["git", "-C", str(repo_cwd), "push", "-u", remote, branch],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"git push to {remote} failed: {detail}")


__all__ = ["publish_branch"]
