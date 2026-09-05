"""tests/test_m2_gate_multiroot.py — M2 multi-root 契約合成テスト（#2866）"""

from pathlib import Path

from issuesmith.ac_contract import run_checks
from issuesmith.m2_gate import (
    check_gate_multi_root,
    synthesize_contract_failures,
)

BODY_ALL_CHECKED = """\
## 受け入れ条件

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
## 受け入れ条件

- [x] done

```yaml
paths_must_exist:
  - only_in_nexus.md
```
"""

BODY_TARGET_ONLY = """\
## 受け入れ条件

- [x] done

```yaml
paths_must_exist:
  - only_in_target.py
```
"""

BODY_MUST_NOT_EXIST = """\
## 受け入れ条件

- [x] done

```yaml
paths_must_not_exist:
  - lingering.txt
```
"""


def _records(contract: dict, root: Path) -> list[dict]:
    return run_checks(contract, root)


def test_synthesize_or_paths_must_exist_either_root_passes(tmp_path):
    nexus = tmp_path / "nexus"
    target = tmp_path / "target"
    nexus.mkdir()
    target.mkdir()
    (nexus / "only_in_nexus.md").write_text("ok")
    (target / "only_in_target.py").write_text("ok")

    contract = {
        "paths_must_exist": ["only_in_nexus.md", "only_in_target.py"],
        "paths_must_not_exist": [],
    }
    failures = synthesize_contract_failures(
        {
            "nexus": _records(contract, nexus),
            "target": _records(contract, target),
        }
    )
    assert failures == []


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


def test_check_gate_multi_root_nexus_only_contract_proceeds(tmp_path):
    nexus = tmp_path / "nexus"
    target = tmp_path / "target"
    nexus.mkdir()
    target.mkdir()
    (nexus / "only_in_nexus.md").write_text("ok")

    contract = {"paths_must_exist": ["only_in_nexus.md"]}
    result = check_gate_multi_root(
        BODY_NEXUS_ONLY,
        [],
        {"sumipan/nexus": nexus, "sumipan/issuesmith": target},
        contracts={"sumipan/nexus": contract, "sumipan/issuesmith": {}},
    )
    assert result["action"] == "proceed"


def test_check_gate_multi_root_target_only_contract_proceeds(tmp_path):
    nexus = tmp_path / "nexus"
    target = tmp_path / "target"
    nexus.mkdir()
    target.mkdir()
    (target / "only_in_target.py").write_text("ok")

    contract = {"paths_must_exist": ["only_in_target.py"]}
    result = check_gate_multi_root(
        BODY_TARGET_ONLY,
        [],
        {"sumipan/nexus": nexus, "sumipan/issuesmith": target},
        contracts={"sumipan/nexus": {}, "sumipan/issuesmith": contract},
    )
    assert result["action"] == "proceed"


def test_check_gate_multi_root_must_not_exist_retries(tmp_path):
    nexus = tmp_path / "nexus"
    target = tmp_path / "target"
    nexus.mkdir()
    target.mkdir()
    (target / "lingering.txt").write_text("still here")

    contract = {"paths_must_not_exist": ["lingering.txt"]}
    result = check_gate_multi_root(
        BODY_MUST_NOT_EXIST,
        [],
        {"sumipan/nexus": nexus, "sumipan/issuesmith": target},
        contracts={"sumipan/nexus": contract, "sumipan/issuesmith": contract},
    )
    assert result["action"] == "retry"
    assert result["contract_failures"]
