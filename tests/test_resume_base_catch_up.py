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
    issue_num = 10
    exec_path = _exec_record(tmp_path, issue_num, tmp_path / "missing")
    _resume_cfg(tmp_path, monkeypatch, exec_path=exec_path)
    store = QueueStore(
        queue_path=tmp_path / "q.jsonl",
        state_path=tmp_path / "state.json",
        lock_path=tmp_path / "lock",
    )
    store.add_in_flight(issue_num, "claude", role="implementation", phase="develop")
    andon = Andon(
        id="wf:10:p1:0",
        kind="blocked",
        issue=issue_num,
        step="p1",
        summary="s",
        evidence="",
        options=["resume"],
        default="resume",
        mention="",
    )
    with (
        patch("issuesmith.andon.QueueStore", return_value=store),
        patch("issuesmith.andon._write_metrics"),
        patch("issuesmith.andon.project_issue"),
    ):
        raise_andon(MagicMock(), andon, metrics_path=tmp_path / "m.jsonl")
    assert store.snapshot().in_flight == []

    def _restore(number: int, step: str) -> None:
        store.add_in_flight(number, "claude", role="implementation", phase="develop")

    def _from_step(issue: int, from_step: str, **kwargs: object) -> int:
        _restore(issue, from_step)
        return 0

    with (
        patch("issuesmith.resume.QueueStore", return_value=store),
        patch("issuesmith.resume._catch_up_base_before_resume", return_value=0),
        patch("issuesmith.resume._resume_from_step", side_effect=_from_step),
    ):
        assert resume_mod.resume(issue_num, from_step="p1") == 0
    snap = store.snapshot()
    assert in_flight_by_engine(snap.in_flight)["claude"] == 1


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
