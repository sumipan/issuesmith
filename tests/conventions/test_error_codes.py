"""Error code and stderr contracts for issuesmith runtime modules (#8129)."""

from __future__ import annotations

import ast
import importlib
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

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
@dataclass(frozen=True)
class _ExceptionDef:
    module: str
    name: str
    node: ast.ClassDef

    @property
    def id(self) -> str:
        return f"{self.module}.{self.name}"


def _module_name(path: Path) -> str:
    relative = path.relative_to(SRC.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _discover_public_exception_defs() -> list[_ExceptionDef]:
    """Enumerate definitions with AST, then confirm exception identity at runtime."""
    found: list[_ExceptionDef] = []
    for path in py_files(SRC):
        module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        runtime_module = importlib.import_module(module)
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            if node.name.startswith("_") or node.name == "IssuesmithError":
                continue
            cls = getattr(runtime_module, node.name)
            if not issubclass(cls, BaseException):
                continue
            found.append(_ExceptionDef(module, node.name, node))
    return sorted(found, key=lambda definition: definition.id)


def _declared_string(node: ast.ClassDef, attribute: str) -> str | None:
    for statement in node.body:
        if not isinstance(statement, (ast.Assign, ast.AnnAssign)):
            continue
        targets = statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        if not any(isinstance(target, ast.Name) and target.id == attribute for target in targets):
            continue
        value = statement.value
        return value.value if isinstance(value, ast.Constant) and isinstance(value.value, str) else None
    return None


_PUBLIC_EXCEPTION_DEFS = _discover_public_exception_defs()


def test_no_legacy_stderr_error_prefixes() -> None:
    """Refs #8129 — stderr must not use pre-contract error prefixes."""
    hits: list[str] = []
    for path in py_files(SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "print":
                continue
            writes_stderr = any(
                keyword.arg == "file"
                and isinstance(keyword.value, ast.Attribute)
                and isinstance(keyword.value.value, ast.Name)
                and keyword.value.value.id == "sys"
                and keyword.value.attr == "stderr"
                for keyword in node.keywords
            )
            strings = (
                value.value
                for argument in node.args
                for value in ast.walk(argument)
                if isinstance(value, ast.Constant) and isinstance(value.value, str)
            )
            if writes_stderr and any(marker in text for text in strings for marker in _LEGACY_MARKERS):
                hits.append(f"{path.relative_to(SRC.parent.parent)}:{node.lineno}")
    assert hits == []


@pytest.mark.parametrize("definition", _PUBLIC_EXCEPTION_DEFS, ids=lambda definition: definition.id)
def test_public_exception_contract_is_complete(definition: _ExceptionDef) -> None:
    """Refs #8129 — every public exception has the required base and explicit code."""
    module = importlib.import_module(definition.module)
    cls = getattr(module, definition.name)
    if definition.id == "issuesmith.contract.RetrySignal":
        assert cls is RetrySignal
        assert not issubclass(cls, IssuesmithError)
        return
    assert issubclass(cls, IssuesmithError), f"{cls.__name__} must subclass IssuesmithError"
    assert IssuesmithError in cls.__mro__
    code = _declared_string(definition.node, "code")
    assert code is not None, f"{cls.__name__} must declare a literal class-level code"
    assert _CODE_RE.fullmatch(code)
    assert code in ERROR_EXIT
    inst = cls.__new__(cls)
    IssuesmithError.__init__(
        inst,
        "contract probe failure",
        code=code,
        hint="contract probe hint",
    )
    payload = inst.to_dict()
    assert payload["code"] == code
    assert payload["message"]
    assert payload["hint"]
    assert payload["exit_code"] == ERROR_EXIT[code]


def test_only_retry_signal_may_skip_issuesmith_error_base() -> None:
    """Refs #8129 — RetrySignal is the sole public non-IssuesmithError exception."""
    exceptions = {
        definition.id: getattr(importlib.import_module(definition.module), definition.name)
        for definition in _PUBLIC_EXCEPTION_DEFS
    }
    assert exceptions["issuesmith.contract.RetrySignal"] is RetrySignal
    non_contract = {
        name for name, cls in exceptions.items() if not issubclass(cls, IssuesmithError)
    }
    assert non_contract == {"issuesmith.contract.RetrySignal"}
