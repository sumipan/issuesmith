"""Gate: specific cleaned test files must contain zero CJK characters (#3385).

This is a strict (no-marker) companion to test_english_only.py covering files
that have been fully migrated away from Japanese fixture data.  Files still
under migration are covered by the marker-based gate in test_english_only.py.
"""
from __future__ import annotations

import re
from pathlib import Path

# Build the CJK pattern from code points to avoid CJK literal chars in this source file.
_CJK = re.compile(
    "[" + chr(0x3040) + "-" + chr(0x30FF)
    + chr(0x3400) + "-" + chr(0x9FFF)
    + chr(0xFF00) + "-" + chr(0xFFEF)
    + chr(0xAC00) + "-" + chr(0xD7AF)
    + "]"
)

_CHECKED_FILES = [
    "steps/test_m1_merge.py",
    "test_body_editor_shim.py",
    "gate_rules/test_cp1_gate.py",
]


def test_cleaned_test_files_have_zero_cjk() -> None:
    root = Path(__file__).resolve().parent
    violations: list[str] = []
    for rel in _CHECKED_FILES:
        path = root / rel
        if not path.is_file():
            violations.append(f"{rel}: file not found")
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            if _CJK.search(line):
                violations.append(f"{rel}:{i}: {line.strip()[:120]}")
    assert not violations, (
        "CJK characters found in files that should be fully migrated:\n"
        + "\n".join(violations)
    )
