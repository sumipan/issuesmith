"""prepare_worktree verb — public extraction from steps.p0_worktree."""

from __future__ import annotations

from pathlib import Path

from issuesmith.steps.p0_worktree import (
    prepare_worktree as _prepare_worktree,
    resolve_base_ref,
)


def prepare_worktree(
    repo: Path,
    base: str,
    branch: str,
    path: Path,
    *,
    external_dir: Path | None = None,
) -> None:
    """Create or verify a git worktree at *path* branched from *base*.

    When *external_dir* is given, it is used as the git repository root
    (cross-repo case); otherwise *repo* is used.
    """
    repo_dir = Path(external_dir) if external_dir is not None else Path(repo)
    base_ref = resolve_base_ref(repo_dir, base)
    _prepare_worktree(repo_dir, Path(path), branch, base_ref)


__all__ = ["prepare_worktree"]
