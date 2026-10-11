"""Engine state file selection, config-derived defaults, and ``engine show`` output."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

from issuesmith import engine

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / "src"

_WSL_LIKE_ENGINES = {
    "implementation": frozenset({"claude", "cursor", "codex"}),
    "review": frozenset({"claude", "cursor", "codex"}),
}
_WSL_DEFAULT_MODELS = {
    ("implementation", "claude"): "claude-sonnet-4-6",
    ("implementation", "cursor"): "auto",
    ("implementation", "codex"): "gpt-5.6-sol",
    ("review", "claude"): "claude-opus-4-6",
    ("review", "cursor"): "auto",
    ("review", "codex"): "gpt-5.6-sol",
}


def _write_legacy_state(path: Path) -> None:
    path.write_text(
        "design:\n  engine: codex\n  model: gpt-5.6-sol\n"
        "implementation:\n  engine: cursor\n  model: auto\n",
        encoding="utf-8",
    )


def test_load_state_ignores_legacy_when_engine_state_explicit(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state_file = repo / ".pipeline-state" / "wsl-engine.yml"
    legacy = repo / ".issuesmith-engine"
    _write_legacy_state(legacy)

    monkeypatch.setattr(engine, "REPO_ROOT", repo)
    monkeypatch.setattr(engine, "STATE_FILE", state_file)
    monkeypatch.setattr(engine, "LEGACY_STATE_FILE", legacy)
    monkeypatch.setattr(engine, "_engine_state_path_explicit", lambda: True)
    monkeypatch.setattr(engine, "ROLE_ENGINES", _WSL_LIKE_ENGINES)
    monkeypatch.setattr(engine, "DEFAULT_MODELS", _WSL_DEFAULT_MODELS)

    state = engine.load_state()
    assert set(state) == {"implementation", "review"}
    assert state["implementation"]["engine"] == "claude"
    assert state["review"]["engine"] == "claude"


def test_load_state_reads_legacy_when_engine_state_not_explicit(
    monkeypatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    state_file = repo / ".pipeline-state" / "issuesmith-engine.yml"
    legacy = repo / ".issuesmith-engine"
    _write_legacy_state(legacy)

    monkeypatch.setattr(engine, "REPO_ROOT", repo)
    monkeypatch.setattr(engine, "STATE_FILE", state_file)
    monkeypatch.setattr(engine, "LEGACY_STATE_FILE", legacy)
    monkeypatch.setattr(engine, "_engine_state_path_explicit", lambda: False)

    state = engine.load_state()
    assert state["design"]["engine"] == "codex"
    assert state["implementation"]["engine"] == "cursor"


def test_default_state_uses_config_roles_only(monkeypatch) -> None:
    monkeypatch.setattr(engine, "ROLE_ENGINES", _WSL_LIKE_ENGINES)
    monkeypatch.setattr(engine, "DEFAULT_MODELS", _WSL_DEFAULT_MODELS)

    state = engine._default_state()
    assert set(state) == {"implementation", "review"}
    assert state["implementation"]["model"] == "claude-sonnet-4-6"


def test_validate_state_ignores_roles_not_in_config(monkeypatch) -> None:
    monkeypatch.setattr(engine, "ROLE_ENGINES", _WSL_LIKE_ENGINES)
    monkeypatch.setattr(engine, "DEFAULT_MODELS", _WSL_DEFAULT_MODELS)

    data = {
        "design": {"engine": "claude", "model": "claude-opus-4-6"},
        "implementation": {"engine": "cursor", "model": "auto"},
        "review": {"engine": "claude", "model": "claude-opus-4-6"},
    }
    normalized = engine._validate_state(data)
    assert "design" not in normalized
    assert normalized["review"]["engine"] == "claude"


def test_engine_show_two_roles_without_design(monkeypatch, capsys) -> None:
    monkeypatch.setattr(engine, "ROLE_ENGINES", _WSL_LIKE_ENGINES)
    monkeypatch.setattr(engine, "DEFAULT_MODELS", _WSL_DEFAULT_MODELS)
    monkeypatch.setattr(engine, "load_state", engine._default_state)
    monkeypatch.setattr(engine, "state_source_label", lambda: "default")

    rc = engine.main(["show"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "state_source\tdefault" in out
    assert "design" not in out
    assert "implementation\t" in out
    assert "review\t" in out


def test_engine_show_subprocess_uses_only_explicit_config_roles(tmp_path: Path) -> None:
    cfg_path = tmp_path / "issuesmith-cli.yaml"
    legacy_path = tmp_path / ".issuesmith-engine"
    _write_legacy_state(legacy_path)
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "example/cli",
                "paths": {"engine_state": ".pipeline-state/missing-engine.yml"},
                "engines": {
                    "implementation": {
                        "allowed": ["claude"],
                        "default_model": {"claude": "claude-sonnet-4-6"},
                    },
                    "review": {
                        "allowed": ["claude"],
                        "default_model": {"claude": "claude-opus-4-6"},
                    },
                },
                "phases": [
                    {
                        "name": "develop",
                        "role": "implementation",
                        "entry_step": "p1",
                        "handler": "develop",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "PYTHONPATH": str(SRC_PATH) + (f":{os.environ.get('PYTHONPATH', '')}" if os.environ.get("PYTHONPATH") else ""),
        "ISSUESMITH_CONFIG": str(cfg_path),
    }
    proc = subprocess.run(
        [sys.executable, "-m", "issuesmith.engine", "show"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "state_source\tdefault",
        "implementation\tclaude\tclaude-sonnet-4-6",
        "review\tclaude\tclaude-opus-4-6",
    ]
