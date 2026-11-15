"""Gate: test sources and fixtures must contain zero CJK text (#3385)."""
from __future__ import annotations

import re

# Build the CJK pattern from code points to avoid CJK literal chars in this source file.
_CJK = re.compile(
    "[" + chr(0x3000) + "-" + chr(0x30FF)
    + chr(0x3400) + "-" + chr(0x9FFF)
    + chr(0xF900) + "-" + chr(0xFAFF)
    + chr(0xFF00) + "-" + chr(0xFFEF)
    + chr(0xAC00) + "-" + chr(0xD7AF)
    + "]"
)


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


def test_detector_accepts_ascii_and_non_cjk_escapes() -> None:
    slash = chr(92)
    assert not _find_cjk("plain ASCII")
    assert not _find_cjk(slash + "u" + "0041")
    assert not _find_cjk(slash + "x" + "41")


