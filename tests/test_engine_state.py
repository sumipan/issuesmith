"""Engine state file selection, config-derived defaults, and ``engine show`` output."""
from __future__ import annotations

from pathlib import Path

import pytest

from issuesmith import engine

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_PATH = REPO_ROOT / 'src'
_WSL_HEAVY_ROUTES = {'implementation': (('claude', 'claude-sonnet-4-6'), ('cursor', 'auto'), ('codex', 'gpt-5.6-sol')), 'review': (('claude', 'claude-opus-4-6'), ('cursor', 'auto'), ('codex', 'gpt-5.6-sol'))}
_WSL_ROLE_ENGINES = {role: tuple((engine_name for engine_name, _ in routes)) for role, routes in _WSL_HEAVY_ROUTES.items()}
_WSL_LIGHT_ROUTES = {role: None for role in _WSL_HEAVY_ROUTES}

def _write_legacy_state(path: Path) -> None:
    path.write_text('design:\n  engine: codex\n  model: gpt-5.6-sol\nimplementation:\n  engine: cursor\n  model: auto\n', encoding='utf-8')

def test_load_state_ignores_legacy_when_engine_state_explicit(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / 'repo'
    repo.mkdir()
    state_file = repo / '.pipeline-state' / 'wsl-engine.yml'
    legacy = repo / '.issuesmith-engine'
    _write_legacy_state(legacy)
    monkeypatch.setattr(engine, 'REPO_ROOT', repo)
    monkeypatch.setattr(engine, 'STATE_FILE', state_file)
    monkeypatch.setattr(engine, 'LEGACY_STATE_FILE', legacy)
    monkeypatch.setattr(engine, '_engine_state_path_explicit', lambda: True)
    monkeypatch.setattr(engine, 'ROLE_ENGINES', _WSL_ROLE_ENGINES)
    monkeypatch.setattr(engine, 'HEAVY_ROUTES', _WSL_HEAVY_ROUTES)
    monkeypatch.setattr(engine, 'LIGHT_ROUTES', _WSL_LIGHT_ROUTES)
    state = engine.load_state()
    assert set(state) == {'implementation', 'review'}
    assert state['implementation']['engine'] == 'claude'
    assert state['review']['engine'] == 'claude'

def test_load_state_reads_legacy_when_engine_state_not_explicit(monkeypatch, tmp_path: Path) -> None:
    repo = tmp_path / 'repo'
    repo.mkdir()
    state_file = repo / '.pipeline-state' / 'issuesmith-engine.yml'
    legacy = repo / '.issuesmith-engine'
    _write_legacy_state(legacy)
    monkeypatch.setattr(engine, 'REPO_ROOT', repo)
    monkeypatch.setattr(engine, 'STATE_FILE', state_file)
    monkeypatch.setattr(engine, 'LEGACY_STATE_FILE', legacy)
    monkeypatch.setattr(engine, '_engine_state_path_explicit', lambda: False)
    state = engine.load_state()
    assert state['design']['engine'] == 'codex'
    assert state['implementation']['engine'] == 'cursor'

def test_engine_show_two_roles_without_design(monkeypatch, capsys) -> None:
    monkeypatch.setattr(engine, 'ROLE_ENGINES', _WSL_ROLE_ENGINES)
    monkeypatch.setattr(engine, 'HEAVY_ROUTES', _WSL_HEAVY_ROUTES)
    monkeypatch.setattr(engine, 'LIGHT_ROUTES', _WSL_LIGHT_ROUTES)
    monkeypatch.setattr(engine, 'load_state', engine._default_state)
    monkeypatch.setattr(engine, 'state_source_label', lambda: 'default')
    rc = engine.main(['show'])
    assert rc == 0
    out = capsys.readouterr().out
    assert 'state_source\tdefault' in out
    assert 'design' not in out
    assert 'implementation\t' in out
    assert 'review\t' in out

def test_switch_rejects_engine_not_in_heavy_route(monkeypatch, tmp_path: Path) -> None:
    """Refs sumipan/nexus#6682 AC7 — switch rejects engines outside the heavy route."""
    state_file = tmp_path / 'engine.yml'
    monkeypatch.setattr(engine, 'STATE_FILE', state_file)
    monkeypatch.setattr(engine, 'ROLE_ENGINES', {'implementation': ('claude', 'cursor')})
    monkeypatch.setattr(engine, 'HEAVY_ROUTES', {'implementation': (('claude', 'claude-sonnet-4-6'), ('cursor', 'auto'))})
    with pytest.raises(ValueError, match='must be one of'):
        engine.switch_role('implementation', 'codex', None)
    assert not state_file.exists()
