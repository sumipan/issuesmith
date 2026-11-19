"""Refs sumipan/issuesmith#7402 — child env: ISSUESMITH_CONFIG and worktree PATH."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml
from ghdag.llm import LLMResult
from ghdag.quota import EngineQuotaState, QuotaSnapshot

from issuesmith.config import load_config, reset_config_cache
from issuesmith.engine import RoleSelection, _execute


def _available():
    return EngineQuotaState(
        status="available",
        observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        resume_at=None,
        reason=None,
    )


@pytest.fixture()
def exe_mocks(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "issuesmith.engine.resolve",
        lambda role, tier=None: RoleSelection(engine="claude", model="m"),
    )
    monkeypatch.setattr("issuesmith.engine._record_task_metrics", lambda **kwargs: None)
    monkeypatch.setattr("issuesmith.engine._resolve_timeout_sec", lambda role: 600.0)
    qg = MagicMock()
    monkeypatch.setattr("issuesmith.engine.QuotaGate", lambda state_path: qg)
    qg.snapshot.return_value = QuotaSnapshot(
        engines={"claude": _available(), "codex": _available()},
        draining_engines={},
        running_tasks={},
        limits={},
    )


def test_child_inherits_host_config_not_worktree_local(
    exe_mocks, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Refs #7402 AC7 — ISSUESMITH_CONFIG points at the host config."""
    host_root = tmp_path / "host"
    host_root.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    host_cfg = host_root / "issuesmith.yaml"
    host_cfg.write_text(yaml.safe_dump({"repo": "org/r"}), encoding="utf-8")
    (worktree / "issuesmith.yaml").write_text(
        yaml.safe_dump({"repo": "wt/r"}), encoding="utf-8"
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(host_cfg))
    reset_config_cache()
    captured: list[str] = []

    def _record(*_a, **_k):
        captured.append(os.environ.get("ISSUESMITH_CONFIG", ""))
        assert load_config().root.resolve() == host_root.resolve()
        return LLMResult(
            stdout="",
            stderr="",
            body="",
            usage=None,
            returncode=0,
            failure_class=None,
            engine_used="claude",
            model_used="m",
            attempts=1,
            quota_reported=False,
            additional_tags={},
        )

    monkeypatch.setattr("issuesmith.engine.call", MagicMock(side_effect=_record))
    _execute("implementation", "p", cwd=str(worktree))
    assert captured
    assert Path(captured[0]).resolve() == host_cfg.resolve()
    reset_config_cache()
    os.environ.pop("ISSUESMITH_CONFIG", None)


def test_child_path_prefixed_with_python_bin(
    exe_mocks, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Refs #7402 AC10 — PATH starts with python_bin_command output."""
    host_root = tmp_path / "host"
    host_root.mkdir()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    venv_bin = tmp_path / "venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\necho py\n", encoding="utf-8")
    (venv_bin / "python").chmod(0o755)
    script = host_root / "bin-of"
    script.write_text(
        '#!/bin/sh\nlast="$1"; shift; while [ $# -gt 0 ]; do last="$1"; shift; done; '
        f'printf "%s\\n" "{venv_bin}"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    cfg = host_root / "issuesmith.yaml"
    cfg.write_text(
        yaml.safe_dump({
            "repo": "org/r",
            "tests": {"python_bin_command": [str(script)]},
        }),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg))
    reset_config_cache()
    captured: list[str] = []

    def _record(*_a, **_k):
        captured.append(os.environ.get("PATH", ""))
        return LLMResult(
            stdout="",
            stderr="",
            body="",
            usage=None,
            returncode=0,
            failure_class=None,
            engine_used="claude",
            model_used="m",
            attempts=1,
            quota_reported=False,
            additional_tags={},
        )

    monkeypatch.setattr("issuesmith.engine.call", MagicMock(side_effect=_record))
    _execute("implementation", "p", cwd=str(worktree))
    assert captured[0].split(os.pathsep)[0] == str(venv_bin.resolve())
    reset_config_cache()


def test_child_path_unchanged_without_python_bin_command(
    exe_mocks, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Refs #7402 AC11 — default PATH when python_bin_command is unset."""
    worktree = tmp_path / "wt"
    worktree.mkdir()
    prior = os.environ.get("PATH", "")
    captured: list[str] = []

    def _record(*_a, **_k):
        captured.append(os.environ.get("PATH", ""))
        return LLMResult(
            stdout="",
            stderr="",
            body="",
            usage=None,
            returncode=0,
            failure_class=None,
            engine_used="claude",
            model_used="m",
            attempts=1,
            quota_reported=False,
            additional_tags={},
        )

    monkeypatch.setattr("issuesmith.engine.call", MagicMock(side_effect=_record))
    _execute("implementation", "p", cwd=str(worktree))
    assert captured[0] == prior
