"""Tests for pin_bump gate rule."""

from __future__ import annotations

import pytest
import yaml

import issuesmith.gate_rules.pin_bump  # noqa: F401 — registers on import
from issuesmith.config import reset_config_cache
from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.pin_bump import _self_package
from tests import legacy_text as lt

_INSTALLS = {
    "issuesmith": "/var/tmp/issuesmith",
    "ghdag": "/var/tmp/ghdag",
    "mltgnt": "/var/tmp/mltgnt",
}


@pytest.fixture(autouse=True)
def _installs_config(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "installs": _INSTALLS,
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





def test_self_package_from_target_repo():
    assert _self_package({"target_repo": "sumipan/ghdag"}) == "ghdag"
    assert _self_package({"target_repo": "sumipan/issuesmith"}) == "issuesmith"
    assert _self_package({"target_repo": "sumipan/nexus"}) is None
    assert _self_package({}) is None





def test_pin_bump_self_requires_pin():
    body = """\
```yaml
target_repo: sumipan/issuesmith
base_branch: main
allow_paths:
  - src/foo.py
requires_pins:
  issuesmith: v0.157.2
```

## Design

Feature work only.
"""
    violations = _check(body, [])
    assert len(violations) == 1
    v = violations[0]
    assert v.rule_id == "pin_bump.self_requires_pin"
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert "requires_pins" in (v.fix_hint or "")
    assert "issuesmith" in (v.fix_hint or "")




def test_in_feature_issue_fix_hint_discourages_self_requires_pin():
    violations = _check(_FEATURE_BODY_PIN, [])
    assert len(violations) == 1
    hint = violations[0].fix_hint or ""
    assert "requires_pins" in hint
    assert "target_repo" in hint.lower() or "own" in hint.lower() or "self" in hint.lower()


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
    """nexus#6325: citing the ported-from version is not a pin change."""
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
    """The hint must not send recovery into file_union_missing_in_parent."""
    body = _VERSION_MENTION_BODY.format(extra="- Raise the ghdag pin to v0.121.0.\n")
    hint = _check(body, [])[0].fix_hint or ""
    assert "Keep pyproject.toml in allow_paths" in hint
    assert "remove pyproject.toml" not in hint





def test_pin_bump_fails_when_clause_mixes_required_and_other_version():
    body = """\
```yaml
target_repo: sumipan/issuesmith
base_branch: main
allow_paths:
  - pyproject.toml
requires_pins:
  ghdag: 0.120.2
```

## Design

Bump the ghdag pin from 0.120.2 to 0.121.0.
"""
    violations = _check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"



def _write_pin_packages_config(tmp_path, monkeypatch, payload: dict) -> None:
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(yaml.safe_dump(payload), encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()


def test_pin_packages_outside_installs_is_watched(tmp_path, monkeypatch):
    """Refs sumipan/nexus#7148: pin_packages can list packages not in installs."""
    _write_pin_packages_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "sumipan/nexus",
            "installs": {},
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
    """Refs sumipan/nexus#7148: self-package detection uses pin_packages, not installs."""
    _write_pin_packages_config(
        tmp_path,
        monkeypatch,
        {
            "repo": "sumipan/issuesmith",
            "installs": {},
            "pin_packages": ["issuesmith"],
        },
    )
    metadata = {"target_repo": "sumipan/issuesmith"}
    assert _self_package(metadata) == "issuesmith"
    body = """\
```yaml
target_repo: sumipan/issuesmith
base_branch: main
allow_paths:
  - src/foo.py
requires_pins:
  issuesmith: v0.157.2
```

## Design

Feature work only.
"""
    violations = _check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.self_requires_pin"


@pytest.mark.parametrize("required_version", ["0.120.2", "v0.120.2"])
def test_pin_bump_ignores_required_version_on_a_line_with_the_pin_word(required_version: str):
    body = f"""\
```yaml
target_repo: sumipan/issuesmith
base_branch: main
allow_paths:
  - pyproject.toml
requires_pins:
  ghdag: {required_version}
```

## Design

Keep the ghdag pin at v0.120.2.
"""
    assert _check(body, []) == []
