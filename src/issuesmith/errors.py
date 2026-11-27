"""Shared exception base for issuesmith runtime failures."""

from __future__ import annotations

from ghdag import GhdagError

from issuesmith.cli_errors import ERROR_EXIT


class IssuesmithError(GhdagError):
    """Failure with a CLI error code from :data:`ERROR_EXIT`."""

    code = "FAILED"

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        hint: str | None = None,
    ) -> None:
        resolved = code if code is not None else type(self).code
        if resolved not in ERROR_EXIT:
            raise ValueError(f"unknown error code: {resolved!r}")
        super().__init__(message, code=resolved, hint=hint)
        self._exit_code = ERROR_EXIT[resolved]

    @property
    def exit_code(self) -> int:
        return self._exit_code

    def to_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "exit_code": self.exit_code,
        }
