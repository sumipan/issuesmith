"""Tests for pin_bump gate rule."""

from __future__ import annotations

import pytest
import yaml

import issuesmith.gate_rules.pin_bump  # noqa: F401 — registers on import
from issuesmith.config import reset_config_cache
from issuesmith.gate_rules import GATE_REGISTRY
from issuesmith.gate_rules.pin_bump import _self_package
from issuesmith.step_verify import collect_violations
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


def test_pin_bump_scans_host_allow_paths():
    body = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
host_allow_paths:
  - pyproject.toml
```

## Design

Raise the issuesmith pin to v0.133.1.
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


def test_pin_bump_registered_and_collected_by_step_verify(tmp_path, monkeypatch):
    cfg_path = tmp_path / "issuesmith.yaml"
    cfg_path.write_text(
        yaml.safe_dump(
            {
                "repo": "sumipan/nexus",
                "scope_gate": {"enabled": False},
                "installs": _INSTALLS,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg_path))
    reset_config_cache()
    assert "pin_bump" in GATE_REGISTRY
    violations = collect_violations(_FEATURE_BODY_PIN, [])
    assert any(v.rule_id == "pin_bump.in_feature_issue" for v in violations)


def test_self_package_from_target_repo():
    assert _self_package({"target_repo": "sumipan/ghdag"}) == "ghdag"
    assert _self_package({"target_repo": "sumipan/issuesmith"}) == "issuesmith"
    assert _self_package({"target_repo": "sumipan/nexus"}) is None
    assert _self_package({}) is None


def test_pin_bump_allows_self_version_bump_in_design():
    body = """\
```yaml
target_repo: sumipan/ghdag
base_branch: main
allow_paths:
  - pyproject.toml
```

## Design

bump ghdag version to 0.104.4
"""
    assert _check(body, []) == []


def test_pin_bump_fails_on_other_package_pin_in_self_repo():
    body = """\
```yaml
target_repo: sumipan/ghdag
base_branch: main
allow_paths:
  - pyproject.toml
```

## Design

raise mltgnt pin to v0.130.0
"""
    violations = _check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"
    assert "mltgnt" in violations[0].message


def test_pin_bump_fails_on_dependency_pin_for_non_install_repo():
    body = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - pyproject.toml
```

## Design

raise ghdag pin to v0.104.4
"""
    violations = _check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"


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


def test_pin_bump_allows_requires_pins_for_other_package_on_nexus():
    body = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - src/foo.py
requires_pins:
  issuesmith: v0.157.2
```

## Design

Waits for issuesmith pin.
"""
    assert _check(body, []) == []


def test_pin_bump_skips_self_requires_pin_with_bump_label():
    body = """\
```yaml
target_repo: sumipan/issuesmith
base_branch: main
requires_pins:
  issuesmith: v0.157.2
```

## Design

Bump path.
"""
    assert _check(body, ["bump:issuesmith"]) == []


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


def test_pin_only_with_src_row_still_fails_in_feature_issue():
    body = _PIN_ONLY_5581_BODY.replace(
        "| `sumipan/nexus` | `tests/conventions/test_required_upstream_apis_exist.py` | modify | assert pin |",
        "| `sumipan/nexus` | `src/foo.py` | modify | impl |",
    )
    violations = _check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"


def test_pin_only_with_changelog_row_still_fails_in_feature_issue():
    body = _PIN_ONLY_5581_BODY + (
        "\n| `sumipan/nexus` | `CHANGELOG.md` | modify | note |\n"
    )
    violations = _check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"


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


def test_pin_only_no_table_keeps_existing_pyproject_pin_detection():
    body = """\
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
    violations = _check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"


def test_pin_only_other_repo_rows_only_unchanged():
    body = """\
```yaml
target_repo: sumipan/nexus
base_branch: main
allow_paths:
  - pyproject.toml
```

## Design

Raise the issuesmith pin to v0.133.1.

### Change targets

| {lt.REPOSITORY} | {lt.FILE_PATH} | {lt.CHANGE_TYPE} | {lt.DESCRIPTION} |
|---|---|---|---|
| `sumipan/ghdag` | `pyproject.toml` | modify | version |
"""
    violations = _check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"


def test_pin_only_self_requires_pin_still_fails():
    body = """\
```yaml
target_repo: sumipan/issuesmith
base_branch: main
allow_paths:
  - pyproject.toml
  - tests/foo/test_bar.py
requires_pins:
  issuesmith: v0.157.2
```

## Design

Pin-only table with invalid self requires_pins.

### Change targets

| {lt.REPOSITORY} | {lt.FILE_PATH} | {lt.CHANGE_TYPE} | {lt.DESCRIPTION} |
|---|---|---|---|
| `sumipan/issuesmith` | `pyproject.toml` | modify | pin |
| `sumipan/issuesmith` | `tests/foo/test_bar.py` | modify | test |
"""
    violations = _check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.self_requires_pin"


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


def test_pin_bump_ignores_package_and_version_in_separate_clauses():
    body = """\
```yaml
target_repo: sumipan/issuesmith
base_branch: main
allow_paths:
  - pyproject.toml
```

## Design

README.md から ghdag 所有の記述を除き、pyproject.toml の版を 0.177.0 に上げる。
"""
    assert _check(body, []) == []


@pytest.mark.parametrize("required_version", ["0.120.2", "v0.120.2"])
def test_pin_bump_ignores_reference_to_required_version(required_version: str):
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

ghdag v0.120.2 の振る舞いを変えない。
"""
    assert _check(body, []) == []


def test_pin_bump_fails_on_version_outside_requires_pins():
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

ghdag の pin を v0.121.0 に上げる。
"""
    violations = _check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"


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

ghdag の pin を 0.120.2 から 0.121.0 に上げる。
"""
    violations = _check(body, [])
    assert len(violations) == 1
    assert violations[0].rule_id == "pin_bump.in_feature_issue"
