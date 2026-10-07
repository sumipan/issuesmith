"""tests/test_scope_gate_root.py — resolve_scope_root single-source-of-truth (#3487 AC-1).

CP1 (gate_rules.scope_breadth), gate-preflight, and P0 must all resolve the same
measurement root for a given Issue metadata. Before this,
scope_breadth.py carried its own private ``_resolve_root`` copy that diverged
from what P0 actually measured (#3483: CP1 passed silently, P0 tripped on the
same allow_paths at 91 files / 21076 lines).
"""

from __future__ import annotations

import os
import subprocess
import time
from unittest.mock import MagicMock

import pytest

from issuesmith import scope_gate
from issuesmith.gate_rules import scope_breadth
from issuesmith.scope_gate import resolve_scope_root


def _cfg(tmp_path, external_dir=None):
    cfg = MagicMock()
    cfg.root = tmp_path / "nexus"
    cfg.paths.external_dir = external_dir or (tmp_path / "nexus" / ".claude" / "external")
    return cfg


def test_no_target_repo_resolves_to_nexus_root(tmp_path):
    cfg = _cfg(tmp_path)
    assert resolve_scope_root({}, cfg) == cfg.root


def test_target_repo_is_nexus_resolves_to_nexus_root(tmp_path):
    cfg = _cfg(tmp_path)
    assert resolve_scope_root({"target_repo": "sumipan/nexus"}, cfg) == cfg.root


def test_cross_repo_resolves_to_external_dir_repo_when_clone_present(tmp_path):
    cfg = _cfg(tmp_path)
    (cfg.paths.external_dir / "issuesmith" / ".git").mkdir(parents=True)
    root = resolve_scope_root({"target_repo": "sumipan/issuesmith"}, cfg)
    assert root == cfg.paths.external_dir / "issuesmith"


def test_cross_repo_returns_none_when_clone_missing(tmp_path):
    """Missing clone must fail closed — never fall back to nexus root (#3483)."""
    cfg = _cfg(tmp_path)
    assert resolve_scope_root({"target_repo": "sumipan/issuesmith"}, cfg) is None


def test_malformed_target_repo_returns_none(tmp_path):
    cfg = _cfg(tmp_path)
    assert resolve_scope_root({"target_repo": "not-a-valid-repo-slug"}, cfg) is None


def test_scope_breadth_gate_imports_the_shared_function():
    """scope_breadth.py must not carry a second, divergent root resolver (#3487 R1)."""
    assert scope_breadth.resolve_scope_root is resolve_scope_root
    assert not hasattr(scope_breadth, "_resolve_root")


# --- clone freshness (#4452) -------------------------------------------------

_META = {"target_repo": "sumipan/issuesmith"}


def _git(cwd, *args):
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    ).stdout.strip()


