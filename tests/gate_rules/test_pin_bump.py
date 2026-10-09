"""Tests for pin_bump gate rule."""

from __future__ import annotations

import pytest
import yaml

import issuesmith.gate_rules.pin_bump  # noqa: F401 — registers on import
from issuesmith.b1_verify import collect_violations
from issuesmith.config import reset_config_cache
from issuesmith.gate_rules import GATE_REGISTRY


@pytest.fixture(autouse=True)
def _installs_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "installs": {"issuesmith": "/var/tmp/issuesmith"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    yield
    reset_config_cache()


def _check(body: str, labels: list[str]):
    return GATE_REGISTRY["pin_bump"]().check(body, labels)


_FEATURE_BODY_PIN = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - pyproject.toml
  - src/foo.py
```

## Design

Bump the issuesmith pin to issuesmith v0.133.1 in pyproject.toml.
"""


_FIXED_BODY = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - src/foo.py
requires_pins:
  issuesmith: v0.133.1
```

## Design

Requires issuesmith v0.133.1 after the bump workflow lands.
"""


def test_pin_bump_fails_on_feature_issue_changing_pin():
    violations = _check(_FEATURE_BODY_PIN, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"
    assert "issuesmith" in violations[0].message


def test_pin_bump_passes_after_requires_pins_fix():
    assert _check(_FIXED_BODY, []) == []


def test_pin_bump_skipped_for_bump_label():
    assert _check(_FEATURE_BODY_PIN, ["bump:issuesmith"]) == []


def test_pin_bump_scans_host_allow_paths():
    body = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
host_allow_paths:
  - pyproject.toml
```

## Design

Requires issuesmith v0.133.1 after the bump workflow lands.
"""
    violations = _check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"


def test_pin_bump_ignores_pyproject_without_pin_line():
    body = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - pyproject.toml
```

## Design

Raise only the package version field in pyproject.toml to 0.2.0.dev1.
"""
    assert _check(body, []) == []


def test_pin_bump_registered_and_collected_by_b1_verify(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "scope_gate": {"enabled": False},
                "installs": {"issuesmith": "/var/tmp/issuesmith"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    assert "pin_bump" in GATE_REGISTRY
    violations = collect_violations(_FEATURE_BODY_PIN, [])
    assert any(v.rule_id == "pin_bump.in_feature_issue" for v in violations)
