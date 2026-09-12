"""P0 worktree Python step — replaces p0-worktree.md bash (#3168 / #3060).

Exit contract: success → exit 0 + WORKTREE_READY;
failure → exit 1 + WORKTREE_FAILED.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ghdag.forge import ForgePort, get_forge
from ghdag.workflow.state_machine import _load_workflow_config, transition

from issuesmith.config import StepConfig, get_config
from issuesmith.steps.base import StepContext, StepResult

_MILESTONE_COMMENT = """## P0 中断: scope:milestone イシュー
このイシューは設計専用です。develop-ready による自動実装は禁止されています。
PIPELINE_STATUS: MILESTONE_BLOCKED"""

_TRANSIENT_FETCH_MARKERS = ("cannot lock ref", "unable to update local ref")


class WorktreeError(Exception):
    """Deterministic P0 failure (maps to WORKTREE_FAILED)."""


def _github_client() -> ForgePort:
    return get_forge()


def _repo_root() -> Path:
    return get_config().root


def _workflow_path() -> Path:
    return get_config().paths.workflow


def _sleep(seconds: float) -> None:
    import time

    time.sleep(seconds)


def _fail(message: str) -> None:
    print(f"WORKTREE_ERROR: {message}", file=sys.stderr)
    raise WorktreeError(message)


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
    local = subprocess.run(
        ["git", "-C", str(repo_dir), "show-ref", "--verify", "--quiet", f"refs/heads/{base}"],
        capture_output=True,
        check=False,
    )
    if local.returncode == 0:
        return base
    remote = subprocess.run(
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
    if remote.returncode == 0:
        return f"origin/{base}"
    raise WorktreeError(f"base branch not found: {base}")


def _is_transient_fetch_error(stderr: str) -> bool:
    return any(marker in stderr for marker in _TRANSIENT_FETCH_MARKERS)


def _classify_clone_result(proc: subprocess.CompletedProcess[str]) -> str:
    if proc.returncode == 0:
        return "ok"
    return "fail"


def _lock_wait_seconds() -> int:
    raw = os.environ.get("P0_FETCH_LOCK_WAIT", "30")
    try:
        return max(0, int(raw))
    except ValueError:
        return 30


def fetch_base_with_retry(repo_dir: Path, base: str) -> bool:
    """Fetch origin base with mkdir lock + transient ref-lock retries (max 3).

    Mirrors ``fetch_base_with_retry`` in workflows/issuesmith/p0-worktree.md (#2518).
    """
    lock_dir = repo_dir / ".git" / "issuesmith-fetch.lock"
    lock_wait = _lock_wait_seconds()

    for attempt in range(1, 4):
        waited = 0
        while True:
            try:
                lock_dir.mkdir()
                break
            except FileExistsError:
                waited += 1
                if waited >= lock_wait:
                    break
                _sleep(1)

        proc = subprocess.run(
            [
                "git",
                "-C",
                str(repo_dir),
                "fetch",
                "origin",
                f"refs/heads/{base}:refs/remotes/origin/{base}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            lock_dir.rmdir()
        except OSError:
            pass

        if proc.returncode == 0:
            return True

        stderr = proc.stderr or ""
        if _is_transient_fetch_error(stderr):
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
        return False

    return False


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


def _transition(issue_number: int, target: str) -> None:
    workflow = _load_workflow_config(_workflow_path())
    transition(
        issue_number,
        target,
        workflow.transitions or {},
        workflow.reset_label,
    )


def _require_yaml_metadata(body: str) -> None:
    first = body.split("\n", 1)[0] if body else ""
    if not first.startswith("```yaml"):
        _fail(
            "issue body must start with a yaml metadata block "
            "(base_branch / allow_paths / target_repo); see #2532"
        )


def _validate_target_worktree_path(path: str) -> None:
    if path.startswith("/"):
        _fail(f"target_worktree_path must be relative, got: {path}")
    parts = path.split("/")
    if ".." in parts:
        _fail(f"target_worktree_path must not contain parent traversal: {path}")


def _handle_milestone(client: ForgePort, issue_number: int) -> StepResult:
    try:
        _transition(issue_number, "issuesmith:draft-done")
    except Exception as exc:  # noqa: BLE001 — bash uses `|| true`
        print(f"P0 milestone transition failed: {exc}", file=sys.stderr)
    try:
        client.issue_comment(issue_number, _MILESTONE_COMMENT)
    except Exception as exc:  # noqa: BLE001
        print(f"P0 milestone comment failed: {exc}", file=sys.stderr)
    print("WORKTREE_ERROR: scope:milestone issue cannot enter implementation", file=sys.stderr)
    return StepResult(exit_code=1, pipeline_status="WORKTREE_FAILED")


def _prepare_cross_repo(ctx: StepContext, repo_root: Path) -> None:
    target_rel = ctx.target_worktree_path.strip()
    _validate_target_worktree_path(target_rel)

    target_clone = repo_root / ctx.target_clone_path.strip()
    target_worktree = repo_root / target_rel
    base = ctx.base_branch.strip()
    branch = ctx.branch.strip()
    target_repo = ctx.target_repo.strip()

    if not target_clone.exists():
        target_clone.parent.mkdir(parents=True, exist_ok=True)
        clone = subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                base,
                f"https://github.com/{target_repo}.git",
                str(target_clone),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if _classify_clone_result(clone) != "ok":
            if clone.stderr:
                print(clone.stderr, file=sys.stderr, end="")
            _fail(f"failed to clone {target_repo}")

    git_dir = subprocess.run(
        ["git", "-C", str(target_clone), "rev-parse", "--git-dir"],
        capture_output=True,
        check=False,
    )
    if git_dir.returncode != 0:
        _fail(f"target clone is not a git repository: {target_clone}")

    if not fetch_base_with_retry(target_clone, base):
        _fail(f"failed to fetch {target_repo}:{base}")

    remote_ok = subprocess.run(
        [
            "git",
            "-C",
            str(target_clone),
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

    prepare_worktree(target_clone, target_worktree, branch, f"origin/{base}")

    try:
        normalized = Path(os.path.realpath(target_worktree))
    except OSError:
        _fail(f"target worktree path does not exist after prepare: {target_worktree}")
    repo_real = Path(os.path.realpath(repo_root))
    try:
        normalized.relative_to(repo_real)
    except ValueError:
        _fail(f"normalized target_worktree_path escaped REPO_ROOT: {normalized}")

    if ctx.has_diary_changes == "true":
        diary_path = Path(ctx.diary_worktree_path.strip())
        try:
            diary_base = resolve_base_ref(repo_root, base)
        except WorktreeError:
            _fail(f"base branch not found in diary repository: {base}")
        prepare_worktree(repo_root, diary_path, f"{branch}-diary", diary_base)


def _prepare_local(ctx: StepContext, repo_root: Path) -> None:
    base = ctx.base_branch.strip()
    try:
        local_base = resolve_base_ref(repo_root, base)
    except WorktreeError:
        _fail(f"base branch not found: {base}")
    prepare_worktree(repo_root, Path(ctx.worktree_path.strip()), ctx.branch.strip(), local_base)


def _assert_jobs_clean(worktree_dir: Path) -> None:
    """Fail if ``jobs/`` under the worktree is dirty (daemon auto-commit risk, #3178)."""
    proc = subprocess.run(
        ["git", "-C", str(worktree_dir), "status", "--porcelain", "--", "jobs/"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()
        msg = f"failed to check jobs/ dirty status in {worktree_dir}"
        if detail:
            msg = f"{msg}: {detail}"
        _fail(msg)
    if (proc.stdout or "").strip():
        msg = (
            "P0: worktree 作成後に jobs/ 配下が dirty です。"
            "daemon の自動コミットが混入している可能性があります。"
        )
        print(msg, file=sys.stderr)
        _fail(msg)


def run(ctx: StepContext, step: StepConfig | None = None) -> StepResult:
    """Execute the P0 worktree provisioning step."""
    del step  # reserved for dispatch StepConfig parity with other steps
    try:
        validate_branch(ctx.branch.strip())
        validate_branch(ctx.base_branch.strip())

        issue_number = int(ctx.issue_number)
        client = _github_client()

        try:
            issue = client.issue_get(issue_number, fields=["labels", "body"])
        except Exception as exc:  # noqa: BLE001
            _fail(f"failed to fetch issue labels: {exc}")

        labels = [lb["name"] for lb in issue.get("labels", []) if isinstance(lb, dict)]
        if "scope:milestone" in labels:
            return _handle_milestone(client, issue_number)

        body = issue.get("body") or ""
        _require_yaml_metadata(body)

        repo_root = _repo_root()
        if not repo_root.is_dir():
            _fail("issuesmith runner is not inside the nexus repository")

        if ctx.is_cross_repo == "true":
            _prepare_cross_repo(ctx, repo_root)
            worktree_dir = repo_root / ctx.target_worktree_path.strip()
        else:
            _prepare_local(ctx, repo_root)
            worktree_dir = Path(ctx.worktree_path.strip())

        _assert_jobs_clean(worktree_dir)

        print(f"WORKTREE_PATH: {ctx.worktree_path}")
        print(f"WORKTREE_BRANCH: {ctx.branch}")
        return StepResult(exit_code=0, pipeline_status="WORKTREE_READY")
    except WorktreeError:
        return StepResult(exit_code=1, pipeline_status="WORKTREE_FAILED")
