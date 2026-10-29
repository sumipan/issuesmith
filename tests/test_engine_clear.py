"""engine clear CLI — QuotaGate source removal."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from issuesmith import engine as eng_mod

_NOW = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)


def _write_sources(path: Path, engine: str, sources: list[dict]) -> None:
    payload = {
        "schema_version": 1,
        "deferred_tasks": {},
        "draining_engines": {},
        "running_tasks": {},
        "engines": {
            engine: {
                "status": "paused",
                "observed_at": _NOW.isoformat(),
                "reason": "test",
                "resume_at": None,
                "sources": sources,
            }
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_engine_clear_kind_failure_only(tmp_path, monkeypatch):
    quota = tmp_path / "quota.json"
    _write_sources(
        quota,
        "codex",
        [
            {
                "kind": "budget",
                "reason": "budget",
                "until": None,
                "set_by": "t",
                "observed_at": _NOW.isoformat(),
                "available": False,
            },
            {
                "kind": "failure",
                "reason": "fail",
                "until": None,
                "set_by": "t",
                "observed_at": _NOW.isoformat(),
                "available": False,
            },
            {
                "kind": "manual",
                "reason": "",
                "until": None,
                "set_by": "t",
                "observed_at": _NOW.isoformat(),
                "available": True,
            },
        ],
    )
    monkeypatch.setattr(eng_mod, "QUOTA_STATE_PATH", quota)
    code = eng_mod.main(["clear", "codex", "--kind", "failure"])
    assert code == 0
    data = json.loads(quota.read_text(encoding="utf-8"))
    kinds = {s["kind"] for s in data["engines"]["codex"]["sources"]}
    assert kinds == {"budget", "manual"}


def test_engine_clear_all_sources(tmp_path, monkeypatch, capsys):
    quota = tmp_path / "quota.json"
    src = {
        "kind": "budget",
        "reason": "budget",
        "until": None,
        "set_by": "t",
        "observed_at": _NOW.isoformat(),
        "available": False,
    }
    payload = {
        "schema_version": 1,
        "deferred_tasks": {},
        "draining_engines": {},
        "running_tasks": {},
        "engines": {
            "codex": {
                "status": "paused",
                "observed_at": _NOW.isoformat(),
                "reason": "test",
                "resume_at": None,
                "sources": [src],
            },
            "claude": {
                "status": "paused",
                "observed_at": _NOW.isoformat(),
                "reason": "test",
                "resume_at": None,
                "sources": [dict(src)],
            },
        },
    }
    quota.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(eng_mod, "QUOTA_STATE_PATH", quota)
    code = eng_mod.main(["clear", "codex"])
    assert code == 0
    data = json.loads(quota.read_text(encoding="utf-8"))
    assert "codex" not in data["engines"]
    assert "claude" in data["engines"]
    out = capsys.readouterr().out
    assert "engine=codex" in out
    assert "cleared=all" in out
