"""tests/test_ac_complete_entry_multiroot.py — AC-complete multi-root contract synthesis tests (#2866)"""

from pathlib import Path

from issuesmith.ac_complete_entry import (
    check_gate_multi_root,
    synthesize_contract_failures,
)
from issuesmith.ac_contract import run_checks

# ASCII fixture data.
BODY_ALL_CHECKED = """\
## Acceptance Criteria

- [x] done

```yaml
paths_must_exist:
  - only_in_nexus.md
  - only_in_target.py
paths_must_not_exist:
  - should_be_gone.txt
```
"""

BODY_NEXUS_ONLY = """\
## Acceptance Criteria

- [x] done

```yaml
paths_must_exist:
  - only_in_nexus.md
```
"""

BODY_TARGET_ONLY = """\
## Acceptance Criteria

- [x] done

```yaml
paths_must_exist:
  - only_in_target.py
```
"""

BODY_MUST_NOT_EXIST = """\
## Acceptance Criteria

- [x] done

```yaml
paths_must_not_exist:
  - lingering.txt
```
"""

BODY_ONE_UNCHECKED = """\
## Acceptance Criteria

- [ ] foo
"""


def _records(contract: dict, root: Path) -> list[dict]:
    return run_checks(contract, root)


def test_check_gate_multi_root_unchecked_ac_returns_retry(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    result = check_gate_multi_root(
        BODY_ONE_UNCHECKED,
        [],
        {"sumipan/issuesmith": root},
    )
    assert result["action"] == "retry"
    assert result["unchecked_count"] == 1


def test_synthesize_paths_must_not_exist_both_roots_must_be_clear(tmp_path):
    nexus = tmp_path / "nexus"
    target = tmp_path / "target"
    nexus.mkdir()
    target.mkdir()
    (nexus / "lingering.txt").write_text("bad")

    contract = {"paths_must_not_exist": ["lingering.txt"]}
    failures = synthesize_contract_failures(
        {
            "nexus": _records(contract, nexus),
            "target": _records(contract, target),
        }
    )
    assert any("lingering.txt" in f for f in failures)


