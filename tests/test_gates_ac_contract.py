"""Unit tests for AcContractGate and registry wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from issuesmith.config import ConfigError
from issuesmith.gates import (
    GATE_REGISTRY,
    GateBuildContext,
    GateBuildError,
    validate_step_requires,
)
from issuesmith.gates.ac_contract import AcContractGate


def _ac_body(yaml_block: str) -> str:
    return f"## Acceptance Criteria\n\n```yaml\n{yaml_block}\n```\n"


def _gate(tmp_path: Path) -> AcContractGate:
    return AcContractGate(tmp_path)


class TestPathsMustExist:
    def test_missing_file_one_violation(self, tmp_path: Path) -> None:
        body = _ac_body("paths_must_exist:\n  - a.py\npaths_must_not_exist: []")
        violations = _gate(tmp_path).check(body, [])
        assert len(violations) == 1
        v = violations[0]
        assert v.rule_id == "ac_contract.paths_must_exist"
        assert v.severity == "fail"
        assert v.location == "a.py"
        assert v.auto_fixable is False
        assert v.fix_hint

    def test_present_file_no_violation(self, tmp_path: Path) -> None:
        (tmp_path / "a.py").write_text("# ok\n", encoding="utf-8")
        body = _ac_body("paths_must_exist:\n  - a.py\npaths_must_not_exist: []")
        assert _gate(tmp_path).check(body, []) == []

    def test_glob_no_match_one_violation_at_pattern(self, tmp_path: Path) -> None:
        body = _ac_body('paths_must_exist:\n  - "tests/*.py"\npaths_must_not_exist: []')
        violations = _gate(tmp_path).check(body, [])
        assert len(violations) == 1
        assert violations[0].location == "tests/*.py"

    def test_glob_with_match_no_violation(self, tmp_path: Path) -> None:
        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "one.py").write_text("", encoding="utf-8")
        body = _ac_body('paths_must_exist:\n  - "tests/*.py"\npaths_must_not_exist: []')
        assert _gate(tmp_path).check(body, []) == []


class TestPathsMustNotExist:
    def test_two_matches_two_violations(self, tmp_path: Path) -> None:
        legacy = tmp_path / "legacy"
        legacy.mkdir()
        (legacy / "a.jsonl").write_text("", encoding="utf-8")
        (legacy / "b.jsonl").write_text("", encoding="utf-8")
        body = _ac_body(
            "paths_must_exist: []\n"
            'paths_must_not_exist:\n  - "legacy/*.jsonl"'
        )
        violations = _gate(tmp_path).check(body, [])
        assert len(violations) == 2
        locs = {v.location for v in violations}
        assert locs == {"legacy/a.jsonl", "legacy/b.jsonl"}
        for v in violations:
            assert v.rule_id == "ac_contract.paths_must_not_exist"
            assert v.severity == "fail"

    def test_glob_no_files_no_violation(self, tmp_path: Path) -> None:
        body = _ac_body(
            "paths_must_exist: []\n"
            'paths_must_not_exist:\n  - "legacy/*.jsonl"'
        )
        assert _gate(tmp_path).check(body, []) == []


class TestContractEdgeCases:
    def test_no_contract_section(self, tmp_path: Path) -> None:
        assert _gate(tmp_path).check("body text", []) == []

    def test_broken_yaml(self, tmp_path: Path) -> None:
        body = "## Acceptance Criteria\n\n```yaml\n: [\n```\n"
        assert _gate(tmp_path).check(body, []) == []

    def test_non_list_paths_must_exist(self, tmp_path: Path) -> None:
        body = _ac_body("paths_must_exist: a.py\npaths_must_not_exist: []")
        assert _gate(tmp_path).check(body, []) == []

    def test_null_paths_must_exist(self, tmp_path: Path) -> None:
        body = _ac_body("paths_must_exist: null\npaths_must_not_exist: []")
        assert _gate(tmp_path).check(body, []) == []

    def test_invalid_paths_excluded(self, tmp_path: Path) -> None:
        body = _ac_body(
            "paths_must_exist:\n"
            '  - "/abs/a.py"\n'
            '  - "../a.py"\n'
            '  - ""\n'
            "paths_must_not_exist: []"
        )
        assert _gate(tmp_path).check(body, []) == []

    def test_references_must_resolve_not_evaluated(self, tmp_path: Path) -> None:
        body = _ac_body(
            "paths_must_exist: []\n"
            "paths_must_not_exist: []\n"
            "references_must_resolve:\n"
            "  - missing_file.py"
        )
        assert _gate(tmp_path).check(body, []) == []


class TestRegistry:
    def test_registry_entry(self) -> None:
        entry = GATE_REGISTRY["ac_contract"]
        assert entry.input_kind == "worktree"
        assert entry.repairable is True
        assert entry.pre_llm is False

    def test_build_without_worktree_raises(self) -> None:
        ctx = GateBuildContext(worktree_path=None, allow_paths=[], base_branch="main")
        with pytest.raises(GateBuildError, match="ac_contract"):
            GATE_REGISTRY["ac_contract"].build(ctx)

    def test_build_and_check_empty_contract(self, tmp_path: Path) -> None:
        ctx = GateBuildContext(
            worktree_path=tmp_path,
            allow_paths=[],
            base_branch="main",
        )
        gate = GATE_REGISTRY["ac_contract"].build(ctx)
        assert gate.check("body text", []) == []


def test_validate_step_requires_worktree_accepts_ac_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from issuesmith.config import load_config

    monkeypatch.setenv("ISSUESMITH_CONFIG", str(tmp_path / "issuesmith.yaml"))
    (tmp_path / "issuesmith.yaml").write_text(
        'repo: "example/app"\n'
        "steps:\n"
        "  p1:\n"
        '    module: "issuesmith.steps.p1"\n'
        '    input_kind: "worktree"\n'
        '    requires: ["ac_contract"]\n',
        encoding="utf-8",
    )
    cfg = load_config()
    validate_step_requires(cfg.steps)
    assert cfg.steps["p1"].requires == ("ac_contract",)


def test_validate_step_requires_issue_rejects_ac_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from issuesmith.config import load_config

    monkeypatch.setenv("ISSUESMITH_CONFIG", str(tmp_path / "issuesmith.yaml"))
    (tmp_path / "issuesmith.yaml").write_text(
        'repo: "example/app"\n'
        "steps:\n"
        "  b1:\n"
        '    module: "issuesmith.steps.b1"\n'
        '    input_kind: "issue"\n'
        '    requires: ["ac_contract"]\n',
        encoding="utf-8",
    )
    cfg = load_config()
    with pytest.raises(ConfigError, match="worktree gate 'ac_contract'"):
        validate_step_requires(cfg.steps)
