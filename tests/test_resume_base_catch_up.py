"""Resume base catch-up and restore in_flight after andon answer."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml
from ghdag.forge import get_forge

from issuesmith import resume as resume_mod
from issuesmith.andon import Andon, raise_andon
from issuesmith.config import reset_config_cache
from issuesmith.queue_store import QueueStore, in_flight_by_engine


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_seed(seed: Path) -> None:
    seed.mkdir(parents=True)
    _git(seed, "init", "-q", "-b", "main")
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "t")
    (seed / "README.md").write_text("base\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-q", "-m", "init")


def _bare_clone_setup(tmp_path: Path) -> tuple[Path, Path]:
    seed = tmp_path / "seed"
    _init_seed(seed)
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", "-q", str(seed), str(bare)], check=True)
    worktree = tmp_path / "wt"
    subprocess.run(["git", "clone", "-q", str(bare), str(worktree)], check=True)
    _git(worktree, "config", "user.email", "t@example.com")
    _git(worktree, "config", "user.name", "t")
    _git(worktree, "checkout", "-q", "-b", "feat/issue-10")
    (worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
    _git(worktree, "add", "feature.txt")
    _git(worktree, "commit", "-q", "-m", "feature")
    _git(worktree, "push", "-u", "origin", "feat/issue-10")
    return bare, worktree


def _resume_cfg(tmp_path: Path, monkeypatch, *, exec_path: Path, repo: str = "owner/repo") -> None:
    cfg = {
        "repo": repo,
        "supported_repos": [repo],
        "phases": [
            {
                "name": "develop",
                "role": "implementation",
                "entry_step": "p1",
                "handler": "impl",
            },
        ],
        "paths": {
            "exec_jsonl": str(exec_path),
            "worktrees_dir": str(tmp_path / "worktrees"),
            "queue": str(tmp_path / "q.jsonl"),
            "queue_state": str(tmp_path / "state.json"),
            "queue_lock": str(tmp_path / "lock"),
        },
    }
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    monkeypatch.setenv("ISSUESMITH_QUEUE_DIR", str(tmp_path))
    monkeypatch.setenv("GHDAG_FORGE", "local")
    monkeypatch.setenv("GHDAG_FORGE_ROOT", str(tmp_path / "forge"))
    reset_config_cache()


def _exec_record(tmp_path: Path, issue: int, worktree: Path) -> Path:
    path = tmp_path / "exec.jsonl"
    rec = {
        "idempotency_key": f"issuesmith:impl:{issue}",
        "command": f'bash -lc "worktree_path={worktree} true"',
    }
    path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
    return path


def test_answer_resume_restores_in_flight(tmp_path, monkeypatch):
    """refs #7464 AC-4."""
    from ghdag.status import StepStatus

    from issuesmith.andon import answer

    exec_path = _exec_record(tmp_path, 0, tmp_path / "missing")
    _resume_cfg(tmp_path, monkeypatch, exec_path=exec_path)
    client = get_forge()
    issue_num = client.issue_create(
        "impl", "```yaml\ntarget_repo: owner/repo\nallow_paths: [\"src/a.py\"]\n```\n"
    )
    store = QueueStore()
    store.add_in_flight(issue_num, "claude", role="implementation", phase="develop")
    andon = Andon(
        id=f"wf:{issue_num}:p1:0",
        kind="blocked",
        issue=issue_num,
        step="p1",
        summary="s",
        evidence="",
        options=["resume"],
        default="resume",
        mention="",
    )
    with patch("issuesmith.andon._write_metrics"):
        raise_andon(client, andon, metrics_path=tmp_path / "m.jsonl")
    assert QueueStore().snapshot().in_flight == []

    steps = [StepStatus("u-p1", "p1", [], "failed", None, None, None)]
    recover = MagicMock(return_value=0)
    monkeypatch.setattr(resume_mod, "get_forge", lambda repo=None: client)
    monkeypatch.setattr(resume_mod, "_generation_keys_available", lambda: True)
    monkeypatch.setattr(resume_mod, "_load_step_statuses", lambda *a, **k: steps)
    monkeypatch.setattr(resume_mod, "_run_ghdag_recover", recover)
    with patch("issuesmith.andon._write_metrics"):
        answer(client, andon.id, "resume", answered_by="human")

    recover.assert_called_once()
    snap = QueueStore().snapshot()
    assert [e.get("issue") for e in snap.in_flight] == [issue_num]
    assert in_flight_by_engine(snap.in_flight).get("claude", 0) == 1


