"""Error code and stderr contracts for issuesmith runtime modules (#8129)."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import re

import pytest

import issuesmith
from issuesmith.cli_errors import ERROR_EXIT
from issuesmith.contract import RetrySignal
from issuesmith.errors import IssuesmithError
from tests.conventions._util import SRC, py_files

_LEGACY_MARKERS = (
    "error: ",
    "Error: ",
    "WORKTREE_ERROR:",
    "[issuesmith-dispatch] ERROR:",
)
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")


def _discover_public_error_classes() -> list[type[BaseException]]:
    found: dict[str, type[BaseException]] = {}
    for mod_info in pkgutil.walk_packages(issuesmith.__path__, issuesmith.__name__ + "."):
        module = importlib.import_module(mod_info.name)
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if obj.__module__ != module.__name__:
                continue
            if name.startswith("_") or not name.endswith("Error"):
                continue
            if not issubclass(obj, BaseException):
                continue
            if obj is IssuesmithError:
                continue
            found[f"{obj.__module__}.{name}"] = obj
    return sorted(found.values(), key=lambda c: (c.__module__, c.__name__))


def test_no_legacy_stderr_error_prefixes() -> None:
    """Refs #8129 — stderr must not use pre-contract error prefixes."""
    hits: list[str] = []
    for path in py_files(SRC):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "print" not in line or "stderr" not in line:
                continue
            if not any(marker in line for marker in _LEGACY_MARKERS):
                continue
            hits.append(f"{path.relative_to(SRC.parent.parent)}:{lineno}: {line.strip()}")
    assert hits == []


@pytest.mark.parametrize("cls", _discover_public_error_classes(), ids=lambda c: c.__name__)
def test_public_exception_contract_is_complete(cls: type[BaseException]) -> None:
    """Refs #8129 — every public *Error is an IssuesmithError with a registered code."""
    if cls is RetrySignal:
        assert not issubclass(cls, IssuesmithError)
        return
    assert issubclass(cls, IssuesmithError), f"{cls.__name__} must subclass IssuesmithError"
    assert IssuesmithError in cls.__mro__
    code = getattr(cls, "code", None)
    assert isinstance(code, str), f"{cls.__name__} must declare class-level code"
    assert _CODE_RE.fullmatch(code)
    assert code in ERROR_EXIT
    try:
        inst = cls("contract probe failure")  # type: ignore[call-arg]
    except TypeError:
        pytest.skip(f"{cls.__name__} needs bespoke constructor arguments")
    payload = inst.to_dict()  # type: ignore[attr-defined]
    assert payload["code"] == code
    assert payload["message"]
    assert payload["hint"]
    assert payload["exit_code"] == ERROR_EXIT[code]


def test_only_retry_signal_may_skip_issuesmith_error_base() -> None:
    """Refs #8129 — RetrySignal is the sole public non-IssuesmithError exception."""
    assert RetrySignal not in _discover_public_error_classes()
    for cls in _discover_public_error_classes():
        assert issubclass(cls, IssuesmithError)