def _commit(repo, name, content="x\n"):
    (repo / name).write_text(content)
    _git(repo, "add", name)
    _git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def origin_and_clone(tmp_path):
    """Bare origin with ``main``, a seeding work repo, and the external clone."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True
    )
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True)
    _git(seed, "checkout", "-q", "-b", "main")
    _commit(seed, "a.txt")
    _git(seed, "push", "-q", "origin", "main")
    cfg = _cfg(tmp_path)
    clone = cfg.paths.external_dir / "issuesmith"
    clone.parent.mkdir(parents=True)
    subprocess.run(["git", "clone", "-q", str(origin), str(clone)], check=True)
    return cfg, seed, clone


def _advance_origin(seed, name="b.txt", branch="main"):
    sha = _commit(seed, name)
    _git(seed, "push", "-q", "origin", branch)
    return sha


class _Recorder:
    def __init__(self, monkeypatch):
        self.calls: list[list[str]] = []
        real = subprocess.run

        def run(cmd, *a, **kw):
            self.calls.append(list(cmd))
            return real(cmd, *a, **kw)

        monkeypatch.setattr(scope_gate.subprocess, "run", run)

    def fetched(self):
        return any("fetch" in c for c in self.calls)


def test_fresh_catches_up_to_origin(origin_and_clone):
    cfg, seed, clone = origin_and_clone
    new = _advance_origin(seed)
    assert resolve_scope_root(_META, cfg) == clone
    assert _git(clone, "rev-parse", "HEAD") == new


def test_fresh_already_up_to_date_keeps_head(origin_and_clone):
    cfg, _seed, clone = origin_and_clone
    before = _git(clone, "rev-parse", "HEAD")
    assert resolve_scope_root(_META, cfg) == clone
    assert _git(clone, "rev-parse", "HEAD") == before


def test_fresh_untracked_worktrees_dir_is_clean(origin_and_clone):
    cfg, seed, clone = origin_and_clone
    (clone / "worktrees" / "issue-1").mkdir(parents=True)
    (clone / "worktrees" / "issue-1" / "f.txt").write_text("u\n")
    new = _advance_origin(seed)
    assert resolve_scope_root(_META, cfg) == clone
    assert _git(clone, "rev-parse", "HEAD") == new
    assert (clone / "worktrees" / "issue-1" / "f.txt").exists()


def test_fresh_follows_base_branch(origin_and_clone):
    cfg, seed, clone = origin_and_clone
    _git(seed, "checkout", "-q", "-b", "develop")
    _git(seed, "push", "-q", "origin", "develop")
    _git(clone, "fetch", "-q", "origin")
    _git(clone, "checkout", "-q", "-b", "develop", "origin/develop")
    os.utime(clone / ".git" / "FETCH_HEAD", (0, 0))
    new = _advance_origin(seed, "d.txt", branch="develop")
    _git(seed, "checkout", "-q", "main")
    main_new = _advance_origin(seed, "m.txt", branch="main")
    meta = {**_META, "base_branch": "develop"}
    assert resolve_scope_root(meta, cfg) == clone
    assert _git(clone, "rev-parse", "HEAD") == new
    assert _git(clone, "rev-parse", "HEAD") != main_new


def test_no_origin_clone_skips_fetch(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    clone = cfg.paths.external_dir / "issuesmith"
    clone.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(clone)], check=True)
    rec = _Recorder(monkeypatch)
    assert resolve_scope_root(_META, cfg) == clone
    assert not rec.fetched()


def test_fake_git_dir_skips_fetch(tmp_path, monkeypatch):
    cfg = _cfg(tmp_path)
    (cfg.paths.external_dir / "issuesmith" / ".git").mkdir(parents=True)
    rec = _Recorder(monkeypatch)
    assert resolve_scope_root(_META, cfg) == cfg.paths.external_dir / "issuesmith"
    assert not rec.fetched()


@pytest.mark.parametrize("meta", [{}, {"target_repo": ""}, {"target_repo": "sumipan/nexus"}])
def test_nexus_target_does_not_call_git(tmp_path, monkeypatch, meta):
    cfg = _cfg(tmp_path)
    rec = _Recorder(monkeypatch)
    assert resolve_scope_root(meta, cfg) == cfg.root
    assert rec.calls == []


def test_dirty_tracked_file_returns_none(origin_and_clone):
    cfg, seed, clone = origin_and_clone
    before = _git(clone, "rev-parse", "HEAD")
    (clone / "a.txt").write_text("local edit\n")
    _advance_origin(seed)
    assert resolve_scope_root(_META, cfg) is None
    assert (clone / "a.txt").read_text() == "local edit\n"
    assert _git(clone, "rev-parse", "HEAD") == before


def test_other_branch_checked_out_returns_none(origin_and_clone):
    cfg, seed, clone = origin_and_clone
    _git(clone, "checkout", "-q", "-b", "feature")
    before = _git(clone, "rev-parse", "feature")
    _advance_origin(seed)
    assert resolve_scope_root(_META, cfg) is None
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD") == "feature"
    assert _git(clone, "rev-parse", "feature") == before


def test_diverged_main_returns_none(origin_and_clone):
    cfg, seed, clone = origin_and_clone
    local = _commit(clone, "local.txt")
    _advance_origin(seed)
    assert resolve_scope_root(_META, cfg) is None
    assert _git(clone, "rev-parse", "HEAD") == local


def test_fetch_failure_up_to_date_returns_clone(origin_and_clone, tmp_path):
    cfg, _seed, clone = origin_and_clone
    _git(clone, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    assert resolve_scope_root(_META, cfg) == clone


def test_fetch_failure_out_of_date_returns_none(origin_and_clone, tmp_path):
    cfg, _seed, clone = origin_and_clone
    _commit(clone, "local.txt")
    _git(clone, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    assert resolve_scope_root(_META, cfg) is None


def test_timeout_returns_none_and_logs(tmp_path, monkeypatch, capsys):
    cfg = _cfg(tmp_path)
    (cfg.paths.external_dir / "issuesmith" / ".git").mkdir(parents=True)

    def boom(cmd, *a, **kw):
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(scope_gate.subprocess, "run", boom)
    assert resolve_scope_root(_META, cfg) is None
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 1
    assert err[0].startswith("scope_root: ")


def test_recent_fetch_head_skips_fetch(origin_and_clone, monkeypatch):
    cfg, _seed, clone = origin_and_clone
    fetch_head = clone / ".git" / "FETCH_HEAD"
    fetch_head.touch()
    rec = _Recorder(monkeypatch)
    assert resolve_scope_root(_META, cfg) == clone
    assert not rec.fetched()


def test_stale_fetch_head_fetches(origin_and_clone, monkeypatch):
    cfg, _seed, clone = origin_and_clone
    fetch_head = clone / ".git" / "FETCH_HEAD"
    fetch_head.touch()
    old = time.time() - 61
    os.utime(fetch_head, (old, old))
    rec = _Recorder(monkeypatch)
    assert resolve_scope_root(_META, cfg) == clone
    assert rec.fetched()
