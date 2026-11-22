"""Closing-phase resolution for nexus vs single-develop layouts (#7811)."""

from __future__ import annotations

import yaml

from issuesmith.config import get_config, reset_config_cache
from issuesmith.queue import _closing_phase_name
from issuesmith.queue_triage import get_terminal_without_merge
from tests.conftest import NEXUS_DECLARATION_PHASES

_SINGLE_DEVELOP_PHASES = [
    {
        "name": "draft",
        "role": "design",
        "entry_step": "b1",
        "handler": "brushup",
        "steps": ["b1"],
        "writes_files": False,
        "advance_when": ["deps_terminal"],
    },
    {
        "name": "develop",
        "role": "implementation",
        "entry_step": "prepare",
        "handler": "develop",
        "steps": ["prepare", "implement", "verify", "review", "merge"],
        "advance_when": ["deps_terminal"],
    },
]


def _write_cfg(tmp_path, monkeypatch, payload: dict) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_closing_phase_nexus_is_merge(tmp_path, monkeypatch) -> None:
    """#7811 AC2."""
    _write_cfg(
        tmp_path,
        monkeypatch,
        {
            "repo": "sumipan/nexus",
            "phases": NEXUS_DECLARATION_PHASES,
            "terminal_labels": ["issuesmith:merge-done", "bump:done"],
        },
    )
    closing = get_config().closing_phase()
    assert closing is not None
    assert closing.name == "merge"


def test_closing_phase_single_develop_is_develop(tmp_path, monkeypatch) -> None:
    """#7811 AC3."""
    _write_cfg(
        tmp_path,
        monkeypatch,
        {
            "repo": "sumipan/issuesmith",
            "phases": _SINGLE_DEVELOP_PHASES,
            "terminal_labels": [],
        },
    )
    closing = get_config().closing_phase()
    assert closing is not None
    assert closing.name == "develop"


def test_closing_phase_name_single_develop(tmp_path, monkeypatch) -> None:
    """#7811 AC4."""
    _write_cfg(
        tmp_path,
        monkeypatch,
        {
            "repo": "sumipan/issuesmith",
            "phases": _SINGLE_DEVELOP_PHASES,
            "terminal_labels": [],
        },
    )
    assert _closing_phase_name() == "develop"


def test_terminal_without_merge_excludes_closing_done(tmp_path, monkeypatch) -> None:
    """#7811 AC5."""
    _write_cfg(
        tmp_path,
        monkeypatch,
        {
            "repo": "sumipan/issuesmith",
            "phases": _SINGLE_DEVELOP_PHASES,
            "terminal_labels": ["issuesmith:develop-done", "bump:done"],
        },
    )
    terminal = get_terminal_without_merge()
    assert "issuesmith:develop-done" not in terminal
    assert "bump:done" in terminal


def test_terminal_without_merge_excludes_cross_namespace_closing_done(
    tmp_path, monkeypatch
) -> None:
    """#7811: ``<other-ns>:<closing>-done`` is also treated as closed-with-merge."""
    _write_cfg(
        tmp_path,
        monkeypatch,
        {
            "repo": "sumipan/issuesmith",
            "phases": _SINGLE_DEVELOP_PHASES,
            "terminal_labels": ["issuesmith:develop-done", "wsl:develop-done", "bump:done"],
        },
    )
    terminal = get_terminal_without_merge()
    assert "wsl:develop-done" not in terminal
    assert "issuesmith:develop-done" not in terminal
    assert "bump:done" in terminal
