"""README Output contract stays aligned with ``issuesmith.cli`` (#6780)."""

from __future__ import annotations

import re
from pathlib import Path

from issuesmith.cli_errors import ERROR_EXIT

_REPO_ROOT = Path(__file__).resolve().parents[1]
_README = _REPO_ROOT / "README.md"


def _readme_error_exit_table(readme: str) -> dict[str, int]:
    marker = "## Output contract"
    start = readme.find(marker)
    assert start >= 0, "README missing ## Output contract"
    section = readme[start:]
    next_heading = section.find("\n## ", len(marker))
    if next_heading > 0:
        section = section[:next_heading]
    table_rows = re.findall(
        r"^\|\s*`([A-Z_]+)`\s*\|\s*(\d+)\s*\|",
        section,
        flags=re.MULTILINE,
    )
    assert table_rows, "no error-code rows in Output contract"
    return {code: int(exit_code) for code, exit_code in table_rows}


def test_readme_error_table_matches_cli() -> None:
    """refs #6780: README error codes and exit codes match ``_ERROR_EXIT``."""
    readme = _README.read_text(encoding="utf-8")
    documented = _readme_error_exit_table(readme)
    assert documented == dict(ERROR_EXIT)
