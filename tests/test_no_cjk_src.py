"""Gate: src/ must contain zero CJK text and no chr()-assembled words (#4347 / #4478)."""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from issuesmith.gates.worktree import line_has_cjk
from tests.test_no_cjk import _find_cjk

_SRC = Path(__file__).resolve().parent.parent / "src"
_SRC_SUFFIXES = {".py", ".md", ".yaml", ".yml", ".json"}

# Word assembly from code points (``map(chr, ...)`` / ``chr(0x...)``) is banned so
# CJK cannot be smuggled back in. Only lines that build CJK range boundaries or
# single-code-point matchers are allowed, pinned by path and stripped line content.
_CHR_ASSEMBLY = re.compile(r"map\(\s*chr\b|chr\(\s*0x", re.IGNORECASE)
_CHR_ALLOWLIST: frozenset[tuple[str, str]] = frozenset({
    ("issuesmith/gates/worktree.py", '+ chr(0x3000) + "-" + chr(0x30FF)'),
    ("issuesmith/gates/worktree.py", '+ chr(0x3400) + "-" + chr(0x9FFF)'),
    ("issuesmith/gates/worktree.py", '+ chr(0xF900) + "-" + chr(0xFAFF)'),
    ("issuesmith/gates/worktree.py", '+ chr(0xFF00) + "-" + chr(0xFFEF)'),
    ("issuesmith/gates/worktree.py", '+ chr(0xAC00) + "-" + chr(0xD7AF)'),
    ("issuesmith/milestone.py", "_IDEOGRAPHIC_SPACE = chr(0x3000)"),
    (
        "issuesmith/milestone.py",
        '"[" + chr(0x3000) + "-" + chr(0x9FFF) + chr(0xFF00) + "-" + chr(0xFFEF) + "]"',
    ),
    ("issuesmith/scope_gate.py", "_FULLWIDTH_PARENS = (chr(0xFF08), chr(0xFF09))"),
})


def _iter_lines(root: Path):
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix not in _SRC_SUFFIXES:
            continue
        if any(part.endswith(".egg-info") or part == "__pycache__" for part in path.parts):
            continue
        rel = path.relative_to(root).as_posix()
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            yield rel, i, line


def cjk_violations(root: Path) -> list[str]:
    return [
        f"{rel}:{i}: {line.strip()[:120]}"
        for rel, i, line in _iter_lines(root)
        if _find_cjk(line) or line_has_cjk(line)
    ]


def chr_assembly_violations(root: Path) -> list[str]:
    return [
        f"{rel}:{i}: {line.strip()[:120]}"
        for rel, i, line in _iter_lines(root)
        if _CHR_ASSEMBLY.search(line) and (rel, line.strip()) not in _CHR_ALLOWLIST
    ]


def test_src_has_zero_cjk() -> None:
    violations = cjk_violations(_SRC)
    assert not violations, (
        "CJK characters or encoded CJK text found under src/:\n" + "\n".join(violations)
    )


def test_src_has_no_chr_word_assembly() -> None:
    violations = chr_assembly_violations(_SRC)
    assert not violations, (
        "map(chr / chr(0x word assembly found under src/ (outside the allowlist):\n"
        + "\n".join(violations)
    )


def test_chr_allowlist_entries_still_exist() -> None:
    present = {
        (rel, line.strip()) for rel, _, line in _iter_lines(_SRC) if _CHR_ASSEMBLY.search(line)
    }
    stale = sorted(_CHR_ALLOWLIST - present)
    assert not stale, f"stale allowlist entries: {stale}"


def _src_copy(tmp_path: Path) -> Path:
    root = tmp_path / "src"
    shutil.copytree(_SRC, root, ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"))
    return root


def test_detector_fails_on_injected_cjk(tmp_path: Path) -> None:
    root = _src_copy(tmp_path)
    assert cjk_violations(root) == []
    (root / "issuesmith" / "injected.py").write_text(
        'MESSAGE = "' + chr(0x65E5) + '"\n', encoding="utf-8"
    )
    assert cjk_violations(root) == ["issuesmith/injected.py:1: MESSAGE = \"" + chr(0x65E5) + '"']


def test_detector_fails_on_injected_escaped_cjk(tmp_path: Path) -> None:
    root = _src_copy(tmp_path)
    slash = chr(92)
    (root / "issuesmith" / "injected.md").write_text(slash + "u65e5\n", encoding="utf-8")
    assert len(cjk_violations(root)) == 1


def test_detector_fails_on_injected_chr_assembly(tmp_path: Path) -> None:
    root = _src_copy(tmp_path)
    assert chr_assembly_violations(root) == []
    (root / "issuesmith" / "injected.py").write_text(
        'WORD = "".join(map(chr, (0x65E5, 0x672C)))\nCH = chr(0x65E5)\n', encoding="utf-8"
    )
    assert len(chr_assembly_violations(root)) == 2
