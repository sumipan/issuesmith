"""Unit tests for AllowPathsForbiddenRules (#5408)."""
from __future__ import annotations

from unittest import mock

from issuesmith.gate_rules.allow_paths_forbidden import AllowPathsForbiddenRules


def _body(*, allow_paths: list[str] | None=None, host_allow_paths: list[str] | None=None, extra_yaml: str='') -> str:
    lines = ['```yaml', 'target_repo: sumipan/nexus', 'base_branch: main']
    if allow_paths is not None:
        lines.append('allow_paths:')
        for p in allow_paths:
            lines.append(f'  - "{p}"')
    if host_allow_paths is not None:
        lines.append('host_allow_paths:')
        for p in host_allow_paths:
            lines.append(f'  - "{p}"')
    if extra_yaml:
        lines.append(extra_yaml.rstrip('\n'))
    lines.append('```')
    lines.append('')
    lines.append('## Overview\ncontent\n')
    return '\n'.join(lines)

def _mock_config(forbidden: list[str], except_paths: list[str] | None=None):
    cfg = mock.MagicMock()
    cfg.forbidden_pr_paths = forbidden
    cfg.forbidden_pr_paths_except = except_paths or []
    return cfg

def test_fixture_path_not_a_violation():
    body = _body(allow_paths=['tests/fixtures/a.jsonl'])
    with mock.patch('issuesmith.gate_rules.allow_paths_forbidden.get_config', return_value=_mock_config(['*.jsonl'])):
        violations = AllowPathsForbiddenRules().check(body, [])
    assert violations == []

def test_dot_slash_procfile_normalizes():
    body = _body(allow_paths=['./Procfile'])
    with mock.patch('issuesmith.gate_rules.allow_paths_forbidden.get_config', return_value=_mock_config(['Procfile'])):
        violations = AllowPathsForbiddenRules().check(body, [])
    assert len(violations) == 1
    assert violations[0].location == 'Procfile'
