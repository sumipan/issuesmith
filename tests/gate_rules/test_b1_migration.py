"""tests/gate_rules/test_b1_migration.py — unit tests for the b1_migration gate."""
from __future__ import annotations

import issuesmith.gate_rules.b1_migration  # noqa: F401 — registers on import
from issuesmith.gate_rules import GATE_REGISTRY

MIGRATION_LABELS = ["scope:migration"]
NON_MIGRATION_LABELS = ["scope:feature"]

MIGRATION_PROCEDURE_SKELETON = """\
## Migration Steps

(write the commands MG1 runs here)

```bash
# e.g. update /var/tmp/mltgnt to the latest main
cd /var/tmp/mltgnt && git fetch origin && git checkout main && git pull origin main
pip install -e "/var/tmp/mltgnt/[dev]" --no-deps

# check that the merged file exists
test -f <target file> && echo "OK: file exists"
```
"""


def _check(body: str, labels: list[str]):
    gate = GATE_REGISTRY["b1_migration"]()
    return gate.check(body, labels)


def _rule_ids(body: str, labels: list[str]) -> set[str]:
    return {v.rule_id for v in _check(body, labels)}


# Body that satisfies all 3 requirements (procedure, runtime-state survey, migration test contract)
BODY_COMPLETE = """\
## Impact Survey

some impact analysis

### Runtime State Survey

- **Persistent state files**: logs/.diary-observer-state.json
- **Untracked live data**: logs/.diary-observer-snapshot-*.md
- **Invariants between data**: hash(snapshot) == state.handled_hash
- **Recovery from an interrupted run**: atomic write (tempfile + os.replace) recovers by itself

## Migration Steps

```bash
test -f tools/foo.py && echo "OK"
```

## Acceptance Criteria

```yaml
paths_must_exist:
  - tests/tools/secretary/test_observer_migration.py
post_merge:
  - kind: stable_install
    repo: sumipan/issuesmith
    path: /var/tmp/issuesmith
removed_trees:
  - tools/issuesmith
```

- [ ] the migration test passes
"""

BODY_WITHOUT_MIGRATION_PROCEDURE = """\
## Impact Survey

some impact analysis
"""

BODY_MISSING_STATE_SURVEY = """\
## Impact Survey

some impact analysis

## Migration Steps

```bash
test -f tools/foo.py && echo "OK"
```

## Acceptance Criteria

```yaml
paths_must_exist:
  - tests/test_migration.py
post_merge:
  - kind: stable_install
    repo: sumipan/issuesmith
    path: /var/tmp/issuesmith
removed_trees:
  - tools/issuesmith
```
"""

BODY_MISSING_TEST_CONTRACT = """\
## Impact Survey

### Runtime State Survey

- **Persistent state files**: (none)

## Migration Steps

```bash
test -f tools/foo.py && echo "OK"
```

## Acceptance Criteria

```yaml
paths_must_exist:
  - tools/foo.py
post_merge:
  - kind: stable_install
    repo: sumipan/issuesmith
    path: /var/tmp/issuesmith
removed_trees:
  - tools/issuesmith
```
"""


def test_no_migration_label_returns_empty():
    assert _check(BODY_WITHOUT_MIGRATION_PROCEDURE, NON_MIGRATION_LABELS) == []


def test_empty_labels_returns_empty():
    assert _check(BODY_WITHOUT_MIGRATION_PROCEDURE, []) == []


def test_migration_label_with_complete_body_returns_empty():
    assert _check(BODY_COMPLETE, MIGRATION_LABELS) == []


def test_migration_label_without_section_returns_procedure_violation():
    violations = _check(BODY_WITHOUT_MIGRATION_PROCEDURE, MIGRATION_LABELS)
    by_id = {v.rule_id: v for v in violations}
    v = by_id["b1_migration.migration_procedure_missing"]
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert v.fix_hint.startswith("## Migration Steps")
    assert "```bash" in v.fix_hint


def test_missing_everything_returns_all_five_violations():
    assert _rule_ids(BODY_WITHOUT_MIGRATION_PROCEDURE, MIGRATION_LABELS) == {
        "b1_migration.migration_procedure_missing",
        "b1_migration.state_survey_missing",
        "b1_migration.verification_test_missing",
        "b1_migration.post_merge_missing",
        "b1_migration.removed_trees_missing",
    }


