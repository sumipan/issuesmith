"""Shared CLI usage-error contract for issuesmith command modules."""

from __future__ import annotations

import argparse
import sys
from typing import Any

ERROR_EXIT: dict[str, int] = {
    "UNKNOWN_COMMAND": 2,
    "UNKNOWN_ARGUMENT": 2,
    "MISSING_ARGUMENT": 2,
    "INVALID_VALUE": 2,
    "NOT_FOUND": 1,
    "CONFIG_ERROR": 1,
    "FAILED": 1,
    "CONFLICT": 1,
}


def fail(code: str, message: str, hint: str | None = None) -> int:
    print(f"error[{code}]: {message}", file=sys.stderr)
    if hint:
        print(f"hint: {hint}", file=sys.stderr)
    return ERROR_EXIT[code]


def _classify_error(message: str, subcommand_dests: frozenset[str]) -> str:
    lower = message.lower()
    if "unrecognized arguments" in lower or "unrecognized option" in lower:
        return "UNKNOWN_ARGUMENT"
    if "the following arguments are required" in lower or "required:" in lower:
        return "MISSING_ARGUMENT"
    for dest in subcommand_dests:
        if message.startswith(f"argument {dest}:") and "invalid choice" in lower:
            return "UNKNOWN_COMMAND"
    if message.startswith("invalid choice:"):
        return "UNKNOWN_COMMAND"
    if "invalid int value" in lower or "invalid choice" in lower:
        return "INVALID_VALUE"
    return "INVALID_VALUE"


class ContractParser(argparse.ArgumentParser):
    def __init__(self, *args: Any, command: str = "", **kwargs: Any) -> None:
        self._subcommand_dests: set[str] = set()
        if "prog" not in kwargs and command:
            kwargs["prog"] = f"issuesmith {command}"
        super().__init__(*args, **kwargs)

    def add_subparsers(self, *args: Any, **kwargs: Any) -> argparse._SubParsersAction[Any]:
        dest = kwargs.get("dest")
        if dest:
            self._subcommand_dests.add(str(dest))
        kwargs.setdefault("parser_class", type(self))
        action = super().add_subparsers(*args, **kwargs)
        return action

    def error(self, message: str) -> None:  # noqa: ARG002 — argparse API
        code = _classify_error(message, frozenset(self._subcommand_dests))
        rc = fail(code, message, self.format_usage())
        raise SystemExit(rc)
