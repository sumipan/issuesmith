"""Unit tests for AllowPathsForbiddenRules (#5408)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest import mock

from issuesmith.gate_rules.allow_paths_forbidden import AllowPathsForbiddenRules


def _body(*, allow_paths: list[str] | None = None, host_allow_paths: list[str] | None = None, extra_yaml: str = "") -> str:
    lines = ["```yaml", "target_repo: sumipan/nexus", "base_branch: main"]
    if allow_paths is not None:
        lines.append("allow_paths:")
        for p in allow_paths:
            lines.append(f'  - "{p}"')
    if host_allow_paths is not None:
        lines.append("host_allow_paths:")
        for p in host_allow_paths:
            lines.append(f'  - "{p}"')
    if extra_yaml:
        lines.append(extra_yaml.rstrip("\n"))
    lines.append("```")
    lines.append("")
    lines.append("## Overview\ncontent\n")
    return "\n".join(lines)


def _mock_config(forbidden: list[str], except_paths: list[str] | None = None):
    cfg = mock.MagicMock()
    cfg.forbidden_pr_paths = forbidden
    cfg.forbidden_pr_paths_except = except_paths or []
    return cfg


def test_exact_overlap_procfile():
    body = _body(allow_paths=["Procfile"])
    with mock.patch(
        "issuesmith.gate_rules.allow_paths_forbidden.get_config",
        return_value=_mock_config(["Procfile"]),
    ):
        violations = AllowPathsForbiddenRules().check(body, [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "allow_paths_forbidden.overlap"
    assert v.severity == "fail"
    assert v.location == "Procfile"
    assert "Procfile" in v.fix_hint


def test_allow_contained_in_forbidden_glob():
    body = _body(allow_paths=["scripts/code-pull.py"])
    with mock.patch(
        "issuesmith.gate_rules.allow_paths_forbidden.get_config",
        return_value=_mock_config(["scripts/**"]),
    ):
        violations = AllowPathsForbiddenRules().check(body, [])
    assert len(violations) == 1
    assert "scripts/**" in violations[0].message


def test_forbidden_contained_in_allow_glob():
    body = _body(allow_paths=["scripts/**"])
    with mock.patch(
        "issuesmith.gate_rules.allow_paths_forbidden.get_config",
        return_value=_mock_config(["scripts/code-pull.py"]),
    ):
        violations = AllowPathsForbiddenRules().check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "allow_paths_forbidden.overlap"


def test_host_allow_paths_overlap():
    body = _body(allow_paths=[], host_allow_paths=["Procfile"])
    with mock.patch(
        "issuesmith.gate_rules.allow_paths_forbidden.get_config",
        return_value=_mock_config(["Procfile"]),
    ):
        violations = AllowPathsForbiddenRules().check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "allow_paths_forbidden.overlap"


def test_fixture_path_not_a_violation():
    body = _body(allow_paths=["tests/fixtures/a.jsonl"])
    with mock.patch(
        "issuesmith.gate_rules.allow_paths_forbidden.get_config",
        return_value=_mock_config(["*.jsonl"]),
    ):
        violations = AllowPathsForbiddenRules().check(body, [])
    assert violations == []


def test_forbidden_except_skips_overlap():
    body = _body(allow_paths=["special/only.py"])
    with mock.patch(
        "issuesmith.gate_rules.allow_paths_forbidden.get_config",
        return_value=_mock_config(["special/**"], except_paths=["special/only.py"]),
    ):
        violations = AllowPathsForbiddenRules().check(body, [])
    assert violations == []


def test_no_overlap_returns_empty():
    body = _body(allow_paths=["src/foo.py"])
    with mock.patch(
        "issuesmith.gate_rules.allow_paths_forbidden.get_config",
        return_value=_mock_config(["Procfile"]),
    ):
        violations = AllowPathsForbiddenRules().check(body, [])
    assert violations == []


def test_scope_gate_override_skips_check():
    body = _body(allow_paths=["Procfile"], extra_yaml="scope_gate:\n  enabled: false")
    with mock.patch(
        "issuesmith.gate_rules.allow_paths_forbidden.get_config",
        return_value=_mock_config(["Procfile"]),
    ):
        violations = AllowPathsForbiddenRules().check(body, [])
    assert violations == []


def test_missing_yaml_returns_empty():
    with mock.patch(
        "issuesmith.gate_rules.allow_paths_forbidden.get_config",
        return_value=_mock_config(["Procfile"]),
    ):
        violations = AllowPathsForbiddenRules().check("## Overview\nno yaml\n", [])
    assert violations == []


def test_dot_slash_procfile_normalizes():
    body = _body(allow_paths=["./Procfile"])
    with mock.patch(
        "issuesmith.gate_rules.allow_paths_forbidden.get_config",
        return_value=_mock_config(["Procfile"]),
    ):
        violations = AllowPathsForbiddenRules().check(body, [])
    assert len(violations) == 1
    assert violations[0].location == "Procfile"


def test_first_forbidden_match_only_one_violation_per_allow():
    body = _body(allow_paths=["scripts/foo.py"])
    with mock.patch(
        "issuesmith.gate_rules.allow_paths_forbidden.get_config",
        return_value=_mock_config(["scripts/**", "scripts/foo.py"]),
    ):
        violations = AllowPathsForbiddenRules().check(body, [])
    assert len(violations) == 1


def test_gate_preflight_matches_check(tmp_path, monkeypatch):
    import os

    import yaml

    from issuesmith.config import reset_config_cache

    body = _body(allow_paths=["Procfile"])
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "scope_gate": {"enabled": False},
                "forbidden_pr_paths": ["Procfile"],
                "phases": [
                    {
                        "name": "draft",
                        "role": "design",
                        "entry_step": "b1",
                        "handler": "brushup",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    body_path = tmp_path / "body.md"
    body_path.write_text(body, encoding="utf-8")
    try:
        direct = AllowPathsForbiddenRules().check(body, [])
        src_root = Path(__file__).resolve().parents[2] / "src"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "issuesmith",
                "gate-preflight",
                "--gate",
                "allow_paths_forbidden",
                "--body-file",
                str(body_path),
            ],
            capture_output=True,
            text=True,
            env={
                **os.environ,
                "ISSUESMITH_CONFIG": str(cfg_path),
                "PYTHONPATH": f"{src_root}{os.pathsep}{os.environ.get('PYTHONPATH', '')}",
            },
        )
        assert result.returncode == 0, result.stderr
        preflight = json.loads(result.stdout)
        assert [v["rule_id"] for v in preflight] == [v.rule_id for v in direct]
        assert preflight[0]["location"] == "Procfile"
    finally:
        reset_config_cache()
