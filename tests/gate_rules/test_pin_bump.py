"""Tests for pin_bump gate rule."""

from __future__ import annotations

import pytest
import yaml

import issuesmith.gate_rules.pin_bump  # noqa: F401 — registers on import
from issuesmith.config import reset_config_cache
from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.pin_bump import _self_package
from tests import legacy_text as lt

_PIN_PACKAGES = ("issuesmith", "ghdag", "mltgnt")


@pytest.fixture(autouse=True)
def _pin_gate_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "pin_packages": list(_PIN_PACKAGES),
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


_REQUIRES_PINS_BODY = """\
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


def test_requires_pins_key_fails_with_dependency_hint():
    """Refs sumipan/issuesmith#8028."""
    violations = _check(_REQUIRES_PINS_BODY, [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "pin_bump.requires_pins_removed"
    assert v.severity == "fail"
    hint = v.fix_hint or ""
    assert "Dependencies" in hint
    assert "- #N" in hint
    assert "remove requires_pins" in hint


def test_requires_pins_empty_still_fails_with_bump_label():
    body = """\
```yaml
target_repo: sumipan/nexus
requires_pins: {}
```

## Design

Legacy metadata.
"""
    violations = _check(body, ["bump:issuesmith"])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.requires_pins_removed"


def test_pin_bump_skipped_for_bump_label():
    assert _check(_FEATURE_BODY_PIN, ["bump:issuesmith"]) == []


def test_in_feature_issue_fix_hint_points_to_dependency_issue():
    """Refs sumipan/issuesmith#8028."""
    violations = _check(_FEATURE_BODY_PIN, [])
    assert len(violations) == 1
    hint = violations[0].fix_hint or ""
    assert "Dependencies" in hint
    assert "- #N" in hint
    assert "requires_pins" not in hint


def test_self_package_from_target_repo():
    assert _self_package({"target_repo": "sumipan/ghdag"}) == "ghdag"
    assert _self_package({"target_repo": "sumipan/issuesmith"}) == "issuesmith"
    assert _self_package({"target_repo": "sumipan/nexus"}) is None
    assert _self_package({}) is None


_PIN_ONLY_5581_BODY = f"""\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - pyproject.toml
  - tests/conventions/test_required_upstream_apis_exist.py
```

## Design

Bump the ghdag pin to ghdag v0.104.5 in pyproject.toml.

### Change targets

| {lt.REPOSITORY} | {lt.FILE_PATH} | {lt.CHANGE_TYPE} | {lt.DESCRIPTION} |
|---|---|---|---|
| `sumipan/nexus` | `pyproject.toml` | modify | raise ghdag pin |
| `sumipan/nexus` | `tests/conventions/test_required_upstream_apis_exist.py` | modify | assert pin |
"""


def test_pin_only_dependency_bump_passes_without_bump_label():
    assert _check(_PIN_ONLY_5581_BODY, []) == []


@pytest.mark.parametrize(
    "path",
    [
        "nested/pyproject.toml",
        "../tests/escape.py",
    ],
)
def test_pin_only_rejects_non_root_or_traversing_paths(path: str):
    body = _PIN_ONLY_5581_BODY.replace(
        "tests/conventions/test_required_upstream_apis_exist.py",
        path,
    )
    violations = _check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"


_VERSION_MENTION_BODY = """\
```yaml
target_repo: sumipan/issuesmith
base_branch: main
allow_paths:
  - pyproject.toml
  - src/issuesmith/body_editor.py
```

## Design

- Keep the behavior of ghdag `src/ghdag/markdown/body_editor.py` v0.120.2.
- Port the inputs and expectations of ghdag `tests/test_body_editor.py` v0.120.2.
- Drop the ghdag ownership note from the README and raise the `pyproject.toml` version to `0.176.0`.
{extra}
### Change targets

| @REPO@ | @FILE@ | @TYPE@ | @DESC@ |
|---|---|---|---|
| `sumipan/issuesmith` | `src/issuesmith/body_editor.py` | modify | own the implementation |
| `sumipan/issuesmith` | `pyproject.toml` | modify | version |
""".replace("@REPO@", lt.REPOSITORY).replace("@FILE@", lt.FILE_PATH).replace(
    "@TYPE@", lt.CHANGE_TYPE
).replace("@DESC@", lt.DESCRIPTION)


def test_dependency_version_mention_without_pin_word_passes():
    assert _check(_VERSION_MENTION_BODY.format(extra=""), []) == []


def test_pin_change_next_to_version_mentions_still_fails():
    body = _VERSION_MENTION_BODY.format(extra="- Raise the ghdag pin to v0.121.0.\n")
    violations = _check(body, [])
    assert [v.rule_id for v in violations] == ["pin_bump.in_feature_issue"]


def test_direct_reference_line_counts_as_pin_change():
    body = _VERSION_MENTION_BODY.format(
        extra='- `"ghdag @ git+https://github.com/sumipan/ghdag.git@v0.121.0"`\n'
    )
    assert [v.rule_id for v in _check(body, [])] == ["pin_bump.in_feature_issue"]


def test_fix_hint_keeps_pyproject_when_change_table_edits_it():
    body = _VERSION_MENTION_BODY.format(extra="- Raise the ghdag pin to v0.121.0.\n")
    hint = _check(body, [])[0].fix_hint or ""
    assert "Keep pyproject.toml in allow_paths" in hint
    assert "remove pyproject.toml" not in hint


def _write_pin_packages_config(tmp_path, monkeypatch, payload: dict) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_pin_packages_list_is_watched(tmp_path, monkeypatch):
    _write_pin_packages_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "sumipan/nexus",
            "pin_packages": ["ghdag"],
        },
    )
    body = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - pyproject.toml
  - src/foo.py
```

## Design

Bump the ghdag pin to ghdag v0.121.0 in pyproject.toml.
"""
    violations = _check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"
    assert "ghdag" in violations[0].message


def test_self_package_read_from_pin_packages(tmp_path, monkeypatch):
    _write_pin_packages_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "sumipan/issuesmith",
            "pin_packages": ["issuesmith"],
        },
    )
    metadata = {"target_repo": "sumipan/issuesmith"}
    assert _self_package(metadata) == "issuesmith"


_GHDAG_PIN_RAISE_BODY = """\
```yaml
target_repo: sumipan/issuesmith
base_branch: main
allow_paths:
  - pyproject.toml
```

## Design

Raise the ghdag pin to v0.121.0 in pyproject.toml.
"""


def test_lower_bound_package_raise_is_not_violation(monkeypatch):
    """Refs sumipan/issuesmith#7744: base lower-bound ghdag is not pin_bump blocked."""
    lower_base = 'ghdag = "ghdag>=0.100.0"\n'
    monkeypatch.setattr(
        "issuesmith.gate_rules.pin_bump.base_pyproject_text",
        lambda metadata, config: lower_base,
    )
    assert _check(_GHDAG_PIN_RAISE_BODY, []) == []


def test_url_pinned_package_change_still_violation(monkeypatch):
    url_base = (
        'ghdag = "ghdag @ git+https://github.com/sumipan/ghdag.git@v0.100.0"\n'
    )
    monkeypatch.setattr(
        "issuesmith.gate_rules.pin_bump.base_pyproject_text",
        lambda metadata, config: url_base,
    )
    violations = _check(_GHDAG_PIN_RAISE_BODY, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"


def test_unreadable_base_keeps_violation(monkeypatch):
    monkeypatch.setattr(
        "issuesmith.gate_rules.pin_bump.base_pyproject_text",
        lambda metadata, config: None,
    )
    violations = _check(_GHDAG_PIN_RAISE_BODY, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"