def test_missing_state_survey_returns_violation_with_skeleton():
    violations = _check(BODY_MISSING_STATE_SURVEY, MIGRATION_LABELS)
    by_id = {v.rule_id: v for v in violations}
    assert set(by_id) == {"b1_migration.state_survey_missing"}
    v = by_id["b1_migration.state_survey_missing"]
    assert v.auto_fixable is True
    assert "Runtime State Survey" in v.fix_hint
    assert v.fix_hint.count("**") >= 8


def test_empty_state_survey_section_is_violation():
    body = BODY_MISSING_STATE_SURVEY.replace(
        "some impact analysis",
        "some impact analysis\n\n### Runtime State Survey\n",
    )
    assert "b1_migration.state_survey_missing" in _rule_ids(body, MIGRATION_LABELS)


def test_missing_test_contract_returns_violation():
    violations = _check(BODY_MISSING_TEST_CONTRACT, MIGRATION_LABELS)
    by_id = {v.rule_id: v for v in violations}
    assert set(by_id) == {"b1_migration.verification_test_missing"}
    assert "paths_must_exist" in by_id["b1_migration.verification_test_missing"].fix_hint


def test_tools_scoped_tests_path_satisfies_contract():
    body = BODY_MISSING_TEST_CONTRACT.replace(
        "  - tools/foo.py",
        "  - tools/issuesmith/tests/test_foo_migration.py",
    )
    assert _check(body, MIGRATION_LABELS) == []


def test_registered_in_gate_registry():
    assert "b1_migration" in GATE_REGISTRY


BODY_MISSING_POST_MERGE = """\
## Impact Survey

### Runtime State Survey

- **Persistent state files**: (none)

## Migration Steps

```bash
test -f tools/foo.py && echo "OK"
```

## Acceptance Criteria

```yaml
paths_must_exist:
  - tests/test_migration.py
removed_trees:
  - tools/issuesmith
```
"""

BODY_MISSING_REMOVED_TREES = """\
## Impact Survey

### Runtime State Survey

- **Persistent state files**: (none)

## Migration Steps

```bash
test -f tools/foo.py && echo "OK"
```

## Acceptance Criteria

```yaml
paths_must_exist:
  - tests/test_migration.py
post_merge:
  - kind: stable_install
    repo: sumipan/issuesmith
    path: /var/tmp/issuesmith
```
"""


def test_missing_post_merge_returns_violation():
    violations = _check(BODY_MISSING_POST_MERGE, MIGRATION_LABELS)
    by_id = {v.rule_id: v for v in violations}
    assert "b1_migration.post_merge_missing" in by_id
    v = by_id["b1_migration.post_merge_missing"]
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert "post_merge" in v.fix_hint


def test_missing_removed_trees_returns_violation():
    violations = _check(BODY_MISSING_REMOVED_TREES, MIGRATION_LABELS)
    by_id = {v.rule_id: v for v in violations}
    assert "b1_migration.removed_trees_missing" in by_id
    v = by_id["b1_migration.removed_trees_missing"]
    assert v.severity == "fail"
    assert v.auto_fixable is True
    assert "removed_trees" in v.fix_hint


def _with_ac_yaml(yaml_text: str) -> str:
    """Return BODY_COMPLETE with its AC YAML block replaced by yaml_text."""
    start = BODY_COMPLETE.index("```yaml\n") + len("```yaml\n")
    end = BODY_COMPLETE.index("```", start)
    return BODY_COMPLETE[:start] + yaml_text + BODY_COMPLETE[end:]


_AC_PATHS = "paths_must_exist:\n  - tests/test_migration.py\n"
_AC_REMOVED = "removed_trees:\n  - tools/issuesmith\n"


