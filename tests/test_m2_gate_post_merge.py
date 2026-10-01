"""test_m2_gate_post_merge.py — unit tests for M2 post_merge runtime verification."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from issuesmith import merge as merge_api
from issuesmith.m2_gate import _proceed_or_contract_retry

# ASCII fixture data.
BODY_WITH_POST_MERGE = """\
## Acceptance Criteria

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


def test_run_post_merge_pytest_reports_exit_code(tmp_path: Path) -> None:
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    (work_dir / "src").mkdir()
    with patch.object(merge_api.subprocess, "run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=0, stdout=".\n", stderr=""),
        ]
        result = merge_api.run_post_merge_pytest(str(work_dir))
    assert result.exit_code == 0
    assert result.cwd == str(work_dir)
    assert result.command == ["python3", "-m", "pytest", "-q"]


def test_run_post_merge_pytest_reports_failure_exit_code(tmp_path: Path) -> None:
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    (work_dir / "src").mkdir()
    with patch.object(merge_api.subprocess, "run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stdout="", stderr=""),
            MagicMock(returncode=1, stdout="", stderr="FAILED\n"),
        ]
        result = merge_api.run_post_merge_pytest(str(work_dir))
    assert result.exit_code == 1


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
