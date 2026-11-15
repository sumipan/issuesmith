"""Doctor CLI JSON output (#6779 / parent #6701)."""

from __future__ import annotations

import json
from unittest.mock import patch

from issuesmith.cli import _cmd_doctor


def test_doctor_json_fail_exits_1(capsys) -> None:
    """refs #6779: any failed check yields exit code 1 in JSON mode."""
    with patch(
        "issuesmith.ops.preflight._check_pyproject_no_direct_pin",
        return_value=(False, "fail"),
    ):
        rc = _cmd_doctor(["--json"])
    assert rc == 1
    rows = json.loads(capsys.readouterr().out)
    assert any(row.get("ok") is False for row in rows)