def test_post_merge_free_text_item_is_schema_violation():
    """AC-1: a free-text post_merge item fails B1 with post_merge_schema."""
    body = _with_ac_yaml(
        _AC_PATHS
        + "post_merge:\n"
        + '  - "run python3 scripts/preflight.py after MG1"\n'
        + _AC_REMOVED
    )
    violations = _check(body, MIGRATION_LABELS)
    by_id = {v.rule_id: v for v in violations}
    assert set(by_id) == {"b1_migration.post_merge_schema"}
    v = by_id["b1_migration.post_merge_schema"]
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert "post_merge[0]" in v.message
    assert "each post_merge item must be a dict" in v.message
    assert "kind: stable_install" in v.fix_hint


def test_post_merge_valid_items_of_all_kinds_pass():
    """AC-2: complete stable_install / tag / restart items yield no violation."""
    body = _with_ac_yaml(
        _AC_PATHS
        + "post_merge:\n"
        + "  - kind: stable_install\n"
        + "    repo: sumipan/issuesmith\n"
        + "    path: /var/tmp/issuesmith\n"
        + "  - kind: tag\n"
        + "    repo: sumipan/issuesmith\n"
        + "    tag: v0.1.0\n"
        + "  - kind: restart\n"
        + "    processes: [release_watcher]\n"
        + _AC_REMOVED
    )
    assert _check(body, MIGRATION_LABELS) == []


def test_post_merge_unknown_kind_lists_allowed_kinds():
    """AC-3: an unknown kind fails and the fix_hint lists the allowed kinds."""
    body = _with_ac_yaml(
        _AC_PATHS + "post_merge:\n  - kind: manual\n" + _AC_REMOVED
    )
    violations = _check(body, MIGRATION_LABELS)
    by_id = {v.rule_id: v for v in violations}
    assert set(by_id) == {"b1_migration.post_merge_schema"}
    v = by_id["b1_migration.post_merge_schema"]
    assert "unknown kind: manual" in v.message
    assert "allowed: stable_install, tag, restart" in v.message
    for kind in ("stable_install", "tag", "restart"):
        assert kind in v.fix_hint


def test_post_merge_missing_required_field_is_violation():
    body = _with_ac_yaml(
        _AC_PATHS
        + "post_merge:\n"
        + "  - kind: stable_install\n"
        + "    repo: sumipan/issuesmith\n"
        + "  - kind: restart\n"
        + _AC_REMOVED
    )
    v = {x.rule_id: x for x in _check(body, MIGRATION_LABELS)}[
        "b1_migration.post_merge_schema"
    ]
    assert "kind stable_install: missing required field: path" in v.message
    assert "kind restart: missing required field: processes" in v.message


def test_post_merge_schema_not_reported_when_key_missing():
    ids = _rule_ids(BODY_MISSING_POST_MERGE, MIGRATION_LABELS)
    assert "b1_migration.post_merge_missing" in ids
    assert "b1_migration.post_merge_schema" not in ids


def test_removed_trees_non_string_item_is_schema_violation():
    """AC-4: a non-string removed_trees item fails with removed_trees_schema."""
    body = _with_ac_yaml(
        _AC_PATHS
        + "post_merge:\n"
        + "  - kind: tag\n"
        + "    repo: sumipan/issuesmith\n"
        + "    tag: v0.1.0\n"
        + "removed_trees:\n"
        + "  - tools/issuesmith\n"
        + "  - path: tools/other\n"
    )
    violations = _check(body, MIGRATION_LABELS)
    by_id = {v.rule_id: v for v in violations}
    assert set(by_id) == {"b1_migration.removed_trees_schema"}
    v = by_id["b1_migration.removed_trees_schema"]
    assert v.severity == "fail"
    assert v.auto_fixable is False
    assert "removed_trees items must be strings, got dict" in v.message
    assert v.fix_hint == "removed_trees:\n  - tools/<package>"


def test_removed_trees_schema_not_reported_when_key_missing():
    ids = _rule_ids(BODY_MISSING_REMOVED_TREES, MIGRATION_LABELS)
    assert "b1_migration.removed_trees_missing" in ids
    assert "b1_migration.removed_trees_schema" not in ids


def test_post_merge_manual_check_passes():
    body = _with_ac_yaml(
        _AC_PATHS
        + "post_merge:\n"
        + "  - kind: manual_check\n"
        + '    description: "run python3 scripts/preflight.py after MG1"\n'
        + _AC_REMOVED
    )
    assert _check(body, MIGRATION_LABELS) == []


