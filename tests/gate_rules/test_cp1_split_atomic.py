"""CP1 yaml contract checks for Issue body ``split: atomic`` (#4916)."""

from __future__ import annotations

import pytest
import yaml

from issuesmith.config import reset_config_cache
from issuesmith.gate_rules.cp1 import Cp1Rules


def _write_config(tmp_path, monkeypatch, scope_gate: dict | None = None) -> None:
    data: dict = {"repo": "sumipan/issuesmith"}
    if scope_gate:
        data["scope_gate"] = scope_gate
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(data), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


@pytest.fixture(autouse=True)
def _config(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch)
    yield
    reset_config_cache()


def _body(split_line: str, allow_paths_yaml: str) -> str:
    return (
        "```yaml\n"
        "target_repo: sumipan/issuesmith\n"
        "base_branch: main\n"
        f"{split_line}"
        f"{allow_paths_yaml}"
        "```\n\n## Overview\ncontent\n"
    )


def test_split_key_absent_is_ok():
    body = _body("", "allow_paths:\n  - src/**\n")
    violations = Cp1Rules().check(body, [])
    assert not any(v.rule_id.startswith("cp1.yaml_contract.split") for v in violations)


def test_split_atomic_within_hard_max_is_ok():
    body = _body("split: atomic\n", "allow_paths:\n  - src/**\n  - tests/**\n")
    violations = Cp1Rules().check(body, [])
    assert not any(v.rule_id.startswith("cp1.yaml_contract.split") for v in violations)


def test_split_invalid_value():
    body = _body("split: foo\n", "allow_paths:\n  - src/**\n")
    violations = Cp1Rules().check(body, [])
    v = next(v for v in violations if v.rule_id == "cp1.yaml_contract.split_invalid")
    assert v.severity == "fail"
    assert v.location == "split"
    assert v.auto_fixable is False
    assert v.fix_hint == "set split to atomic or remove it"


def test_split_atomic_over_hard_max(tmp_path, monkeypatch):
    _write_config(tmp_path, monkeypatch, scope_gate={"hard_max_files": 2})
    body = _body(
        "split: atomic\n",
        "allow_paths:\n  - a\n  - b\n  - c\n",
    )
    violations = Cp1Rules().check(body, [])
    v = next(
        v for v in violations if v.rule_id == "cp1.yaml_contract.split_atomic_over_hard_max"
    )
    assert v.severity == "fail"
    assert v.location == "allow_paths"
    assert v.auto_fixable is False
    assert "hard_max_files" in v.fix_hint
