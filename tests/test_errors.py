"""IssuesmithError base contract. refs #8127."""

from __future__ import annotations

import pytest

from issuesmith.errors import IssuesmithError


def test_issuesmith_error_to_dict_uses_code_exit() -> None:
    """refs #8127: code, hint, exit_code mapping, and unknown code rejection."""
    exc = IssuesmithError("x", code="NOT_FOUND", hint="h")
    assert exc.to_dict() == {
        "code": "NOT_FOUND",
        "message": "x",
        "hint": "h",
        "exit_code": 1,
    }
    assert exc.exit_code == 1

    default = IssuesmithError("failed")
    assert default.to_dict()["code"] == "FAILED"
    assert default.exit_code == 1

    with pytest.raises(ValueError, match="unknown error code"):
        IssuesmithError("x", code="NO_SUCH_CODE")