def test_post_merge_manual_check_without_description_is_violation():
    body = _with_ac_yaml(
        _AC_PATHS + "post_merge:\n  - kind: manual_check\n" + _AC_REMOVED
    )
    violations = _check(body, MIGRATION_LABELS)
    by_id = {v.rule_id: v for v in violations}
    assert set(by_id) == {"b1_migration.post_merge_schema"}
    assert (
        "kind manual_check: missing required field: description"
        in by_id["b1_migration.post_merge_schema"].message
    )


def test_post_merge_manual_check_empty_description_is_violation():
    body = _with_ac_yaml(
        _AC_PATHS
        + "post_merge:\n"
        + "  - kind: manual_check\n"
        + '    description: ""\n'
        + _AC_REMOVED
    )
    v = {x.rule_id: x for x in _check(body, MIGRATION_LABELS)}[
        "b1_migration.post_merge_schema"
    ]
    assert "description must be a non-empty string" in v.message


def test_post_merge_skeleton_lists_manual_check():
    v = {x.rule_id: x for x in _check(BODY_MISSING_POST_MERGE, MIGRATION_LABELS)}[
        "b1_migration.post_merge_missing"
    ]
    assert "kind: manual_check" in v.fix_hint

# ---------------------------------------------------------------------------
# Headings come from the language pack (#4476)
# ---------------------------------------------------------------------------


def _migration_body(sections) -> str:
    return (
        BODY_COMPLETE.replace("## Impact Survey", f"## {sections['impact_survey']}")
        .replace("### Runtime State Survey", f"### {sections['migration_state_survey']}")
        .replace("## Migration Steps", f"## {sections['migration']}")
        .replace("## Acceptance Criteria", f"## {sections['acceptance_criteria']}")
    )


def test_en_pack_headings_pass():
    from issuesmith.language import EN

    assert _check(_migration_body(EN.sections), MIGRATION_LABELS) == []


def _use_custom_headings(tmp_path, monkeypatch) -> dict:
    import dataclasses

    import yaml

    from issuesmith.config import reset_config_cache
    from issuesmith.language import EN, LanguagePack

    data = {}
    for f in dataclasses.fields(LanguagePack):
        value = getattr(EN, f.name)
        data[f.name] = list(value) if isinstance(value, tuple) else (
            value if isinstance(value, str) else dict(value)
        )
    sections = {key: f"X {heading}" for key, heading in EN.sections.items()}
    data["sections"] = sections
    pack = tmp_path / "pack.yaml"
    pack.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    cfg = tmp_path / "issuesmith.yaml"
    cfg.write_text(f"repo: example/app\nlanguage_pack: {pack}\n", encoding="utf-8")
    monkeypatch.setenv("ISSUESMITH_CONFIG", str(cfg))
    reset_config_cache()
    return sections


def test_custom_pack_headings_pass(tmp_path, monkeypatch):
    sections = _use_custom_headings(tmp_path, monkeypatch)
    assert _check(_migration_body(sections), MIGRATION_LABELS) == []


def test_custom_pack_default_headings_fail_with_same_rules(tmp_path, monkeypatch):
    """Body written with EN headings misses every section under a custom pack."""
    from issuesmith.language import EN

    sections = _use_custom_headings(tmp_path, monkeypatch)
    violations = _check(_migration_body(EN.sections), MIGRATION_LABELS)
    by_id = {v.rule_id: v for v in violations}
    assert set(by_id) == {
        "b1_migration.migration_procedure_missing",
        "b1_migration.state_survey_missing",
        "b1_migration.verification_test_missing",
        "b1_migration.post_merge_missing",
        "b1_migration.removed_trees_missing",
    }
    assert by_id["b1_migration.migration_procedure_missing"].fix_hint.startswith(
        f"## {sections['migration']}\n"
    )
    assert f"### {sections['migration_state_survey']}" in (
        by_id["b1_migration.state_survey_missing"].fix_hint
    )
    assert f"## {sections['acceptance_criteria']}" in (
        by_id["b1_migration.post_merge_missing"].message
    )
