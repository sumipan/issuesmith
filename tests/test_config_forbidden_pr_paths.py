"""Tests for forbidden_pr_paths_except config loading (#4987)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from issuesmith.config import (
    _build_forbidden_pr_paths_except,
    get_config,
    reset_config_cache,
)
from issuesmith.pr_scope import check_pr_diff_scope

_MINIMAL_PHASES = [
    {"name": "draft", "role": "design", "entry_step": "b1", "handler": "brushup"},
]
_LEDGER_EXCEPT = "skills/research-store/ledger/*/*.jsonl"


@pytest.fixture(autouse=True)
def _clear_config_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _write_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: dict) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_except_defaults_to_empty_tuple(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, {"repo": "example/app"})
    assert get_config().forbidden_pr_paths_except == ()


def test_except_loaded_from_yaml(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "forbidden_pr_paths_except": [_LEDGER_EXCEPT]},
    )
    assert get_config().forbidden_pr_paths_except == (_LEDGER_EXCEPT,)


def test_except_non_list_raises(tmp_path, monkeypatch):
    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "forbidden_pr_paths_except": _LEDGER_EXCEPT},
    )
    with pytest.raises(ValueError, match="forbidden_pr_paths_except must be a list"):
        get_config()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ()),
        ([], ()),
        (["a/*.jsonl", 1], ("a/*.jsonl", "1")),
    ],
)
def test_build_except(raw, expected):
    assert _build_forbidden_pr_paths_except(raw) == expected


@pytest.mark.parametrize("raw", ["a/*.jsonl", {"a": 1}, 3])
def test_build_except_rejects_non_list(raw):
    with pytest.raises(ValueError, match="forbidden_pr_paths_except must be a list"):
        _build_forbidden_pr_paths_except(raw)


def test_config_show_includes_except_key(tmp_path, monkeypatch, capsys):
    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "phases": _MINIMAL_PHASES},
    )
    from issuesmith.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["config", "show"])
    assert exc.value.code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["forbidden_pr_paths_except"] == []


def test_config_except_applies_to_pr_diff_scope(tmp_path, monkeypatch):
    """Config except with default forbidden_pr_paths lets the ledger through only."""
    _write_config(
        tmp_path,
        monkeypatch,
        {"repo": "example/app", "forbidden_pr_paths_except": [_LEDGER_EXCEPT]},
    )
    assert (
        check_pr_diff_scope(["skills/research-store/ledger/a/b.jsonl"], ["skills/**"])
        == []
    )
    violations = check_pr_diff_scope(["jobs/x.jsonl"], ["jobs/**"])
    assert [v.rule_id for v in violations] == ["pr_diff_scope.forbidden_path"]
    out = check_pr_diff_scope(["skills/research-store/ledger/a/b.jsonl"], ["src/**"])
    assert [v.rule_id for v in out] == ["pr_diff_scope.out_of_scope"]