def test_resume_rebases_worktree_onto_new_base(tmp_path, monkeypatch):
    """refs #7464 AC-5a."""
    bare, worktree = _bare_clone_setup(tmp_path)
    issue_num = 10
    exec_path = _exec_record(tmp_path, issue_num, worktree)
    _resume_cfg(tmp_path, monkeypatch, exec_path=exec_path)
    seed = tmp_path / "seed"
    (seed / "ahead.txt").write_text("ahead\n", encoding="utf-8")
    _git(seed, "add", "ahead.txt")
    _git(seed, "commit", "-q", "-m", "advance main")
    subprocess.run(["git", "-C", str(bare), "fetch", str(seed), "main:main"], check=True)
    _git(worktree, "fetch", "origin", "main")

    client = MagicMock()
    client.issue_get.return_value = {
        "body": "```yaml\nbase_branch: main\n```\n",
        "number": issue_num,
    }

    recover = MagicMock(return_value=0)
    with (
        patch("issuesmith.resume.get_forge", return_value=client),
        patch("issuesmith.resume.QueueStore") as store_cls,
        patch("issuesmith.resume._generation_keys_available", return_value=True),
        patch("issuesmith.resume._resume_from_step", recover),
    ):
        store_cls.return_value = QueueStore(
            queue_path=tmp_path / "q2.jsonl",
            state_path=tmp_path / "s2.json",
            lock_path=tmp_path / "l2",
        )
        assert resume_mod.resume(issue_num, from_step="p1") == 0
    origin_main = _git(worktree, "rev-parse", "origin/main")
    merged = subprocess.run(
        ["git", "-C", str(worktree), "merge-base", "--is-ancestor", origin_main, "HEAD"],
        check=False,
    )
    assert merged.returncode == 0
    recover.assert_called_once()


def test_resume_conflict_raises_andon_without_running_step(tmp_path, monkeypatch):
    """refs #7464 AC-5b."""
    bare, worktree = _bare_clone_setup(tmp_path)
    forge_client = get_forge()
    issue_num = forge_client.issue_create("resume conflict", "body")
    exec_path = _exec_record(tmp_path, issue_num, worktree)
    _resume_cfg(tmp_path, monkeypatch, exec_path=exec_path)
    (worktree / "README.md").write_text("conflict on feature\n", encoding="utf-8")
    _git(worktree, "add", "README.md")
    _git(worktree, "commit", "-q", "-m", "touch same file")
    seed = tmp_path / "seed"
    _git(seed, "checkout", "main")
    (seed / "README.md").write_text("conflict on main\n", encoding="utf-8")
    _git(seed, "add", "README.md")
    _git(seed, "commit", "-q", "-m", "advance main with conflict")
    subprocess.run(["git", "-C", str(bare), "fetch", str(seed), "main:main"], check=True)
    _git(worktree, "fetch", "origin", "main")

    real_issue_get = forge_client.issue_get

    def _issue_get(number, fields=None):
        data = dict(real_issue_get(number, fields=fields or ["body", "number"]))
        data["body"] = "```yaml\nbase_branch: main\n```\n"
        return data

    client = forge_client
    client.issue_get = _issue_get  # type: ignore[method-assign]
    monkeypatch.setattr("issuesmith.resume.get_forge", lambda repo=None: client)
    monkeypatch.setattr(
        "issuesmith.andon.get_forge",
        lambda repo=None: client,
        raising=False,
    )

    recover = MagicMock(return_value=0)
    with (
        patch("issuesmith.resume.QueueStore") as store_cls,
        patch("issuesmith.resume._generation_keys_available", return_value=True),
        patch("issuesmith.resume._resume_from_step", recover),
        patch("issuesmith.andon.QueueStore"),
        patch("issuesmith.andon._write_metrics"),
        patch("issuesmith.andon.project_issue"),
    ):
        store_cls.return_value = QueueStore(
            queue_path=tmp_path / "q3.jsonl",
            state_path=tmp_path / "s3.json",
            lock_path=tmp_path / "l3",
        )
        rc = resume_mod.resume(issue_num, from_step="p1")
    assert rc == 1
    recover.assert_not_called()
    bodies = " ".join(
        str(c.get("body", "")) for c in forge_client.get_issue_comments(issue_num)
    )
    assert "p0.base_rebase_conflict" in bodies
    assert "README.md" in bodies
    assert subprocess.run(
        ["git", "-C", str(worktree), "rev-parse", "-q", "--verify", "REBASE_HEAD"],
        check=False,
    ).returncode != 0


