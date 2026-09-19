"""Gate: test sources and fixtures must contain zero CJK text (#3385)."""
from __future__ import annotations

import re
from pathlib import Path

# Build the CJK pattern from code points to avoid CJK literal chars in this source file.
_CJK = re.compile(
    "[" + chr(0x3000) + "-" + chr(0x30FF)
    + chr(0x3400) + "-" + chr(0x9FFF)
    + chr(0xF900) + "-" + chr(0xFAFF)
    + chr(0xFF00) + "-" + chr(0xFFEF)
    + chr(0xAC00) + "-" + chr(0xD7AF)
    + "]"
)
_TEXT_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json", ".diff"}


def _find_cjk(text: str) -> bool:
    if _CJK.search(text):
        return True

    slash = chr(92)
    unicode_escape = re.compile(re.escape(slash) + r"([uU])([0-9a-fA-F]{4,8})")
    for match in unicode_escape.finditer(text):
        width = 4 if match.group(1) == "u" else 8
        digits = match.group(2)
        if len(digits) >= width and _CJK.search(chr(int(digits[:width], 16))):
            return True

    byte_run = re.compile(r"(?:" + re.escape(slash) + r"x[0-9a-fA-F]{2})+")
    for match in byte_run.finditer(text):
        raw = bytes.fromhex(match.group(0).replace(slash + "x", ""))
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if _CJK.search(decoded):
            return True
    return False


def test_detector_rejects_literal_and_escaped_cjk() -> None:
    slash = chr(92)
    samples = [
        chr(0x65E5),
        slash + "u" + "65e5",
        slash + "x" + "e6" + slash + "x" + "97" + slash + "x" + "a5",
    ]
    for sample in samples:
        assert _find_cjk(sample)


def test_detector_accepts_ascii_and_non_cjk_escapes() -> None:
    slash = chr(92)
    assert not _find_cjk("plain ASCII")
    assert not _find_cjk(slash + "u" + "0041")
    assert not _find_cjk(slash + "x" + "41")


def test_tests_directory_has_zero_cjk() -> None:
    root = Path(__file__).resolve().parent
    violations: list[str] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in _TEXT_SUFFIXES:
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines, 1):
            if _find_cjk(line):
                rel = path.relative_to(root)
                violations.append(f"{rel}:{i}: {line.strip()[:120]}")
    assert not violations, (
        "CJK characters or encoded CJK text found under tests/:\n"
        + "\n".join(violations)
    )
