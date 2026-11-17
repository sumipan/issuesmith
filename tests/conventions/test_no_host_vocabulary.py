"""Convention test: src/issuesmith must not name the host repository or owner."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_VOCAB = re.compile(r"nexus|sumipan", re.IGNORECASE)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src" / "issuesmith"


def host_vocabulary(source: str) -> list[int]:
    """Return 1-indexed line numbers containing host repository vocabulary."""
    lines: list[int] = []
    for i, line in enumerate(source.splitlines(), 1):
        if _VOCAB.search(line):
            lines.append(i)
    return lines


def _source_files() -> list[Path]:
    return sorted(_SRC.rglob("*.py"))


@pytest.mark.parametrize(
    "path",
    _source_files(),
    ids=lambda p: str(p.relative_to(_SRC)),
)
def test_no_host_vocabulary(path: Path) -> None:
    violations = host_vocabulary(path.read_text(encoding="utf-8"))
    assert violations == [], (
        f"{path.relative_to(_SRC)}: host vocabulary on lines {violations}"
    )


@pytest.mark.parametrize(
    "source",
    [
        "# see sumipan/nexus#3572",
        "# nexus #1234",
    ],
)
def test_host_vocabulary_detects_host_reference(source: str) -> None:
    assert host_vocabulary(source) == [1]