def _advance_main_on_bare(tmp_path: Path, bare: Path, worktree: Path) -> None:
    seed = tmp_path / "seed"
    (seed / "ahead.txt").write_text("ahead\n", encoding="utf-8")
    _git(seed, "add", "ahead.txt")
    _git(seed, "commit", "-q", "-m", "advance main")
    subprocess.run(["git", "-C", str(bare), "fetch", str(seed), "main:main"], check=True)
    _git(worktree, "fetch", "origin", "main")


def _resume_env(
    tmp_path: Path,
    monkeypatch,
    issue_num: int,
    worktree: Path,
    *,
    queue_suffix: str = "",
) -> tuple[Path, Path, Path]:
    exec_path = _exec_record(tmp_path, issue_num, worktree)
    _resume_cfg(tmp_path, monkeypatch, exec_path=exec_path)
    suffix = queue_suffix or "q"
    return (
        tmp_path / f"{suffix}.jsonl",
        tmp_path / f"{suffix}s.json",
        tmp_path / f"{suffix}l",
    )


def test_resume_pushes_rebased_branch_when_remote_was_in_sync(
    tmp_path, monkeypatch, capsys
):
    """refs #7756 AC-1."""
    from issuesmith.lines.develop.review import _check_remote_branch_matches_head

    bare, worktree = _bare_clone_setup(tmp_path)
    issue_num = 10
    branch = _git(worktree, "branch", "--show-current")
    _advance_main_on_bare(tmp_path, bare, worktree)
    client = MagicMock()
    client.issue_get.return_value = {
        "body": "```yaml\nbase_branch: main\n```\n",
        "number": issue_num,
    }
    q_path, s_path, l_path = _resume_env(tmp_path, monkeypatch, issue_num, worktree)
    recover = MagicMock(return_value=0)
    with (
        patch("issuesmith.resume.get_forge", return_value=client),
        patch("issuesmith.resume.QueueStore") as store_cls,
        patch("issuesmith.resume._generation_keys_available", return_value=True),
        patch("issuesmith.resume._resume_from_step", recover),
    ):
        store_cls.return_value = QueueStore(
            queue_path=q_path,
            state_path=s_path,
            lock_path=l_path,
        )
        assert resume_mod.resume(issue_num, from_step="p1") == 0
    head = _git(worktree, "rev-parse", "HEAD")
    remote = _git(worktree, "ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    remote_sha = remote.split("\t", 1)[0]
    assert head == remote_sha
    assert _check_remote_branch_matches_head(worktree, branch) is None
    assert "RESUME_PUSH:" in capsys.readouterr().err
    recover.assert_called_once()


def _unpublished_branch_setup(tmp_path: Path) -> tuple[Path, Path, str]:
    bare, worktree = _bare_clone_setup(tmp_path)
    branch = "feat/local-only"
    _git(worktree, "checkout", "-q", "-b", branch)
    (worktree / "local.txt").write_text("local\n", encoding="utf-8")
    _git(worktree, "add", "local.txt")
    _git(worktree, "commit", "-q", "-m", "local only")
    return bare, worktree, branch


def test_resume_does_not_create_remote_branch_when_unpublished(tmp_path, monkeypatch):
    """refs #7756 AC-2."""
    bare, worktree, branch = _unpublished_branch_setup(tmp_path)
    issue_num = 11
    _advance_main_on_bare(tmp_path, bare, worktree)
    client = MagicMock()
    client.issue_get.return_value = {
        "body": "```yaml\nbase_branch: main\n```\n",
        "number": issue_num,
    }
    q_path, s_path, l_path = _resume_env(
        tmp_path, monkeypatch, issue_num, worktree, queue_suffix="q2"
    )
    recover = MagicMock(return_value=0)
    with (
        patch("issuesmith.resume.get_forge", return_value=client),
        patch("issuesmith.resume.QueueStore") as store_cls,
        patch("issuesmith.resume._generation_keys_available", return_value=True),
        patch("issuesmith.resume._resume_from_step", recover),
    ):
        store_cls.return_value = QueueStore(
            queue_path=q_path,
            state_path=s_path,
            lock_path=l_path,
        )
        assert resume_mod.resume(issue_num, from_step="p1") == 0
    remote = _git(worktree, "ls-remote", "--heads", "origin", f"refs/heads/{branch}")
    assert remote == ""
    recover.assert_called_once()


def test_resume_does_not_push_when_remote_already_diverged(tmp_path, monkeypatch):
    """refs #7756 AC-3."""
    bare, worktree = _bare_clone_setup(tmp_path)
    issue_num = 12
    branch = _git(worktree, "branch", "--show-current")
    remote_before = _git(worktree, "rev-parse", f"refs/remotes/origin/{branch}")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", str(bare), str(other)], check=True)
    _git(other, "config", "user.email", "t@example.com")
    _git(other, "config", "user.name", "t")
    _git(other, "checkout", "-q", branch)
    (other / "diverge.txt").write_text("x\n", encoding="utf-8")
    _git(other, "add", "diverge.txt")
    _git(other, "commit", "-q", "-m", "diverge")
    _git(other, "push", "origin", branch)
    remote_after_other = _git(other, "rev-parse", "HEAD")
    assert remote_after_other != remote_before
    _advance_main_on_bare(tmp_path, bare, worktree)
    client = MagicMock()
    client.issue_get.return_value = {
        "body": "```yaml\nbase_branch: main\n```\n",
        "number": issue_num,
    }
    q_path, s_path, l_path = _resume_env(
        tmp_path, monkeypatch, issue_num, worktree, queue_suffix="q3"
    )
    recover = MagicMock(return_value=0)
    with (
        patch("issuesmith.resume.get_forge", return_value=client),
        patch("issuesmith.resume.QueueStore") as store_cls,
        patch("issuesmith.resume._generation_keys_available", return_value=True),
        patch("issuesmith.resume._resume_from_step", recover),
    ):
        store_cls.return_value = QueueStore(
            queue_path=q_path,
            state_path=s_path,
            lock_path=l_path,
        )
        assert resume_mod.resume(issue_num, from_step="p1") == 0
    remote_now = _git(
        worktree, "ls-remote", "--heads", "origin", f"refs/heads/{branch}"
    ).split("\t", 1)[0]
    assert remote_now == remote_after_other
    recover.assert_called_once()


def test_resume_continues_when_remote_branch_check_fails(
    tmp_path, monkeypatch, capsys
):
    """refs #7756 AC-4."""
    bare, worktree = _bare_clone_setup(tmp_path)
    issue_num = 13
    _advance_main_on_bare(tmp_path, bare, worktree)
    client = MagicMock()
    client.issue_get.return_value = {
        "body": "```yaml\nbase_branch: main\n```\n",
        "number": issue_num,
    }
    exec_path = _exec_record(tmp_path, issue_num, worktree)
    _resume_cfg(tmp_path, monkeypatch, exec_path=exec_path)
    recover = MagicMock(return_value=0)

    real_git = resume_mod._git_in_worktree

    def _git_ls_remote_fail(worktree, *args):
        if "ls-remote" in args:
            return subprocess.CompletedProcess(args, 1, "", "ls-remote failed")
        return real_git(worktree, *args)

    with (
        patch("issuesmith.resume.get_forge", return_value=client),
        patch("issuesmith.resume.QueueStore") as store_cls,
        patch("issuesmith.resume._generation_keys_available", return_value=True),
        patch("issuesmith.resume._resume_from_step", recover),
        patch("issuesmith.resume._git_in_worktree", side_effect=_git_ls_remote_fail),
    ):
        store_cls.return_value = QueueStore(
            queue_path=tmp_path / "q4.jsonl",
            state_path=tmp_path / "s4.json",
            lock_path=tmp_path / "l4",
        )
        assert resume_mod.resume(issue_num, from_step="p1") == 0
    err = capsys.readouterr().err
    assert "warning: resume remote branch check skipped:" in err
    recover.assert_called_once()


def test_resume_continues_when_remote_branch_check_raises(
    tmp_path, monkeypatch, capsys
):
    """Git execution errors disable only the optional push decision."""
    bare, worktree = _bare_clone_setup(tmp_path)
    issue_num = 15
    _advance_main_on_bare(tmp_path, bare, worktree)
    client = MagicMock()
    client.issue_get.return_value = {
        "body": "```yaml\nbase_branch: main\n```\n",
        "number": issue_num,
    }
    exec_path = _exec_record(tmp_path, issue_num, worktree)
    _resume_cfg(tmp_path, monkeypatch, exec_path=exec_path)
    recover = MagicMock(return_value=0)
    real_git = resume_mod._git_in_worktree

    def _git_ls_remote_raise(worktree, *args):
        if "ls-remote" in args:
            raise OSError("git unavailable")
        return real_git(worktree, *args)

    with (
        patch("issuesmith.resume.get_forge", return_value=client),
        patch("issuesmith.resume.QueueStore") as store_cls,
        patch("issuesmith.resume._generation_keys_available", return_value=True),
        patch("issuesmith.resume._resume_from_step", recover),
        patch("issuesmith.resume._git_in_worktree", side_effect=_git_ls_remote_raise),
    ):
        store_cls.return_value = QueueStore(
            queue_path=tmp_path / "q6.jsonl",
            state_path=tmp_path / "s6.json",
            lock_path=tmp_path / "l6",
        )
        assert resume_mod.resume(issue_num, from_step="p1") == 0
    assert (
        "warning: resume remote branch check skipped: git unavailable"
        in capsys.readouterr().err
    )
    recover.assert_called_once()


def test_resume_stops_when_rebased_branch_push_fails(tmp_path, monkeypatch, capsys):
    """refs #7756 AC-5."""
    bare, worktree = _bare_clone_setup(tmp_path)
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    issue_num = 14
    _advance_main_on_bare(tmp_path, bare, worktree)
    client = MagicMock()
    client.issue_get.return_value = {
        "body": "```yaml\nbase_branch: main\n```\n",
        "number": issue_num,
    }
    q_path, s_path, l_path = _resume_env(
        tmp_path, monkeypatch, issue_num, worktree, queue_suffix="q5"
    )
    recover = MagicMock(return_value=0)
    with (
        patch("issuesmith.resume.get_forge", return_value=client),
        patch("issuesmith.resume.QueueStore") as store_cls,
        patch("issuesmith.resume._generation_keys_available", return_value=True),
        patch("issuesmith.resume._resume_from_step", recover),
    ):
        store_cls.return_value = QueueStore(
            queue_path=q_path,
            state_path=s_path,
            lock_path=l_path,
        )
        assert resume_mod.resume(issue_num, from_step="p1") == 1
    err = capsys.readouterr().err
    assert "error: resume push failed:" in err
    recover.assert_not_called()
