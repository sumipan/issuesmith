"""Unit tests for AcContractGate and registry wiring."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from issuesmith.gates import GATE_REGISTRY, GateBuildContext, GateBuildError
from issuesmith.gates.ac_contract import AcContractGate


def _ac_body(yaml_block: str) -> str:
    return f'## Acceptance Criteria\n\n```yaml\n{yaml_block}\n```\n'

def _gate(tmp_path: Path) -> AcContractGate:
    return AcContractGate(tmp_path)

class TestPathsMustExist:

    def test_present_file_no_violation(self, tmp_path: Path) -> None:
        (tmp_path / 'a.py').write_text('# ok\n', encoding='utf-8')
        body = _ac_body('paths_must_exist:\n  - a.py\npaths_must_not_exist: []')
        assert _gate(tmp_path).check(body, []) == []

    def test_glob_no_match_one_violation_at_pattern(self, tmp_path: Path) -> None:
        body = _ac_body('paths_must_exist:\n  - "tests/*.py"\npaths_must_not_exist: []')
        violations = _gate(tmp_path).check(body, [])
        assert len(violations) == 1
        assert violations[0].location == 'tests/*.py'

    def test_glob_with_match_no_violation(self, tmp_path: Path) -> None:
        tests = tmp_path / 'tests'
        tests.mkdir()
        (tests / 'one.py').write_text('', encoding='utf-8')
        body = _ac_body('paths_must_exist:\n  - "tests/*.py"\npaths_must_not_exist: []')
        assert _gate(tmp_path).check(body, []) == []

class TestPathsMustNotExist:

    def test_glob_no_files_no_violation(self, tmp_path: Path) -> None:
        body = _ac_body('paths_must_exist: []\npaths_must_not_exist:\n  - "legacy/*.jsonl"')
        assert _gate(tmp_path).check(body, []) == []

class TestContractEdgeCases:

    def test_no_contract_section(self, tmp_path: Path) -> None:
        assert _gate(tmp_path).check('body text', []) == []

    def test_broken_yaml(self, tmp_path: Path) -> None:
        body = '## Acceptance Criteria\n\n```yaml\n: [\n```\n'
        assert _gate(tmp_path).check(body, []) == []

    def test_non_list_paths_must_exist(self, tmp_path: Path) -> None:
        body = _ac_body('paths_must_exist: a.py\npaths_must_not_exist: []')
        assert _gate(tmp_path).check(body, []) == []

    def test_null_paths_must_exist(self, tmp_path: Path) -> None:
        body = _ac_body('paths_must_exist: null\npaths_must_not_exist: []')
        assert _gate(tmp_path).check(body, []) == []

    def test_invalid_paths_excluded(self, tmp_path: Path) -> None:
        body = _ac_body('paths_must_exist:\n  - "/abs/a.py"\n  - "../a.py"\n  - ""\npaths_must_not_exist: []')
        assert _gate(tmp_path).check(body, []) == []

class TestRegistry:

    def test_registry_entry(self) -> None:
        entry = GATE_REGISTRY['ac_contract']
        assert entry.input_kind == 'worktree'
        assert entry.repairable is True
        assert entry.pre_llm is False

    def test_build_without_worktree_raises(self) -> None:
        ctx = GateBuildContext(worktree_path=None, allow_paths=[], base_branch='main')
        with pytest.raises(GateBuildError, match='ac_contract'):
            GATE_REGISTRY['ac_contract'].build(ctx)

def _git_repo(tmp_path: Path) -> Path:
    """Refs sumipan/nexus#6431 — minimal git repo for AcContractGate grep checks."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(['git', 'init', '-q', '-b', 'main', str(tmp_path)], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(tmp_path), 'config', 'user.email', 't@example.com'], check=True, capture_output=True)
    subprocess.run(['git', '-C', str(tmp_path), 'config', 'user.name', 't'], check=True, capture_output=True)
    return tmp_path

def test_greps_must_be_empty_fails_closed_on_invalid_entry(tmp_path: Path) -> None:
    """Refs sumipan/nexus#6431 — an unevaluable grep entry is a violation, not a pass."""
    repo = _git_repo(tmp_path)
    body = _ac_body('paths_must_exist: []\npaths_must_not_exist: []\ngreps_must_be_empty:\n  - pattern: NEEDLE\n')
    violations = _gate(repo).check(body, [])
    assert [v.location for v in violations] == ['greps_must_be_empty[0]']
    assert violations[0].rule_id == 'ac_contract.greps_must_be_empty'

def test_greps_must_be_empty_respects_exclude(tmp_path: Path) -> None:
    """Refs sumipan/nexus#6431 — exclude pathspec suppresses grep violations."""
    repo = _git_repo(tmp_path)
    pkg = repo / 'pkg'
    pkg.mkdir()
    (pkg / 'a.py').write_text('NEEDLE = 1\n', encoding='utf-8')
    (pkg / 'b.py').write_text('NEEDLE = 2\n', encoding='utf-8')
    body = _ac_body('paths_must_exist: []\npaths_must_not_exist: []\ngreps_must_be_empty:\n  - pattern: NEEDLE\n    paths:\n      - pkg\n    exclude:\n      - pkg/a.py\n')
    violations = _gate(repo).check(body, [])
    assert len(violations) == 1
    assert violations[0].location == 'pkg/b.py'
