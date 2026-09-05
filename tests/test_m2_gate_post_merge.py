"""test_m2_gate_post_merge.py — M2 post_merge 実行時検証のユニットテスト。"""
from __future__ import annotations

from unittest.mock import patch

from issuesmith.m2_gate import _proceed_or_contract_retry

BODY_WITH_POST_MERGE = """\
## 受け入れ条件

```yaml
paths_must_exist:
  - README.md
post_merge:
  - kind: tag
    repo: sumipan/issuesmith
    tag: v0.1.0
```

- [x] done
"""


def test_proceed_or_contract_retry_migrate_when_post_merge_incomplete(tmp_path):
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    with patch(
        "issuesmith.ops.preflight.check_post_merge",
        return_value=[(False, "tag v0.1.0 not found: cd /var/tmp/issuesmith && git tag v0.1.0")],
    ):
        result = _proceed_or_contract_retry(
            BODY_WITH_POST_MERGE,
            True,
            True,
            repo_root=tmp_path,
        )
    assert result["action"] == "migrate"
    assert result["has_migration_label"] is True


def test_proceed_or_contract_retry_proceed_when_post_merge_complete(tmp_path):
    (tmp_path / "README.md").write_text("ok", encoding="utf-8")
    with patch(
        "issuesmith.ops.preflight.check_post_merge",
        return_value=[(True, "tag v0.1.0 OK")],
    ):
        result = _proceed_or_contract_retry(
            BODY_WITH_POST_MERGE,
            True,
            True,
            repo_root=tmp_path,
        )
    assert result["action"] == "proceed"
