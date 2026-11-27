"""``engine check --json`` output (refs #8141)."""

from __future__ import annotations

import json
from unittest.mock import patch

from issuesmith import engine


def test_engine_check_json_failure_is_single_object(capsys) -> None:
    """refs #8141: check --json returns one object with errors and exit 1."""
    with patch.object(engine, "check_state", return_value=["role-a: broken"]):
        code = engine.main(["check", "--json"])
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)
    assert payload == {"ok": False, "errors": ["role-a: broken"]}
    assert code == 1
    assert "REJECTED" not in out


def test_engine_check_success_and_text_compatibility(capsys) -> None:
    """refs #8141: healthy check succeeds in JSON and text modes."""
    with patch.object(engine, "check_state", return_value=[]):
        json_code = engine.main(["check", "--json"])
    json_out = capsys.readouterr().out.strip()
    json_payload = json.loads(json_out)
    assert json_payload == {"ok": True, "errors": []}
    assert json_code == 0
    assert "ACCEPTED" not in json_out

    with patch.object(engine, "check_state", return_value=[]):
        text_code = engine.main(["check"])
    text_out = capsys.readouterr().out.strip()
    assert text_out == "ACCEPTED"
    assert text_code == 0
