"""tests/gate_rules/test_b1_migration.py — unit tests for the b1_migration gate."""
from __future__ import annotations

import issuesmith.gate_rules.b1_migration  # noqa: F401 — registers on import
from issuesmith.gate_rules import GATE_REGISTRY

MIGRATION_LABELS = ["scope:migration"]
NON_MIGRATION_LABELS = ["scope:feature"]

# ASCII fixture data.
MIGRATION_PROCEDURE_SKELETON = """\
## Migration Steps

cFF08MG1 c304C_c5B9F_c884C_c3059_c308B_c30B3_c30DE_c30F3_c30C9_c3092_c3053_c3053_c306B_c8A18_c8FF0_c3059_c308B_cFF09

```bash
# c4F8B: /var/tmp/mltgnt c3092 main c6700_c65B0_c306B_c66F4_c65B0
cd /var/tmp/mltgnt && git fetch origin && git checkout main && git pull origin main
pip install -e "/var/tmp/mltgnt/[dev]" --no-deps

# c30DE_c30FC_c30B8_c6E08_c307F_c30D5_c30A1_c30A4_c30EB_c306E_c5B58_c5728_c78BA_c8A8D
test -f <c5BFE_c8C61_c30D5_c30A1_c30A4_c30EB> && echo "OK: file exists"
```
"""


def _check(body: str, labels: list[str]):
    gate = GATE_REGISTRY["b1_migration"]()
    return gate.check(body, labels)


def _rule_ids(body: str, labels: list[str]) -> set[str]:
    return {v.rule_id for v in _check(body, labels)}


# Body that satisfies all 3 requirements (procedure, runtime-state survey, migration test contract)
# ASCII fixture data.
BODY_COMPLETE = """\
## Impact Survey

some impact analysis

### Runtime State Survey

- **c6C38_c7D9A state c30D5_c30A1_c30A4_c30EB**: logs/.diary-observer-state.json
- **untracked c5B9F_c30C7_c30FC_c30BF**: logs/.diary-observer-snapshot-*.md
- **c30C7_c30FC_c30BF_c9593_c306E_c4E0D_c5909_c6761_c4EF6**: hash(snapshot) == state.handled_hash
- **c9014_c4E2D_c505C_c6B62_c6642_c306E_c5FA9_c65E7**: atomic writecFF08tempfile + os.replacecFF09_c3067_c81EA_c5DF1_c5FA9_c65E7_c53EF_c80FD

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

- [ ] c79FB_c884C_c30C6_c30B9_c30C8_c304C_c901A_c308B_c3053_c3068
"""

# ASCII fixture data.
BODY_WITHOUT_MIGRATION_PROCEDURE = """\
## Impact Survey

some impact analysis
"""

# ASCII fixture data.
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

# ASCII fixture data.
BODY_MISSING_TEST_CONTRACT = """\
## Impact Survey

### Runtime State Survey

- **c6C38_c7D9A state c30D5_c30A1_c30A4_c30EB**: cFF08_c8A72_c5F53_None_cFF09

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
    # ASCII fixture data.
    assert "Runtime State Survey" in v.fix_hint
    assert v.fix_hint.count("**") >= 8


def test_empty_state_survey_section_is_violation():
    # ASCII fixture data.
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


# ASCII fixture data.
BODY_MISSING_POST_MERGE = """\
## Impact Survey

### Runtime State Survey

- **c6C38_c7D9A state c30D5_c30A1_c30A4_c30EB**: cFF08_c8A72_c5F53_None_cFF09

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

# ASCII fixture data.
BODY_MISSING_REMOVED_TREES = """\
## Impact Survey

### Runtime State Survey

- **c6C38_c7D9A state c30D5_c30A1_c30A4_c30EB**: cFF08_c8A72_c5F53_None_cFF09

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
