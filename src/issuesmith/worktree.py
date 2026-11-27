"""Reusable git worktree helpers for issuesmith consumers (#4274).

Public API for base ref resolution, branch validation, fetch retry, worktree
preparation, and cross-repo clone-if-missing. Does not reference workflow
phase names, labels, comments, templates, Slack, or ``jobs/`` paths.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from ghdag.forge import ForgePort, get_forge

from issuesmith.cli_errors import fail
from issuesmith.config import get_config
from issuesmith.contract import Andon, StepResult
from issuesmith.errors import IssuesmithError

_TRANSIENT_FETCH_MARKERS = ("cannot lock ref", "unable to update local ref")
CALLER_STEP_ENV_VAR = "ISSUESMITH_CALLER_STEP"


class WorktreeError(IssuesmithError, RuntimeError):
    """Deterministic worktree preparation failure."""

    code = "FAILED"

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(
            message,
            hint=hint or "check branch name, base ref, fetch state, and worktree paths",
        )


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def _fail(message: str, *, hint: str | None = None) -> None:
    fail("FAILED", message, hint)
    raise WorktreeError(message, hint=hint)


def validate_branch(branch: str) -> None:
    proc = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        _fail(f"invalid branch name: {branch}")


def resolve_base_ref(repo_dir: Path, base: str) -> str:
    """Return the start-point ref for a new branch off *base*.

    ``origin/<base>`` wins so the branch start matches the ``origin/<base>...HEAD``
    range used by pr_scope; a local base may carry unpushed auto-commits.
    ``refs/heads/<base>`` is only a fallback for local-only repos.
    """
    for ref, result in (
        (f"refs/remotes/origin/{base}", f"origin/{base}"),
        (f"refs/heads/{base}", f"refs/heads/{base}"),
    ):
        found = subprocess.run(
            ["git", "-C", str(repo_dir), "show-ref", "--verify", "--quiet", ref],
            capture_output=True,
            check=False,
        )
        if found.returncode == 0:
            return result
    raise WorktreeError(f"base branch not found: {base}")


def _is_transient_fetch_error(stderr: str) -> bool:
    return any(marker in stderr for marker in _TRANSIENT_FETCH_MARKERS)


def _git_common_dir(repo_dir: Path) -> Path:
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return repo_dir / ".git"
    raw = proc.stdout.strip()
    path = Path(raw)
    if not path.is_absolute():
        path = (repo_dir / path).resolve()
    return path


def _lock_wait_seconds() -> int:
    raw = os.environ.get("ISSUESMITH_FETCH_LOCK_WAIT", "30")
    try:
        return max(0, int(raw))
    except ValueError:
        return 30


_FETCH_TIMEOUT_DEFAULT_SEC = 300.0


def _fetch_timeout_sec() -> float:
    raw = os.environ.get("ISSUESMITH_FETCH_TIMEOUT_SEC", str(_FETCH_TIMEOUT_DEFAULT_SEC))
    try:
        value = float(raw)
    except ValueError:
        return _FETCH_TIMEOUT_DEFAULT_SEC
    if value <= 0:
        return _FETCH_TIMEOUT_DEFAULT_SEC
    return value


def _fetch_failure_reason(returncode: int, stderr: str, *, timed_out: bool) -> str:
    if timed_out:
        return f"timed out after {_fetch_timeout_sec()} s"
    lines = [line.strip() for line in (stderr or "").splitlines() if line.strip()]
    if lines:
        return lines[-1]
    return f"git fetch exited {returncode}"


def _run_git_fetch(repo_dir: Path, base: str) -> tuple[int, str, bool]:
    """Run ``git fetch`` for *base*; returns ``(returncode, stderr, timed_out)``."""
    cmd = [
        "git",
        "-C",
        str(repo_dir),
        "fetch",
        "origin",
        f"refs/heads/{base}:refs/remotes/origin/{base}",
    ]
    timeout = _fetch_timeout_sec()
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        _stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode or 0, stderr or "", False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        _stdout, stderr = proc.communicate()
        limit = _fetch_timeout_sec()
        print(
            f"P0_FETCH_TIMEOUT: git fetch origin {base} exceeded {limit} s in {repo_dir}",
            file=sys.stderr,
        )
        return 1, stderr or "", True


def _fetch_base(
    repo_dir: Path, base: str, *, max_attempts: int
) -> tuple[bool, bool, str]:
    """Fetch origin *base* with lock, timeout, and retries. Third value is failure reason."""
    lock_dir = _git_common_dir(repo_dir) / "issuesmith-fetch.lock"
    lock_wait = _lock_wait_seconds()
    transient_attempts = 0

    for attempt in range(1, max_attempts + 1):
        waited = 0
        lock_acquired = False
        while True:
            try:
                lock_dir.mkdir()
                lock_acquired = True
                break
            except FileExistsError:
                waited += 1
                if waited >= lock_wait:
                    break
                _sleep(1)

        returncode, stderr, timed_out = _run_git_fetch(repo_dir, base)
        if lock_acquired:
            try:
                lock_dir.rmdir()
            except OSError:
                pass

        if returncode == 0:
            return True, False, ""

        if timed_out:
            return False, False, _fetch_failure_reason(returncode, stderr, timed_out=True)

        if _is_transient_fetch_error(stderr):
            transient_attempts += 1
            print(
                f"P0_FETCH_RETRY: attempt={attempt} (transient ref conflict)",
                file=sys.stderr,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo_dir),
                    "update-ref",
                    "-d",
                    f"refs/remotes/origin/{base}",
                ],
                capture_output=True,
                check=False,
            )
            _sleep(float(attempt))
            continue

        if stderr:
            print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n")
        reason = _fetch_failure_reason(returncode, stderr, timed_out=False)
        return False, False, reason

    if transient_attempts >= max_attempts:
        return False, True, ""
    return False, False, ""


def fetch_base_gate_result(
    repo_dir: Path, base: str, *, max_attempts: int = 3
) -> tuple[bool, bool]:
    """Fetch origin base with lock + retries.

    Returns ``(success, transient_exhausted)``. ``transient_exhausted`` is
    ``True`` only when every attempt failed with a transient ref-lock error.
    """
    success, transient_exhausted, _reason = _fetch_base(
        repo_dir, base, max_attempts=max_attempts
    )
    return success, transient_exhausted


def fetch_base_with_retry(repo_dir: Path, base: str, *, max_attempts: int = 3) -> bool:
    """Fetch origin base with mkdir lock + transient ref-lock retries.

    Returns ``True`` on success. On exhaustion returns ``False``; callers that
    need a raised error should use :func:`fetch_base_or_raise`.
    """
    success, _ = fetch_base_gate_result(repo_dir, base, max_attempts=max_attempts)
    return success


def fetch_base_or_raise(repo_dir: Path, base: str, *, remote: str = "origin") -> None:
    """Fetch *base* from *remote*, raising :class:`WorktreeError` after retry exhaustion."""
    success, _transient, reason = _fetch_base(repo_dir, base, max_attempts=3)
    if success:
        return
    suffix = f": {reason}" if reason else ""
    raise WorktreeError(f"failed to fetch {remote}:{base}{suffix}")


def prepare_worktree(
    repo_dir: Path,
    worktree_dir: Path,
    branch: str,
    base_ref: str,
) -> None:
    if worktree_dir.exists():
        if not worktree_dir.is_dir():
            _fail(f"worktree path is not a directory: {worktree_dir}")
        inside = subprocess.run(
            ["git", "-C", str(worktree_dir), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
        if inside.returncode != 0:
            _fail(f"existing path is not a git worktree: {worktree_dir}")

        expected = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        actual = subprocess.run(
            [
                "git",
                "-C",
                str(worktree_dir),
                "rev-parse",
                "--path-format=absolute",
                "--git-common-dir",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if expected.returncode != 0 or actual.returncode != 0:
            _fail(f"failed to resolve git-common-dir for worktree: {worktree_dir}")
        if expected.stdout.strip() != actual.stdout.strip():
            _fail(f"existing worktree belongs to a different repository: {worktree_dir}")

        actual_branch = subprocess.run(
            ["git", "-C", str(worktree_dir), "branch", "--show-current"],
            capture_output=True,
            text=True,
            check=False,
        )
        got = (actual_branch.stdout or "").strip()
        if got != branch:
            _fail(f"existing worktree branch mismatch: expected={branch} actual={got}")
        return

    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    has_branch = subprocess.run(
        ["git", "-C", str(repo_dir), "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        capture_output=True,
        check=False,
    )
    if has_branch.returncode == 0:
        add = subprocess.run(
            ["git", "-C", str(repo_dir), "worktree", "add", str(worktree_dir), branch],
            capture_output=True,
            text=True,
            check=False,
        )
        if add.returncode != 0:
            _fail(f"failed to attach existing branch: {branch}")
        return

    add = subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "worktree",
            "add",
            "-b",
            branch,
            str(worktree_dir),
            base_ref,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if add.returncode != 0:
        detail = (add.stderr or add.stdout or "").strip()
        msg = f"failed to create worktree: {worktree_dir}"
        if detail:
            msg = f"{msg}: {detail}"
        _fail(msg)


def clone_if_missing(clone_path: Path, target_repo: str, base_branch: str) -> None:
    """Clone *target_repo* at *base_branch* when *clone_path* does not exist.

    An existing checkout is reused without re-cloning.
    """
    if clone_path.exists():
        return

    clone_path.parent.mkdir(parents=True, exist_ok=True)
    clone_url = get_config().clone_url(target_repo)
    clone = subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            base_branch,
            clone_url,
            str(clone_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if clone.returncode != 0:
        if clone.stderr:
            print(clone.stderr, file=sys.stderr, end="")
        raise WorktreeError(f"failed to clone {target_repo}")


def _assert_git_repository(repo_dir: Path) -> None:
    git_dir = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "--git-dir"],
        capture_output=True,
        check=False,
    )
    if git_dir.returncode != 0:
        _fail(f"target clone is not a git repository: {repo_dir}")


def _assert_remote_base(repo_dir: Path, base: str) -> None:
    remote_ok = subprocess.run(
        [
            "git",
            "-C",
            str(repo_dir),
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/remotes/origin/{base}",
        ],
        capture_output=True,
        check=False,
    )
    if remote_ok.returncode != 0:
        _fail(f"fetched base ref not found: origin/{base}")


def prepare_local_worktree(
    repo_dir: Path,
    worktree_path: Path,
    branch: str,
    base_branch: str,
) -> Path:
    """Fetch base (when remote exists), prepare *worktree_path*, return its path."""
    validate_branch(branch)
    validate_branch(base_branch)
    base_ref = resolve_base_ref(repo_dir, base_branch)
    prepare_worktree(repo_dir, worktree_path, branch, base_ref)
    return worktree_path


def github_client() -> ForgePort:
    """Return the configured forge client for GitHub API calls."""
    return get_forge()


def require_yaml_metadata(body: str) -> None:
    """Require Issue body to start with a YAML metadata fenced block."""
    first = body.split("\n", 1)[0] if body else ""
    if not first.startswith("```yaml"):
        _fail(
            "issue body must start with a yaml metadata block "
            "(base_branch / allow_paths / target_repo); see #2532"
        )


def handle_milestone(
    client: ForgePort,
    issue_number: int,
    *,
    comment: str,
    on_transition: Callable[[int], None] | None = None,
    stderr_error: str = "scope:milestone issue cannot enter implementation",
) -> StepResult:
    """Block milestone-scoped issues from entering implementation.

    *on_transition* and *comment* are supplied by the caller so this helper
    stays free of workflow-specific defaults.
    """
    if on_transition is not None:
        try:
            on_transition(issue_number)
        except Exception as exc:  # noqa: BLE001 — caller may use best-effort transition
            print(f"P0 milestone transition failed: {exc}", file=sys.stderr)
    try:
        client.issue_comment(issue_number, comment)
    except Exception as exc:  # noqa: BLE001
        print(f"P0 milestone comment failed: {exc}", file=sys.stderr)
    fail(
        "FAILED",
        stderr_error,
        "milestone-scoped issues cannot enter implementation; use milestone workflow",
    )
    return StepResult(exit_code=1, pipeline_status="WORKTREE_FAILED")


def _branch_on_remote(worktree_dir: Path, branch: str) -> bool | None:
    """Return whether *branch* exists on ``origin``, or ``None`` if unknown."""
    if not branch:
        return None
    proc = subprocess.run(
        [
            "git",
            "-C",
            str(worktree_dir),
            "ls-remote",
            "--exit-code",
            "--heads",
            "origin",
            f"refs/heads/{branch}",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode == 0:
        return True
    if proc.returncode == 2:
        return False
    return None


def _stale_base_resume_command(issue_number: int, caller_step: str | None) -> tuple[str, list[str]]:
    """Resume CLI hint and andon option keys after a STALE_BASE rebase conflict."""
    from issuesmith.config import get_config
    from issuesmith.projection import phase_for_step, phase_steps

    base = f"issuesmith resume {issue_number}"
    if not caller_step:
        return f"{base} --force", [
            "manual_rebase_then_resume",
            "discard_branch_then_restart_p0",
        ]
    cfg = get_config()
    phase_name = phase_for_step(caller_step, cfg)
    if phase_name is None:
        return f"{base} --force", [
            "manual_rebase_then_resume",
            "discard_branch_then_restart_p0",
        ]
    phase = next((p for p in cfg.phases if p.name == phase_name), None)
    if phase is None:
        return f"{base} --force", [
            "manual_rebase_then_resume",
            "discard_branch_then_restart_p0",
        ]
    steps = phase_steps(phase)
    try:
        idx = steps.index(caller_step)
    except ValueError:
        return f"{base} --force", [
            "manual_rebase_then_resume",
            "discard_branch_then_restart_p0",
        ]
    if idx + 1 >= len(steps):
        return f"{base} --force", [
            "manual_rebase_then_resume",
            "discard_branch_then_restart_p0",
        ]
    next_step = steps[idx + 1]
    return (
        f"{base} --from {next_step} --force",
        [
            f"manual_rebase_then_resume_{next_step}",
            f"discard_branch_then_restart_{caller_step}",
        ],
    )


def ensure_base_included(
    worktree_dir: Path,
    base_branch: str,
    client: ForgePort,
    issue_number: int,
    *,
    stale_base_comment_template: str,
    caller_step: str | None = None,
) -> StepResult | None:
    """Return ``None`` when HEAD includes origin/*base_branch*; rebase or fail otherwise."""
    if caller_step is None:
        from_env = os.environ.get(CALLER_STEP_ENV_VAR, "").strip()
        if from_env:
            caller_step = from_env
    rev_parse = subprocess.run(
        ["git", "-C", str(worktree_dir), "rev-parse", f"origin/{base_branch}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if rev_parse.returncode != 0:
        return None

    base_sha = rev_parse.stdout.strip()

    ancestor = subprocess.run(
        ["git", "-C", str(worktree_dir), "merge-base", "--is-ancestor", base_sha, "HEAD"],
        capture_output=True,
        check=False,
    )
    if ancestor.returncode == 0:
        return None

    from issuesmith.bump_commits import BumpCommitsError, drop_bump_commits_before_rebase

    try:
        drop_bump_commits_before_rebase(worktree_dir, base_branch)
    except BumpCommitsError as exc:
        return _stale_base_rebase_conflict_andon(
            worktree_dir,
            base_branch,
            issue_number,
            caller_step,
            f"bump commit removal failed: {exc}",
        )

    ancestor = subprocess.run(
        ["git", "-C", str(worktree_dir), "merge-base", "--is-ancestor", base_sha, "HEAD"],
        capture_output=True,
        check=False,
    )
    if ancestor.returncode == 0:
        print(
            f"P0_REBASE: auto-synced with origin/{base_branch}",
            file=sys.stderr,
        )
        return None

    branch_proc = subprocess.run(
        ["git", "-C", str(worktree_dir), "branch", "--show-current"],
        capture_output=True,
        text=True,
        check=False,
    )
    current_branch = (branch_proc.stdout or "").strip()

    rebase = subprocess.run(
        ["git", "-C", str(worktree_dir), "rebase", f"origin/{base_branch}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if rebase.returncode == 0:
        print(
            f"P0_REBASE: auto-synced with origin/{base_branch}",
            file=sys.stderr,
        )
        return None

    from issuesmith.ops.publish import continue_rebase_past_changelog

    changelog_remaining = continue_rebase_past_changelog(worktree_dir, rebase)
    if not changelog_remaining:
        print(
            f"P0_REBASE: auto-synced with origin/{base_branch}",
            file=sys.stderr,
        )
        return None

    on_remote = _branch_on_remote(worktree_dir, current_branch)
    if on_remote is False:
        subprocess.run(
            ["git", "-C", str(worktree_dir), "rebase", "--abort"],
            capture_output=True,
            check=False,
        )
        head_proc = subprocess.run(
            ["git", "-C", str(worktree_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        discarded_sha = (head_proc.stdout or "").strip()
        reset = subprocess.run(
            [
                "git",
                "-C",
                str(worktree_dir),
                "reset",
                "--hard",
                f"origin/{base_branch}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if reset.returncode != 0:
            detail = (reset.stderr or reset.stdout or "").strip()
            return _stale_base_rebase_conflict_andon(
                worktree_dir,
                base_branch,
                issue_number,
                caller_step,
                f"reset to origin/{base_branch} failed: {detail}",
            )
        print(
            f"P0_REBASE: discarded local-only branch {current_branch} "
            f"(was {discarded_sha}); restarted from origin/{base_branch}",
            file=sys.stderr,
        )
        return None

    return _stale_base_rebase_conflict_andon(
        worktree_dir,
        base_branch,
        issue_number,
        caller_step,
        None,
    )


def _stale_base_rebase_conflict_andon(
    worktree_dir: Path,
    base_branch: str,
    issue_number: int,
    caller_step: str | None,
    detail: str | None,
) -> StepResult:
    conflict_proc = subprocess.run(
        ["git", "-C", str(worktree_dir), "diff", "--name-only", "--diff-filter=U"],
        capture_output=True,
        text=True,
        check=False,
    )
    conflict_files = (conflict_proc.stdout or "").strip() or "(unavailable)"
    if detail:
        conflict_files = f"{detail}; conflicts: {conflict_files}"

    subprocess.run(
        ["git", "-C", str(worktree_dir), "rebase", "--abort"],
        capture_output=True,
        check=False,
    )

    worktree = str(worktree_dir)
    resume_cmd, options = _stale_base_resume_command(issue_number, caller_step)
    summary = (
        f"P0 rebase onto origin/{base_branch} conflicted in: {conflict_files}.\n"
        f"Option 1: In {worktree}, run `git rebase origin/{base_branch}`, resolve "
        f"conflicts, then `{resume_cmd}` to resume this generation.\n"
        f"Option 2: If discarding this branch's work, recreate the worktree/branch "
        f"and restart from P0."
    )
    print("PIPELINE_STATUS: STALE_BASE", file=sys.stderr)
    return StepResult(
        status="andon",
        andon=Andon(
            kind="decision",
            rule_id="p0.base_rebase_conflict",
            summary=summary,
            options=options,
        ),
    )


def assert_jobs_clean(worktree_dir: Path, jobs_path: str) -> None:
    """Fail when *jobs_path* under *worktree_dir* is dirty in git status."""
    proc = subprocess.run(
        ["git", "-C", str(worktree_dir), "status", "--porcelain", "--", jobs_path],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        msg = f"failed to check {jobs_path} dirty status in {worktree_dir}"
        if detail:
            msg = f"{msg}: {detail}"
        _fail(msg)
    if (proc.stdout or "").strip():
        msg = (
            f"P0: worktree has dirty files under {jobs_path} after prepare. "
            "Daemon auto-commits may have leaked into the tree."
        )
        print(msg, file=sys.stderr)
        _fail(msg)


def prepare_cross_repo_worktree(
    clone_path: Path,
    worktree_path: Path,
    *,
    target_repo: str,
    base_branch: str,
    branch: str,
) -> Path:
    """Clone-if-missing, fetch base, and prepare *worktree_path* for *branch*."""
    validate_branch(branch)
    validate_branch(base_branch)
    clone_if_missing(clone_path, target_repo, base_branch)
    _assert_git_repository(clone_path)
    fetch_base_or_raise(clone_path, base_branch, remote=target_repo)
    _assert_remote_base(clone_path, base_branch)
    prepare_worktree(clone_path, worktree_path, branch, f"origin/{base_branch}")
    return worktree_path


__all__ = [
    "WorktreeError",
    "assert_jobs_clean",
    "clone_if_missing",
    "ensure_base_included",
    "fetch_base_or_raise",
    "fetch_base_with_retry",
    "github_client",
    "handle_milestone",
    "prepare_cross_repo_worktree",
    "prepare_local_worktree",
    "prepare_worktree",
    "require_yaml_metadata",
    "resolve_base_ref",
    "validate_branch",
]
